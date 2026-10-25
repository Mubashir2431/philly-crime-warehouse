"""Run every analysis view in sql/views and save each result as a CSV."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT
from .db import view_files

log = logging.getLogger(__name__)

RESULTS_DIR = REPO_ROOT / "analysis" / "results"

# v_incident is the wide join used for exports, not an analysis result.
SKIP = {"v_incident"}


def view_name(path: Path) -> str:
    match = re.search(r"CREATE VIEW (\w+)", path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"no CREATE VIEW in {path}")
    return match.group(1)


def view_question(path: Path) -> str:
    """The '-- Question:' comment block at the top of a view file, as one line."""
    lines, grab = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("-- Question:"):
            grab = True
            line = line.removeprefix("-- Question:")
        elif grab and (not line.startswith("--") or line.startswith("-- Techniques")):
            break
        if grab:
            lines.append(line.removeprefix("--").strip())
    return " ".join(lines).strip()


def run_analysis(conn: sqlite3.Connection, out_dir: Path = RESULTS_DIR) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for path in view_files():
        name = view_name(path)
        if name in SKIP:
            continue
        df = pd.read_sql(f"SELECT * FROM {name}", conn)
        df.to_csv(out_dir / f"{name}.csv", index=False)
        counts[name] = len(df)
        log.info("%-28s %6d rows  %s", name, len(df), view_question(path)[:70])
    return counts
