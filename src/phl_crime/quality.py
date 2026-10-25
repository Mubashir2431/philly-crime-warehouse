"""Data-quality checks that run against the loaded warehouse.

Each check is a SQL query that returns a single number. A check passes when
that number matches its expectation (usually zero bad rows). The same checks
back the pytest warehouse tests and the JSON report written after every ETL run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .db import scalar
from .load import window_keys
from .transform import PHILLY_BOUNDS

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    description: str
    value: int
    expected: int
    passed: bool


def zero_checks(start_key: int, end_key: int) -> list[tuple[str, str, str]]:
    """(name, description, SQL returning a count that must be zero)."""
    b = PHILLY_BOUNDS
    window = f"date_key BETWEEN {start_key} AND {end_key}"
    return [
        (
            "foreign_key_violations",
            "rows reported by PRAGMA foreign_key_check (any table)",
            "SELECT count(*) FROM pragma_foreign_key_check",
        ),
        (
            "orphan_date_keys",
            "fact rows whose date_key has no dim_date row",
            "SELECT count(*) FROM fact_incident f LEFT JOIN dim_date d USING (date_key) "
            "WHERE d.date_key IS NULL",
        ),
        (
            "orphan_hours",
            "fact rows whose hour has no dim_hour row",
            "SELECT count(*) FROM fact_incident f LEFT JOIN dim_hour h USING (hour) "
            "WHERE h.hour IS NULL",
        ),
        (
            "orphan_offense_keys",
            "fact rows whose offense_key has no dim_offense row",
            "SELECT count(*) FROM fact_incident f LEFT JOIN dim_offense o USING (offense_key) "
            "WHERE o.offense_key IS NULL",
        ),
        (
            "orphan_district_codes",
            "fact rows whose district_code has no dim_district row",
            "SELECT count(*) FROM fact_incident f LEFT JOIN dim_district d USING (district_code) "
            "WHERE d.district_code IS NULL",
        ),
        (
            "orphan_grid_ids",
            "fact rows with a grid_id missing from dim_grid",
            "SELECT count(*) FROM fact_incident f LEFT JOIN dim_grid g USING (grid_id) "
            "WHERE f.grid_id IS NOT NULL AND g.grid_id IS NULL",
        ),
        (
            "duplicate_source_ids",
            "source rows loaded more than once",
            "SELECT count(*) FROM (SELECT source_id FROM fact_incident "
            "GROUP BY source_id HAVING count(*) > 1)",
        ),
        (
            "duplicate_dc_key_offense",
            "same complaint number and offense loaded more than once",
            "SELECT count(*) FROM (SELECT dc_key, offense_key FROM fact_incident "
            "GROUP BY dc_key, offense_key HAVING count(*) > 1)",
        ),
        (
            "dates_outside_window",
            "fact rows dated outside the configured load window",
            f"SELECT count(*) FROM fact_incident WHERE NOT ({window})",
        ),
        (
            "future_dates",
            "fact rows dated after today",
            "SELECT count(*) FROM fact_incident f JOIN dim_date d USING (date_key) "
            "WHERE d.full_date > date('now')",
        ),
        (
            "date_key_mismatch",
            "date_key or hour that does not match the dispatch timestamp",
            "SELECT count(*) FROM fact_incident "
            "WHERE date_key <> CAST(strftime('%Y%m%d', dispatch_ts) AS INTEGER) "
            "OR hour <> CAST(strftime('%H', dispatch_ts) AS INTEGER)",
        ),
        (
            "coords_out_of_bounds",
            "coordinates outside the Philadelphia bounding box",
            "SELECT count(*) FROM fact_incident WHERE lat IS NOT NULL AND NOT ("
            f"lat BETWEEN {b['lat_min']} AND {b['lat_max']} "
            f"AND lon BETWEEN {b['lon_min']} AND {b['lon_max']})",
        ),
        (
            "half_null_coords",
            "rows with only one of lat / lon set",
            "SELECT count(*) FROM fact_incident WHERE (lat IS NULL) <> (lon IS NULL)",
        ),
        (
            "empty_months",
            "calendar months in the window with zero incidents",
            "SELECT count(*) FROM (SELECT DISTINCT month_start FROM dim_date "
            f"WHERE {window}) m WHERE NOT EXISTS (SELECT 1 FROM fact_incident f "
            "JOIN dim_date d USING (date_key) WHERE d.month_start = m.month_start)",
        ),
        (
            "anomalous_low_months",
            "months with fewer than half the median monthly volume",
            # SQLite has no median(), so the middle row is picked with ROW_NUMBER().
            "WITH m AS (SELECT d.month_start, count(*) AS n FROM fact_incident f "
            f"JOIN dim_date d USING (date_key) WHERE f.{window} GROUP BY d.month_start), "
            "o AS (SELECT n, row_number() OVER (ORDER BY n) AS rn, count(*) OVER () AS c FROM m), "
            "med AS (SELECT avg(n) AS v FROM o WHERE rn IN ((c + 1) / 2, (c + 2) / 2)) "
            "SELECT count(*) FROM m, med WHERE m.n < 0.5 * med.v",
        ),
    ]


def run_checks(
    conn: sqlite3.Connection,
    start_year: int,
    end_year: int,
    expected_rows: int | None = None,
    min_rows_for_volume_checks: int = 50_000,
) -> list[CheckResult]:
    start_key, end_key = window_keys(start_year, end_year)
    # Small fixture samples are too sparse for the monthly-volume checks.
    big_enough = scalar(conn, "SELECT count(*) FROM fact_incident") >= min_rows_for_volume_checks
    results: list[CheckResult] = []
    for name, desc, sql in zero_checks(start_key, end_key):
        if name in {"empty_months", "anomalous_low_months"} and not big_enough:
            continue
        value = int(scalar(conn, sql))
        results.append(CheckResult(name, desc, value, 0, value == 0))

    if expected_rows is not None:
        loaded = int(
            scalar(
                conn,
                "SELECT count(*) FROM fact_incident WHERE date_key BETWEEN ? AND ?",
                (start_key, end_key),
            )
        )
        results.append(
            CheckResult(
                "row_count_reconciles",
                "fact rows in window equal rows that passed every transform and dedup rule",
                loaded,
                expected_rows,
                loaded == expected_rows,
            )
        )
    for r in results:
        log.log(
            logging.INFO if r.passed else logging.ERROR,
            "DQ %-24s %s (value=%d)",
            r.name,
            "PASS" if r.passed else "FAIL",
            r.value,
        )
    return results


def write_report(path: Path, checks: list[CheckResult], summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        **summary,
        "checks": [asdict(c) for c in checks],
        "all_checks_passed": all(c.passed for c in checks),
    }
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    log.info("data-quality report -> %s", path)
