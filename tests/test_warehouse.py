"""Data-quality tests on a built warehouse (fixture build by default, see conftest.py)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from phl_crime.config import Settings
from phl_crime.db import connect, scalar, view_files
from phl_crime.export import export_dashboard_data
from phl_crime.load import window_keys
from phl_crime.quality import run_checks
from phl_crime.transform import PHILLY_BOUNDS

SETTINGS = Settings()


def latest_run(conn) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM etl_run_log WHERE status = 'success' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    conn.row_factory = None
    assert row is not None, "no successful ETL run logged"
    return row


def test_fact_count_matches_etl_log(conn):
    run = latest_run(conn)
    assert scalar(conn, "SELECT count(*) FROM fact_incident") == run["rows_loaded"]


def test_every_extracted_row_is_loaded_or_rejected(conn):
    run = latest_run(conn)
    assert run["rows_extracted"] == run["rows_loaded"] + run["rows_rejected"]
    by_reason = scalar(
        conn, "SELECT sum(n_rows) FROM dq_reject_summary WHERE run_id = ?", (run["run_id"],)
    )
    assert by_reason == run["rows_rejected"]
    details = json.loads(run["details"])
    assert sum(details["rejected_by_reason"].values()) == run["rows_rejected"]


def test_foreign_keys_are_enforced_and_clean(conn):
    assert scalar(conn, "PRAGMA foreign_keys") == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("dim", "key"),
    [
        ("dim_date", "date_key"),
        ("dim_hour", "hour"),
        ("dim_offense", "offense_key"),
        ("dim_district", "district_code"),
    ],
)
def test_no_orphan_dimension_keys(conn, dim, key):
    orphans = scalar(
        conn,
        f"SELECT count(*) FROM fact_incident f LEFT JOIN {dim} d ON d.{key} = f.{key} "
        f"WHERE d.{key} IS NULL",
    )
    assert orphans == 0


def test_orphan_insert_is_rejected_by_the_database(built_db):
    c = connect(built_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO fact_incident (source_id, dc_key, date_key, hour, offense_key, "
                "district_code, dispatch_ts) VALUES (-1, 'x', 19000101, 0, 1, '01', '1900-01-01')"
            )
    finally:
        c.close()


def test_no_duplicate_incident_ids(conn):
    assert scalar(conn, "SELECT count(*) - count(DISTINCT source_id) FROM fact_incident") == 0
    dupes = scalar(
        conn,
        "SELECT count(*) FROM (SELECT 1 FROM fact_incident "
        "GROUP BY dc_key, offense_key HAVING count(*) > 1)",
    )
    assert dupes == 0


def test_dates_inside_window_and_calendar_is_complete(conn):
    start_key, end_key = window_keys(SETTINGS.start_year, SETTINGS.end_year)
    lo, hi = conn.execute("SELECT min(date_key), max(date_key) FROM fact_incident").fetchone()
    assert start_key <= lo <= hi <= end_key
    days = scalar(
        conn, "SELECT count(*) FROM dim_date WHERE date_key BETWEEN ? AND ?", (start_key, end_key)
    )
    span = scalar(
        conn, "SELECT julianday(max(full_date)) - julianday(min(full_date)) + 1 FROM dim_date"
    )
    assert days == span


def test_coordinates_in_bounds(conn):
    b = PHILLY_BOUNDS
    lat_lo, lat_hi, lon_lo, lon_hi = conn.execute(
        "SELECT min(lat), max(lat), min(lon), max(lon) FROM fact_incident"
    ).fetchone()
    assert b["lat_min"] <= lat_lo and lat_hi <= b["lat_max"]
    assert b["lon_min"] <= lon_lo and lon_hi <= b["lon_max"]
    mapped = scalar(conn, "SELECT avg(lat IS NOT NULL) FROM fact_incident")
    assert mapped > 0.95


def test_all_quality_checks_pass(conn):
    checks = run_checks(conn, SETTINGS.start_year, SETTINGS.end_year)
    failed = [c.name for c in checks if not c.passed]
    assert failed == []


def test_every_view_runs(conn):
    for path in view_files():
        name = path.read_text(encoding="utf-8").split("CREATE VIEW ", 1)[1].split()[0]
        conn.execute(f"SELECT * FROM {name} LIMIT 5").fetchall()


def test_monthly_trend_window_math(conn):
    rows = conn.execute(
        "SELECT incidents, rolling_3mo_avg FROM v_monthly_trend ORDER BY month_start LIMIT 3"
    ).fetchall()
    assert rows[2][1] == pytest.approx(round(sum(r[0] for r in rows) / 3, 1))


def test_district_shares_sum_to_100(conn):
    for (total,) in conn.execute("SELECT sum(pct_of_city) FROM v_district_share GROUP BY year"):
        assert total == pytest.approx(100, abs=0.2)


def test_fixture_rejects_exact_counts(conn, is_fixture_db):
    if not is_fixture_db:
        pytest.skip("exact counts only apply to the committed fixture")
    run = latest_run(conn)
    reasons = dict(
        conn.execute(
            "SELECT reason, n_rows FROM dq_reject_summary WHERE run_id = ?", (run["run_id"],)
        ).fetchall()
    )
    assert reasons == {
        "date_out_of_range": 1,
        "duplicate_dc_key_offense": 1,
        "invalid_dispatch_date": 1,
        "invalid_district": 2,
        "unknown_ucr_code": 1,
    }
    assert run["rows_extracted"] == 3008 and run["rows_loaded"] == 3002


def test_reload_is_idempotent(tmp_path):
    from conftest import build_fixture_db

    db = build_fixture_db(tmp_path)
    build_fixture_db(tmp_path)
    c = connect(db)
    assert scalar(c, "SELECT count(*) FROM fact_incident") == 3002
    assert scalar(c, "SELECT count(*) FROM etl_run_log WHERE status = 'success'") == 2
    c.close()


def test_duplicates_across_batches_are_removed_in_sql(tmp_path):
    """With small batches the fixture's duplicate pair lands in different batches."""
    from conftest import build_fixture_db

    db = build_fixture_db(tmp_path, "--chunk-rows", "1000")
    c = connect(db)
    details = json.loads(scalar(c, "SELECT details FROM etl_run_log"))
    assert details["batches"] == 4
    assert scalar(c, "SELECT count(*) FROM fact_incident") == 3002
    assert details["rejected_by_reason"]["duplicate_dc_key_offense"] == 1
    rejects = (tmp_path / "processed" / "rejects.csv").read_text(encoding="utf-8")
    assert rejects.count("duplicate_dc_key_offense") == 1
    c.close()


def test_dashboard_export_is_small(conn, tmp_path):
    export_dashboard_data(conn, out_dir=tmp_path, run_summary={"ok": True})
    files = list(tmp_path.glob("*.parquet"))
    assert {f.stem for f in files} == {"monthly", "heatmap", "grid", "hotspots", "districts"}
    assert sum(f.stat().st_size for f in files) < 5_000_000
