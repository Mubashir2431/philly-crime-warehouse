"""Exports: small aggregates for the Streamlit dashboard, and a flat CSV for BI tools.

The dashboard reads only the files written by ``export_dashboard_data`` so it
can run on Streamlit Community Cloud with no database behind it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT

log = logging.getLogger(__name__)

DASHBOARD_DATA = REPO_ROOT / "dashboard" / "data"
EXPORTS_DIR = REPO_ROOT / "exports"

DASHBOARD_QUERIES = {
    # month x district x offense group: trend chart, KPI cards, district comparison
    "monthly": """
        SELECT d.month_start, d.year, f.district_code, o.ucr_group, o.category,
               count(*) AS incidents,
               count(*) FILTER (WHERE o.involves_firearm) AS firearm_incidents
        FROM fact_incident f
        JOIN dim_date d USING (date_key)
        JOIN dim_offense o USING (offense_key)
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY 1, 3, 4
    """,
    # year x district x offense group x weekday x hour: heatmap
    "heatmap": """
        SELECT d.year, f.district_code, o.ucr_group, d.iso_dow, f.hour,
               count(*) AS incidents
        FROM fact_incident f
        JOIN dim_date d USING (date_key)
        JOIN dim_offense o USING (offense_key)
        GROUP BY 1, 2, 3, 4, 5
    """,
    # year x district x offense group x grid cell: hotspot map
    "grid": """
        SELECT d.year, f.district_code, o.ucr_group, f.grid_id,
               g.center_lat AS lat, g.center_lon AS lon, count(*) AS incidents
        FROM fact_incident f
        JOIN dim_date d USING (date_key)
        JOIN dim_offense o USING (offense_key)
        JOIN dim_grid g USING (grid_id)
        GROUP BY 1, 2, 3, 4, 5, 6
    """,
    "hotspots": "SELECT * FROM v_repeat_location_hotspots ORDER BY hotspot_rank LIMIT 50",
    "districts": """
        SELECT district_code, district_name, area_sq_mi, center_lat, center_lon
        FROM dim_district ORDER BY district_code
    """,
}


def _shrink(df: pd.DataFrame) -> pd.DataFrame:
    """Categoricals and small ints keep the committed Parquet files tiny."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if (pd.api.types.is_string_dtype(s) or s.dtype == object) and s.nunique() < 500:
            out[col] = s.astype("category")
        elif pd.api.types.is_integer_dtype(s) and len(s):
            if s.min() >= -32_000 and s.max() < 32_000:
                out[col] = s.astype("int16")
            elif s.min() >= -2_000_000_000 and s.max() < 2_000_000_000:
                out[col] = s.astype("int32")
        elif pd.api.types.is_float_dtype(s):
            out[col] = s.astype("float32")
    return out


def simplify_geojson(src: Path, dst: Path, decimals: int = 5) -> None:
    """Round coordinates so the committed boundary file stays small."""
    geo = json.loads(src.read_text(encoding="utf-8"))

    def rnd(obj):
        if isinstance(obj, list):
            if obj and isinstance(obj[0], (int, float)):
                return [round(v, decimals) for v in obj]
            return [rnd(v) for v in obj]
        return obj

    for feat in geo["features"]:
        feat["geometry"]["coordinates"] = rnd(feat["geometry"]["coordinates"])
        feat["properties"] = {"district_code": str(feat["properties"]["dist_numc"]).zfill(2)}
    dst.write_text(json.dumps(geo, separators=(",", ":")), encoding="utf-8")


def export_dashboard_data(
    conn: sqlite3.Connection,
    out_dir: Path = DASHBOARD_DATA,
    geojson: Path | None = None,
    run_summary: dict | None = None,
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, sql in DASHBOARD_QUERIES.items():
        df = pd.read_sql(sql, conn)
        path = out_dir / f"{name}.parquet"
        _shrink(df).to_parquet(path, index=False, compression="zstd")
        sizes[name] = len(df)
        log.info(
            "dashboard export %-10s %7d rows %6.0f KB", name, len(df), path.stat().st_size / 1024
        )
    if geojson and geojson.exists():
        simplify_geojson(geojson, out_dir / "police_districts.geojson")
    if run_summary is not None:
        (out_dir / "pipeline_summary.json").write_text(
            json.dumps(run_summary, indent=2, default=str), encoding="utf-8"
        )
    return sizes


def export_bi_csv(conn: sqlite3.Connection, out_dir: Path = EXPORTS_DIR) -> list[Path]:
    """Flat, analysis-ready files for Tableau or Power BI (git-ignored)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = out_dir / "philly_incidents_detail.csv"
    monthly = out_dir / "philly_incidents_monthly_by_district.csv"

    cols = (
        "incident_key, dc_key, dispatch_ts, full_date, year, month, day_name, is_weekend, "
        "hour, daypart, ucr_code, offense_name, ucr_group, category, involves_firearm, "
        "district_code, district_name, psa, location_block, lat, lon"
    )
    first = True
    for chunk in pd.read_sql(
        f"SELECT {cols} FROM v_incident ORDER BY incident_key", conn, chunksize=200_000
    ):
        chunk.to_csv(detail, mode="w" if first else "a", header=first, index=False)
        first = False

    pd.read_sql(
        """
        SELECT d.month_start, d.year, d.month, f.district_code, ds.district_name,
               o.ucr_group, o.category, count(*) AS incidents
        FROM fact_incident f
        JOIN dim_date d USING (date_key)
        JOIN dim_offense o USING (offense_key)
        JOIN dim_district ds USING (district_code)
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        ORDER BY 1, 4, 6
        """,
        conn,
    ).to_csv(monthly, index=False)
    log.info("BI exports -> %s, %s", detail, monthly)
    return [detail, monthly]
