"""Command line entry point: ``python -m phl_crime.pipeline <command>``.

Commands
    run         extract (unless --raw-file is given), transform, load, check, export
    extract     download and cache raw data only
    check       run data-quality checks against the current warehouse
    analyze     run every analysis view and write the results to CSV
    export-bi   write flat CSVs for Tableau / Power BI to exports/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .analysis import run_analysis
from .config import SOURCE_COLUMNS, Settings
from .db import connect, scalar
from .districts import build_district_dim
from .export import export_bi_csv, export_dashboard_data
from .extract import extract_district_boundaries, extract_incidents, source_row_count
from .load import (
    create_schema,
    create_views,
    district_centers,
    load_warehouse,
    reset_staging,
    stage_batch,
    staged_duplicates,
)
from .quality import run_checks, write_report
from .transform import build_grid_dim, build_offense_dim, transform

log = logging.getLogger("phl_crime")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def iter_raw_batches(paths: list[Path], chunk_rows: int = 100_000) -> Iterator[pd.DataFrame]:
    """Yield raw rows in bounded batches: one month file at a time, or chunks of a big file."""
    for path in paths:
        if path.suffix == ".parquet":
            for batch in pq.ParquetFile(path).iter_batches(batch_size=chunk_rows):
                yield batch.to_pandas().astype("string").fillna("")
        else:
            yield from pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=chunk_rows)


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    db_path = Path(args.db) if args.db else settings.db_path
    conn = connect(db_path)
    create_schema(conn)
    cur = conn.execute(
        "INSERT INTO etl_run_log (started_at, start_year, end_year) VALUES (?, ?, ?)",
        (_now(), settings.start_year, settings.end_year),
    )
    run_id = cur.lastrowid
    processed = Path(args.out_dir) if args.out_dir else settings.processed_dir
    processed.mkdir(parents=True, exist_ok=True)

    try:
        # --- extract ------------------------------------------------------
        t = time.perf_counter()
        source_count = None
        if args.raw_file:
            raw_paths, geo_path = [Path(args.raw_file)], None
        else:
            raw_paths = extract_incidents(settings, force=args.force_extract)
            geo_path = extract_district_boundaries(settings)
            if args.check_source:
                try:
                    source_count = source_row_count(settings)
                except Exception as exc:  # network is optional for reconciliation
                    log.warning("could not fetch source row count: %s", exc)
        timings["extract_s"] = time.perf_counter() - t

        # --- transform + stage, one batch at a time -------------------------
        t = time.perf_counter()
        reset_staging(conn)
        stats: Counter[str] = Counter()
        offense_pairs, grid_ids = [], set()
        rejects_path = processed / "rejects.csv"
        rejects_path.unlink(missing_ok=True)
        n_batches = 0
        for raw in iter_raw_batches(raw_paths, args.chunk_rows):
            result = transform(raw, settings.start_year, settings.end_year)
            stats.update(result.stats)
            stage_batch(conn, result.clean)
            offense_pairs.append(result.clean[["ucr_code", "offense_name"]].drop_duplicates())
            grid_ids.update(result.clean["grid_id"].dropna().astype(int).tolist())
            if len(result.rejects):
                result.rejects.to_csv(
                    rejects_path, mode="a", header=not rejects_path.exists(), index=False
                )
            n_batches += 1

        # Duplicates that span two monthly batches are removed in SQL at load
        # time; record them here so every input row still has a fate.
        cross = staged_duplicates(conn)
        if len(cross):
            pd.DataFrame(
                {
                    "objectid": cross["source_id"],
                    "dc_key": cross["dc_key"],
                    "dispatch_date": cross["dispatch_ts"].str[:10],
                    "ucr_general": cross["ucr_code"],
                    "text_general_code": cross["offense_name"],
                    "reject_reason": "duplicate_dc_key_offense",
                }
            ).reindex(columns=[*SOURCE_COLUMNS, "reject_reason"]).to_csv(
                rejects_path, mode="a", header=not rejects_path.exists(), index=False
            )
        stats["rejected_duplicate_dc_key_offense"] += len(cross)
        stats["rows_rejected"] += len(cross)
        stats["rows_out"] -= len(cross)
        stats["cross_batch_duplicates"] = len(cross)

        offense_dim = build_offense_dim(pd.concat(offense_pairs, ignore_index=True))
        grid_dim = build_grid_dim(pd.DataFrame({"grid_id": pd.Series(sorted(grid_ids))}))
        district_dim = build_district_dim(geo_path, district_centers(conn))
        timings["transform_s"] = time.perf_counter() - t
        log.info("transformed %d batches: %s", n_batches, dict(stats))

        # --- load ---------------------------------------------------------
        t = time.perf_counter()
        n_loaded = load_warehouse(
            conn, offense_dim, grid_dim, district_dim, settings.start_year, settings.end_year
        )
        create_views(conn)
        timings["load_s"] = time.perf_counter() - t

        # --- quality ------------------------------------------------------
        t = time.perf_counter()
        checks = run_checks(conn, settings.start_year, settings.end_year, stats["rows_out"])
        timings["checks_s"] = time.perf_counter() - t
        passed = all(c.passed for c in checks) and (
            stats["rows_in"] == n_loaded + stats["rows_rejected"]
        )

        reasons = {
            k.removeprefix("rejected_"): v
            for k, v in sorted(stats.items())
            if k.startswith("rejected_")
        }
        summary = {
            "run_id": run_id,
            "window": f"{settings.start_year}-{settings.end_year}",
            "source_row_count": source_count,
            "rows_extracted": stats["rows_in"],
            "rows_rejected": stats["rows_rejected"],
            "rows_loaded": n_loaded,
            "rejected_by_reason": reasons,
            "coords_nulled": stats["coords_nulled"],
            "coords_missing": stats["coords_missing"],
            "coords_out_of_bounds": stats["coords_out_of_bounds"],
            "source_hour_missing": stats["source_hour_missing"],
            "source_hour_mismatch": stats["source_hour_mismatch"],
            "batches": n_batches,
            "dim_rows": {
                "dim_date": scalar(conn, "SELECT count(*) FROM dim_date"),
                "dim_hour": scalar(conn, "SELECT count(*) FROM dim_hour"),
                "dim_offense": scalar(conn, "SELECT count(*) FROM dim_offense"),
                "dim_district": scalar(conn, "SELECT count(*) FROM dim_district"),
                "dim_grid": scalar(conn, "SELECT count(*) FROM dim_grid"),
            },
            "all_checks_passed": passed,
        }

        # --- exports ------------------------------------------------------
        t = time.perf_counter()
        if not args.no_dashboard_export:
            timings["export_s"] = 0.0
            timings["total_s"] = time.perf_counter() - t0
            export_dashboard_data(
                conn,
                geojson=geo_path,
                run_summary={**summary, "timings_s": _round(timings)},
            )
            timings["export_s"] = time.perf_counter() - t
        timings["total_s"] = time.perf_counter() - t0
        summary["timings_s"] = _round(timings)
        summary["finished_at"] = _now()

        write_report(processed / "dq_report.json", checks, summary)
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE etl_run_log SET finished_at = ?, rows_extracted = ?, rows_rejected = ?, "
            "rows_loaded = ?, dq_passed = ?, status = ?, details = ? WHERE run_id = ?",
            (
                summary["finished_at"],
                stats["rows_in"],
                stats["rows_rejected"],
                n_loaded,
                int(passed),
                "success" if passed else "dq_failed",
                json.dumps(summary),
                run_id,
            ),
        )
        conn.executemany(
            "INSERT INTO dq_reject_summary (run_id, reason, n_rows) VALUES (?, ?, ?)",
            [(run_id, r, n) for r, n in reasons.items()],
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.execute(
            "UPDATE etl_run_log SET finished_at = ?, status = 'error' WHERE run_id = ?",
            (_now(), run_id),
        )
        raise
    finally:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    log.info("run %d finished: %s", run_id, json.dumps(summary))
    conn.close()
    return 0 if passed else 1


def _round(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 1) for k, v in d.items()}


def _db(settings: Settings, args: argparse.Namespace) -> Path:
    return Path(args.db) if args.db else settings.db_path


def cmd_extract(settings: Settings, args: argparse.Namespace) -> int:
    extract_incidents(settings, force=args.force_extract)
    extract_district_boundaries(settings)
    return 0


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    checks = run_checks(connect(_db(settings, args)), settings.start_year, settings.end_year)
    return 0 if all(c.passed for c in checks) else 1


def cmd_analyze(settings: Settings, args: argparse.Namespace) -> int:
    conn = connect(_db(settings, args))
    if args.out_dir:
        run_analysis(conn, out_dir=Path(args.out_dir))
    else:
        run_analysis(conn)
    return 0


def cmd_export_bi(settings: Settings, args: argparse.Namespace) -> int:
    export_bi_csv(connect(_db(settings, args)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phl_crime", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["run", "extract", "check", "analyze", "export-bi"])
    parser.add_argument("--db", help="SQLite file (default: data/warehouse.db or $PHL_DB)")
    parser.add_argument("--raw-file", help="skip the API and load this CSV/Parquet instead")
    parser.add_argument(
        "--out-dir", help="output folder for run (rejects, DQ report) or analyze (CSVs)"
    )
    parser.add_argument(
        "--chunk-rows",
        type=int,
        default=100_000,
        help="max raw rows per transform batch (bounds memory)",
    )
    parser.add_argument("--force-extract", action="store_true", help="ignore cached raw files")
    parser.add_argument(
        "--check-source",
        action="store_true",
        help="ask the API for its row count to reconcile against",
    )
    parser.add_argument(
        "--no-dashboard-export",
        action="store_true",
        help="do not overwrite dashboard/data (useful for fixture runs)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )
    settings = Settings()
    handlers = {
        "run": cmd_run,
        "extract": cmd_extract,
        "check": cmd_check,
        "analyze": cmd_analyze,
        "export-bi": cmd_export_bi,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
