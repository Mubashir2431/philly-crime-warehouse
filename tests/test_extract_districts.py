"""Extract paging/caching and district geometry, with the network mocked out."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from phl_crime import extract
from phl_crime.config import SOURCE_COLUMNS, Settings
from phl_crime.districts import build_district_dim, polygon_area_sq_mi


def fake_rows(n: int, start: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {c: [f"{c}{i}" for i in range(start, start + n)] for c in SOURCE_COLUMNS}, dtype=str
    )


def test_extract_month_pages_until_short_page(monkeypatch):
    calls = []

    def fake_fetch(session, sql, timeout=180):
        calls.append(sql)
        offset = int(sql.rsplit("OFFSET ", 1)[1])
        return fake_rows(3 if offset < 6 else 1, offset)

    monkeypatch.setattr(extract, "fetch_csv", fake_fetch)
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    settings = replace(Settings(), page_size=3, request_pause_s=0)
    a, b = extract.month_ranges(2024, 2024)[0]
    df = extract.extract_month(None, a, b, settings)
    assert len(calls) == 3  # 3 + 3 + 1 rows
    assert len(df) == 7
    assert df["objectid"].is_unique


def test_extract_skips_cached_months(monkeypatch, tmp_path):
    settings = replace(Settings(), data_dir=tmp_path, start_year=2024, end_year=2024)
    settings.raw_dir.mkdir(parents=True)
    for start, _ in extract.month_ranges(2024, 2024)[:11]:
        fake_rows(2).to_csv(settings.raw_dir / f"incidents_{start:%Y_%m}.csv", index=False)

    fetched = []

    def fake_month(session, start, end, s):
        fetched.append(start)
        return fake_rows(5)

    monkeypatch.setattr(extract, "extract_month", fake_month)
    files = extract.extract_incidents(settings)
    assert len(files) == 12
    assert [f"{d:%m}" for d in fetched] == ["12"]
    assert not list(settings.raw_dir.glob("*.part"))


def test_session_retries_transient_errors():
    adapter = extract.make_session().get_adapter("https://phl.carto.com")
    retry = adapter.max_retries
    assert retry.total >= 3
    assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)


def test_square_area():
    # 0.01 x 0.01 degree square at Philadelphia's latitude is about 0.37 sq mi
    ring = [[-75.17, 39.95], [-75.16, 39.95], [-75.16, 39.96], [-75.17, 39.96], [-75.17, 39.95]]
    area = polygon_area_sq_mi({"type": "Polygon", "coordinates": [ring]})
    assert area == pytest.approx(0.6909 * 0.5299 * 1.0, rel=0.02)


def test_district_dim_without_geometry():
    centers = pd.DataFrame(
        {"district_code": ["01", "77"], "lat": [39.92, 39.88], "lon": [-75.17, -75.24]}
    )
    dim = build_district_dim(None, centers).set_index("district_code")
    assert dim.loc["77", "district_name"] == "Airport (77th)"
    assert dim["area_sq_mi"].isna().all()
    assert dim.loc["01", "center_lat"] == pytest.approx(39.92)
