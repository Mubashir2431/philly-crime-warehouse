"""SQLite helpers: connections with foreign keys on, SQL file execution, bulk inserts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT

SQL_DIR = REPO_ROOT / "sql"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the warehouse in autocommit mode; callers manage transactions with BEGIN/COMMIT.

    Foreign keys are off by default in SQLite, so every connection turns them on.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def run_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    """Run a file that may hold many statements."""
    conn.executescript(path.read_text(encoding="utf-8"))


def run_sql_params(conn: sqlite3.Connection, path: Path, params: dict) -> None:
    """Run a multi-statement file with named parameters, one statement at a time.

    executescript() cannot bind parameters, so the file is split on semicolons
    at line ends. The project's SQL files keep semicolons out of string literals.
    """
    text = path.read_text(encoding="utf-8")
    for stmt in text.split(";\n"):
        body = "\n".join(
            line for line in stmt.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            names = {k: v for k, v in params.items() if f":{k}" in body}
            conn.execute(body, names)


def _py(value):
    """numpy / pandas scalars -> plain Python values sqlite3 understands."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value.item() if hasattr(value, "item") else value


def _rows(df: pd.DataFrame):
    return ([_py(v) for v in row] for row in df.itertuples(index=False, name=None))


def insert_dataframe(conn: sqlite3.Connection, df: pd.DataFrame, table: str) -> int:
    """Bulk insert with executemany. The caller owns the transaction."""
    if df.empty:
        return 0
    cols = list(df.columns)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.executemany(sql, _rows(df))
    return len(df)


def upsert_dataframe(
    conn: sqlite3.Connection, df: pd.DataFrame, table: str, key: list[str]
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, so surrogate keys stay stable across reloads."""
    if df.empty:
        return
    cols = list(df.columns)
    updates = [c for c in cols if c not in key]
    action = (
        "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in updates)
        if updates
        else "DO NOTHING"
    )
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(key)}) {action}"
    )
    conn.executemany(sql, _rows(df))


def scalar(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()):
    return conn.execute(sql, params).fetchone()[0]


def view_files() -> list[Path]:
    return sorted((SQL_DIR / "views").glob("*.sql"))
