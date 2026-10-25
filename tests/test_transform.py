"""Unit tests for the pandas transform. No database or network needed."""

from __future__ import annotations

import pandas as pd
import pytest

from phl_crime.extract import build_month_query, month_ranges
from phl_crime.transform import (
    PHILLY_BOUNDS,
    build_grid_dim,
    build_offense_dim,
    grid_center,
    grid_id,
    normalize_block,
    normalize_offense_name,
    transform,
)

BASE = {
    "cartodb_id": "1",
    "objectid": "1",
    "dc_dist": "24",
    "psa": "2",
    "dispatch_date_time": "2023-07-04 18:30:00+00",
    "dispatch_date": "2023-07-04",
    "dispatch_time": "14:30:00",
    "hour": "14",
    "dc_key": "202324012345.00000000",
    "location_block": "3200 BLOCK  KENSINGTON AVE",
    "ucr_general": "600",
    "text_general_code": "Thefts",
    "point_x": "-75.1103",
    "point_y": "39.9990",
}


def make_raw(*overrides: dict) -> pd.DataFrame:
    rows = []
    for i, o in enumerate(overrides, start=1):
        row = {**BASE, "objectid": str(i), "cartodb_id": str(i), "dc_key": f"2023240{i:05d}"}
        row.update(o)
        rows.append(row)
    return pd.DataFrame(rows, dtype=str)


def run(*overrides: dict):
    return transform(make_raw(*overrides), 2020, 2025)


def test_clean_row_types_and_features():
    res = run({})
    row = res.clean.iloc[0]
    assert res.stats["rows_out"] == 1 and res.stats["rows_rejected"] == 0
    assert row["date_key"] == 20230704
    assert row["hour"] == 14
    assert row["dc_key"] == "202324000001"
    assert row["district_code"] == "24"
    assert row["offense_name"] == "Theft"
    assert row["location_block"] == "3200 BLOCK KENSINGTON AV"
    assert row["dispatch_ts"] == pd.Timestamp("2023-07-04 14:30:00")


def test_dc_key_decimal_suffix_is_stripped():
    res = run({"dc_key": "202014003725.00000000"})
    assert res.clean.iloc[0]["dc_key"] == "202014003725"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"dispatch_date": "", "dispatch_time": ""}, "invalid_dispatch_date"),
        ({"dispatch_date": "not-a-date"}, "invalid_dispatch_date"),
        ({"dispatch_date": "2019-12-31"}, "date_out_of_range"),
        ({"dispatch_date": "2026-01-01"}, "date_out_of_range"),
        ({"ucr_general": ""}, "unknown_ucr_code"),
        ({"ucr_general": "9999"}, "unknown_ucr_code"),
        ({"text_general_code": "   "}, "missing_offense_name"),
        ({"dc_dist": "04"}, "invalid_district"),
        ({"dc_dist": ""}, "invalid_district"),
        ({"objectid": ""}, "missing_source_id"),
        ({"dc_key": ""}, "missing_dc_key"),
    ],
)
def test_rejection_rules(override, reason):
    res = run(override)
    assert res.stats["rows_out"] == 0
    assert res.rejects["reject_reason"].tolist() == [reason]
    assert res.stats[f"rejected_{reason}"] == 1


def test_single_digit_district_is_zero_padded():
    res = run({"dc_dist": "1"})
    assert res.clean.iloc[0]["district_code"] == "01"


def test_duplicates_keep_latest_published_row():
    raw = make_raw({}, {})
    raw.loc[:, "dc_key"] = "202324000099"  # same complaint, same offense
    res = transform(raw, 2020, 2025)
    assert res.stats["rows_out"] == 1
    assert res.stats["rejected_duplicate_dc_key_offense"] == 1
    assert res.clean.iloc[0]["source_id"] == 2


def test_same_complaint_different_offense_is_not_a_duplicate():
    raw = make_raw({}, {"ucr_general": "400", "text_general_code": "Aggravated Assault Firearm"})
    raw.loc[:, "dc_key"] = "202324000099"
    res = transform(raw, 2020, 2025)
    assert res.stats["rows_out"] == 2


@pytest.mark.parametrize(
    ("x", "y"),
    [("0", "0"), ("-80.19", "25.76"), ("", ""), ("-75.1", "41.0"), ("abc", "39.95")],
)
def test_bad_coordinates_are_nulled_not_rejected(x, y):
    res = run({"point_x": x, "point_y": y})
    row = res.clean.iloc[0]
    assert res.stats["rows_out"] == 1
    assert res.stats["coords_nulled"] == 1
    assert pd.isna(row["lat"]) and pd.isna(row["lon"]) and pd.isna(row["grid_id"])


def test_missing_and_out_of_bounds_coordinates_are_counted_separately():
    res = run({"point_x": "", "point_y": ""}, {"point_x": "0", "point_y": "0"}, {})
    assert res.stats["coords_missing"] == 1
    assert res.stats["coords_out_of_bounds"] == 1
    assert res.clean["lat"].notna().sum() == 1


def test_source_hour_gaps_are_counted():
    res = run({"hour": ""}, {"hour": "3"}, {})
    assert res.stats["source_hour_missing"] == 1
    assert res.stats["source_hour_mismatch"] == 1
    assert res.clean["hour"].tolist() == [14, 14, 14]


def test_every_input_row_is_accounted_for():
    res = run({}, {"dc_dist": "04"}, {"dispatch_date": "2019-01-01"}, {"point_x": "0"})
    assert res.stats["rows_in"] == res.stats["rows_out"] + res.stats["rows_rejected"]
    assert len(res.rejects) == res.stats["rows_rejected"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DRIVING UNDER THE INFLUENCE", "Driving Under the Influence"),
        ("Narcotic / Drug Law Violations", "Narcotic/Drug Law Violations"),
        ("Thefts", "Theft"),
        ("  Robbery   Firearm ", "Robbery Firearm"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_offense_name(raw, expected):
    assert normalize_offense_name(raw) == expected


def test_normalize_block():
    s = normalize_block(pd.Series(["100 block  market street", "  ", "5200 BLOCK BALTIMORE AVE"]))
    assert s[0] == "100 BLOCK MARKET ST"
    assert pd.isna(s[1])
    assert s[2] == "5200 BLOCK BALTIMORE AV"


def test_grid_round_trip_contains_point():
    lat = pd.Series([39.9526, 40.05], dtype="Float64")
    lon = pd.Series([-75.1652, -75.0], dtype="Float64")
    gid = grid_id(lat, lon)
    clat, clon = grid_center(gid)
    assert ((clat - lat).abs() <= 0.0025 + 1e-9).all()
    assert ((clon - lon).abs() <= 0.0025 + 1e-9).all()
    assert gid.min() >= 0


def test_offense_dim_categories_and_firearm_flag():
    res = run(
        {},
        {"ucr_general": "300", "text_general_code": "Robbery Firearm"},
        {"ucr_general": "300", "text_general_code": "Robbery No Firearm"},
        {"ucr_general": "1400", "text_general_code": "Vandalism/Criminal Mischief"},
    )
    dim = build_offense_dim(res.clean).set_index("offense_name")
    assert dim.loc["Robbery Firearm", "involves_firearm"]
    assert not dim.loc["Robbery No Firearm", "involves_firearm"]
    assert dim.loc["Robbery Firearm", "category"] == "Violent"
    assert dim.loc["Theft", "category"] == "Property"
    assert dim.loc["Vandalism/Criminal Mischief", "ucr_part"] == 2


def test_grid_dim_is_unique():
    res = run({}, {}, {"point_x": "-75.2", "point_y": "39.95"})
    dim = build_grid_dim(res.clean)
    assert dim["grid_id"].is_unique
    assert dim["center_lat"].between(PHILLY_BOUNDS["lat_min"], PHILLY_BOUNDS["lat_max"]).all()


def test_month_ranges_cover_years_without_gaps():
    ranges = month_ranges(2020, 2021)
    assert len(ranges) == 24
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False))
    assert ranges[-1][1].isoformat() == "2022-01-01"


def test_month_query_is_paged_and_ordered():
    a, b = month_ranges(2024, 2024)[1]
    sql = build_month_query(a, b, limit=500, offset=1000)
    assert "dispatch_date >= '2024-02-01'" in sql and "dispatch_date < '2024-03-01'" in sql
    assert sql.endswith("ORDER BY cartodb_id LIMIT 500 OFFSET 1000")
