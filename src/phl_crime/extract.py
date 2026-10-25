"""Extract: paged pulls from the OpenDataPhilly Carto SQL API, cached to disk.

Each calendar month is fetched in pages of ``page_size`` rows and written to
``data/raw/incidents_YYYY_MM.csv``. Months that already have a cached file are
skipped, so a rerun after a network failure only fetches what is missing.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    CARTO_SQL_URL,
    DISTRICTS_GEOJSON_URL,
    INCIDENTS_TABLE,
    SOURCE_COLUMNS,
    Settings,
)

log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    """HTTP session with exponential backoff on transient errors."""
    retry = Retry(
        total=6,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "philly-crime-warehouse/0.1 (portfolio project)"
    return session


def month_ranges(start_year: int, end_year: int) -> list[tuple[date, date]]:
    """Half-open [first_of_month, first_of_next_month) ranges, inclusive of end_year."""
    out = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            start = date(year, month, 1)
            end = date(year + (month == 12), month % 12 + 1, 1)
            out.append((start, end))
    return out


def build_month_query(start: date, end: date, limit: int, offset: int) -> str:
    cols = ", ".join(SOURCE_COLUMNS)
    # dispatch_date is stored as ISO text in the source table, so string
    # comparison is both correct and index friendly.
    return (
        f"SELECT {cols} FROM {INCIDENTS_TABLE} "
        f"WHERE dispatch_date >= '{start.isoformat()}' AND dispatch_date < '{end.isoformat()}' "
        f"ORDER BY cartodb_id LIMIT {limit} OFFSET {offset}"
    )


def fetch_csv(session: requests.Session, sql: str, timeout: int = 180) -> pd.DataFrame:
    resp = session.get(CARTO_SQL_URL, params={"q": sql, "format": "csv"}, timeout=timeout)
    resp.raise_for_status()
    if not resp.text.strip():
        return pd.DataFrame(columns=SOURCE_COLUMNS)
    return pd.read_csv(io.StringIO(resp.text), dtype=str, keep_default_na=False)


def extract_month(
    session: requests.Session, start: date, end: date, settings: Settings
) -> pd.DataFrame:
    pages = []
    offset = 0
    while True:
        sql = build_month_query(start, end, settings.page_size, offset)
        page = fetch_csv(session, sql)
        pages.append(page)
        time.sleep(settings.request_pause_s)
        if len(page) < settings.page_size:
            break
        offset += settings.page_size
    return pd.concat(pages, ignore_index=True)


def extract_incidents(settings: Settings, force: bool = False) -> list[Path]:
    """Download every month in the configured range. Returns the cached month files.

    Months are kept as separate files so the transform can stream them one at a
    time instead of holding the whole extract in memory.
    """
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    month_files = []
    for start, end in month_ranges(settings.start_year, settings.end_year):
        path = settings.raw_dir / f"incidents_{start:%Y_%m}.csv"
        month_files.append(path)
        if path.exists() and not force:
            continue
        df = extract_month(session, start, end, settings)
        tmp = path.with_suffix(".csv.part")
        df.to_csv(tmp, index=False)
        tmp.replace(path)  # atomic, so a crash never leaves a half-written cache file
        log.info("extracted %s: %d rows", f"{start:%Y-%m}", len(df))
    log.info("raw extract: %d cached month files in %s", len(month_files), settings.raw_dir)
    return month_files


def source_row_count(settings: Settings, session: requests.Session | None = None) -> int:
    """Row count reported by the source for the configured window (for reconciliation)."""
    session = session or make_session()
    sql = (
        f"SELECT count(*) AS n FROM {INCIDENTS_TABLE} "
        f"WHERE dispatch_date >= '{settings.start_year}-01-01' "
        f"AND dispatch_date < '{settings.end_year + 1}-01-01'"
    )
    resp = session.get(CARTO_SQL_URL, params={"q": sql}, timeout=180)
    resp.raise_for_status()
    return int(resp.json()["rows"][0]["n"])


def extract_district_boundaries(settings: Settings, force: bool = False) -> Path:
    """Official police district polygons (GeoJSON, WGS84)."""
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    path = settings.raw_dir / "police_districts.geojson"
    if path.exists() and not force:
        return path
    resp = make_session().get(DISTRICTS_GEOJSON_URL, timeout=120)
    resp.raise_for_status()
    geo = resp.json()
    if not geo.get("features"):
        raise RuntimeError("district boundary response had no features")
    path.write_text(json.dumps(geo), encoding="utf-8")
    log.info("district boundaries: %d features -> %s", len(geo["features"]), path)
    return path
