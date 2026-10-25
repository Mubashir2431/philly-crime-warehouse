"""Load: write cleaned batches into the SQLite star schema.

Clean rows are appended to ``stg_incident`` one monthly batch at a time, so the
whole extract never has to sit in memory. Once every batch is staged, a single
transaction upserts the dimensions, deletes the fact rows for the load window,
and reinserts them from staging. If any step fails the transaction rolls back
and the warehouse is left exactly as it was.
"""

from __future__ import annotations

import logging
import sqlite3

import pandas as pd

from .db import (
    SQL_DIR,
    insert_dataframe,
    run_sql_file,
    run_sql_params,
    scalar,
    upsert_dataframe,
    view_files,
)

log = logging.getLogger(__name__)

STAGING_COLUMNS = [
    "source_id",
    "dc_key",
    "dispatch_ts",
    "date_key",
    "hour",
    "district_code",
    "psa",
    "ucr_code",
    "offense_name",
    "location_block",
    "lat",
    "lon",
    "grid_id",
]


def window_keys(start_year: int, end_year: int) -> tuple[int, int]:
    return start_year * 10000 + 101, end_year * 10000 + 1231


def create_schema(conn: sqlite3.Connection) -> None:
    run_sql_file(conn, SQL_DIR / "schema.sql")
    log.info("schema ready")


def create_views(conn: sqlite3.Connection) -> None:
    for path in view_files():
        run_sql_file(conn, path)
    log.info("created %d views", len(view_files()))


def reset_staging(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM stg_incident")


def stage_batch(conn: sqlite3.Connection, clean: pd.DataFrame) -> int:
    """Append one cleaned batch to staging in its own transaction."""
    conn.execute("BEGIN")
    try:
        n = insert_dataframe(conn, clean[STAGING_COLUMNS], "stg_incident")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return n


def staged_duplicates(conn: sqlite3.Connection) -> pd.DataFrame:
    """Staged rows that repeat a complaint + offense already staged from another batch.

    Mirrors the ROW_NUMBER() rule in 02_fact_incident.sql so the rejected rows
    can be written to the rejects file with a reason.
    """
    sql = """
        WITH ranked AS (
            SELECT source_id, dc_key, dispatch_ts, ucr_code, offense_name,
                   row_number() OVER (
                       PARTITION BY dc_key, ucr_code, offense_name ORDER BY source_id DESC
                   ) AS copy_rank
            FROM stg_incident
        )
        SELECT source_id, dc_key, dispatch_ts, ucr_code, offense_name
        FROM ranked WHERE copy_rank > 1
    """
    return pd.read_sql(sql, conn)


def district_centers(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT district_code, avg(lat) AS lat, avg(lon) AS lon "
        "FROM stg_incident GROUP BY district_code",
        conn,
    )


def load_warehouse(
    conn: sqlite3.Connection,
    offense_dim: pd.DataFrame,
    grid_dim: pd.DataFrame,
    district_dim: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> int:
    """Upsert dimensions and reload the fact window from staging. Returns fact rows."""
    start_key, end_key = window_keys(start_year, end_year)
    conn.execute("BEGIN")
    try:
        run_sql_params(
            conn,
            SQL_DIR / "load" / "01_dim_date_hour.sql",
            {"start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31"},
        )
        upsert_dataframe(conn, district_dim, "dim_district", ["district_code"])
        upsert_dataframe(conn, offense_dim, "dim_offense", ["ucr_code", "offense_name"])
        upsert_dataframe(conn, grid_dim, "dim_grid", ["grid_id"])
        run_sql_params(
            conn,
            SQL_DIR / "load" / "02_fact_incident.sql",
            {"start_key": start_key, "end_key": end_key},
        )
        n_fact = scalar(
            conn,
            "SELECT count(*) FROM fact_incident WHERE date_key BETWEEN ? AND ?",
            (start_key, end_key),
        )
        conn.execute("DELETE FROM stg_incident")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    log.info(
        "loaded fact_incident: %d rows; dims: %d offenses, %d districts, %d grid cells",
        n_fact,
        len(offense_dim),
        len(district_dim),
        len(grid_dim),
    )
    return int(n_fact)
