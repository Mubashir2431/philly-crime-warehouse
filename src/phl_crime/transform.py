"""Transform: clean, validate, and enrich raw incident rows with pandas.

The output is one clean DataFrame ready for the staging table, plus a
rejects DataFrame that records every dropped row and the reason it was dropped.
Rows are only rejected when they cannot be placed in the star schema (no date,
no offense, no district) or are duplicates. Bad coordinates do not reject a row:
the incident still counts, it just gets null coordinates and is left off maps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Generous bounding box around the City of Philadelphia (WGS84).
PHILLY_BOUNDS = {"lat_min": 39.86, "lat_max": 40.14, "lon_min": -75.29, "lon_max": -74.95}

# Size of a hotspot grid cell in degrees (about 550 m north-south, 425 m east-west).
GRID_DEG = 0.005

VALID_DISTRICTS = {
    "01",
    "02",
    "03",
    "05",
    "06",
    "07",
    "08",
    "09",
    "12",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "22",
    "24",
    "25",
    "26",
    "35",
    "39",
    "77",
}

# UCR general code -> (group name, category, UCR part)
UCR_GROUPS: dict[int, tuple[str, str, int]] = {
    100: ("Homicide", "Violent", 1),
    200: ("Rape", "Violent", 1),
    300: ("Robbery", "Violent", 1),
    400: ("Aggravated Assault", "Violent", 1),
    500: ("Burglary", "Property", 1),
    600: ("Theft", "Property", 1),
    700: ("Motor Vehicle Theft", "Property", 1),
    800: ("Other Assaults", "Other", 2),
    900: ("Arson", "Property", 1),
    1000: ("Forgery and Counterfeiting", "Other", 2),
    1100: ("Fraud", "Other", 2),
    1200: ("Embezzlement", "Other", 2),
    1300: ("Receiving Stolen Property", "Other", 2),
    1400: ("Vandalism/Criminal Mischief", "Other", 2),
    1500: ("Weapon Violations", "Other", 2),
    1600: ("Prostitution and Commercialized Vice", "Other", 2),
    1700: ("Other Sex Offenses", "Other", 2),
    1800: ("Narcotic/Drug Law Violations", "Other", 2),
    1900: ("Gambling Violations", "Other", 2),
    2000: ("Offenses Against Family and Children", "Other", 2),
    2100: ("Driving Under the Influence", "Other", 2),
    2200: ("Liquor Law Violations", "Other", 2),
    2300: ("Public Drunkenness", "Other", 2),
    2400: ("Disorderly Conduct", "Other", 2),
    2500: ("Vagrancy/Loitering", "Other", 2),
    2600: ("All Other Offenses", "Other", 2),
}

# Spelling and casing variants seen in the source, normalized to one label.
OFFENSE_NAME_FIXES = {
    "driving under the influence": "Driving Under the Influence",
    "narcotic / drug law violations": "Narcotic/Drug Law Violations",
    "thefts": "Theft",
    "homicide - criminal": "Homicide - Criminal",
    "homicide - criminal ": "Homicide - Criminal",
}

CLEAN_COLUMNS = [
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


@dataclass
class TransformResult:
    clean: pd.DataFrame
    rejects: pd.DataFrame
    stats: dict[str, int]


def _blank_to_na(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    return s.mask(s == "", pd.NA)


def normalize_offense_name(name: str | None) -> str | None:
    if name is None or pd.isna(name):
        return None
    collapsed = re.sub(r"\s+", " ", str(name)).strip()
    if not collapsed:
        return None
    return OFFENSE_NAME_FIXES.get(collapsed.lower(), collapsed)


def normalize_block(block: pd.Series) -> pd.Series:
    """Uppercase, collapse whitespace, and unify common street suffixes."""
    s = block.astype("string").str.upper().str.replace(r"\s+", " ", regex=True).str.strip()
    s = s.str.replace(r"\bAVE\b", "AV", regex=True).str.replace(r"\bSTREET\b", "ST", regex=True)
    return s.mask(s == "", pd.NA)


def grid_id(lat: pd.Series, lon: pd.Series) -> pd.Series:
    """Stable integer id for the GRID_DEG cell containing each point."""
    row = np.floor((lat - PHILLY_BOUNDS["lat_min"]) / GRID_DEG)
    col = np.floor((lon - PHILLY_BOUNDS["lon_min"]) / GRID_DEG)
    gid = row * 1000 + col
    return gid.astype("Int64")


def grid_center(gid: pd.Series) -> tuple[pd.Series, pd.Series]:
    row = gid // 1000
    col = gid % 1000
    lat = PHILLY_BOUNDS["lat_min"] + (row + 0.5) * GRID_DEG
    lon = PHILLY_BOUNDS["lon_min"] + (col + 0.5) * GRID_DEG
    return lat.astype(float).round(5), lon.astype(float).round(5)


def transform(raw: pd.DataFrame, start_year: int, end_year: int) -> TransformResult:
    """Clean raw source rows. Pure function: no I/O, easy to unit test."""
    df = raw.copy()
    rows_in = len(df)
    reasons = pd.Series(pd.NA, index=df.index, dtype="string")

    def reject(mask: pd.Series, reason: str) -> None:
        # first failing rule wins, so each row has exactly one reason
        reasons.loc[mask & reasons.isna()] = reason

    for col in df.columns:
        df[col] = _blank_to_na(df[col])

    # --- types -----------------------------------------------------------
    df["source_id"] = pd.to_numeric(df["objectid"], errors="coerce").astype("Int64")
    dc = pd.to_numeric(df["dc_key"], errors="coerce")
    df["dc_key"] = dc.round().astype("Int64").astype("string")

    time_str = df["dispatch_time"].fillna("00:00:00")
    ts = pd.to_datetime(df["dispatch_date"] + " " + time_str, errors="coerce", format="mixed")
    df["dispatch_ts"] = ts

    ucr = pd.to_numeric(df["ucr_general"], errors="coerce")
    df["ucr_code"] = ucr.astype("Int64")
    df["offense_name"] = df["text_general_code"].map(normalize_offense_name).astype("string")

    dist = df["dc_dist"].str.replace(r"\.0+$", "", regex=True).str.zfill(2)
    df["district_code"] = dist
    df["psa"] = df["psa"].str.upper()

    # --- row-level rejection rules ---------------------------------------
    reject(df["source_id"].isna(), "missing_source_id")
    reject(df["dc_key"].isna(), "missing_dc_key")
    reject(df["dispatch_ts"].isna(), "invalid_dispatch_date")
    years = df["dispatch_ts"].dt.year
    reject(~years.between(start_year, end_year), "date_out_of_range")
    reject(df["ucr_code"].isna() | ~df["ucr_code"].isin(list(UCR_GROUPS)), "unknown_ucr_code")
    reject(df["offense_name"].isna(), "missing_offense_name")
    reject(
        df["district_code"].isna() | ~df["district_code"].isin(VALID_DISTRICTS), "invalid_district"
    )

    # Duplicates: the same complaint number recorded twice for the same offense.
    # Keep the most recently published row (highest objectid).
    ok = reasons.isna()
    order = df.loc[ok].sort_values("source_id", ascending=False)
    dup_idx = order.index[order.duplicated(subset=["dc_key", "ucr_code", "offense_name"])]
    reject(df.index.isin(dup_idx), "duplicate_dc_key_offense")

    # --- coordinates: null out instead of rejecting ----------------------
    lon = pd.to_numeric(df["point_x"], errors="coerce")
    lat = pd.to_numeric(df["point_y"], errors="coerce")
    has_coords = (lat.notna() & lon.notna()).fillna(False).astype(bool)
    in_bounds = (
        lat.between(PHILLY_BOUNDS["lat_min"], PHILLY_BOUNDS["lat_max"])
        & lon.between(PHILLY_BOUNDS["lon_min"], PHILLY_BOUNDS["lon_max"])
    ).fillna(False).astype(bool) & has_coords
    df["lat"] = lat.where(in_bounds).round(6)
    df["lon"] = lon.where(in_bounds).round(6)
    df["grid_id"] = grid_id(df["lat"], df["lon"])

    # --- derived features ------------------------------------------------
    t = df["dispatch_ts"].dt
    df["date_key"] = (t.year * 10000 + t.month * 100 + t.day).astype("Int64")
    # The source also publishes an `hour` column, but it is blank for about
    # 100k rows (mostly 2023) and sometimes disagrees with dispatch_time, so
    # the hour is always derived from dispatch_time and the gaps are reported.
    src_hour = pd.to_numeric(df["hour"], errors="coerce")
    df["hour"] = df["dispatch_ts"].dt.hour.astype("Int64")
    df["location_block"] = normalize_block(df["location_block"])

    rejected = reasons.notna()
    rejects = raw.loc[rejected].copy()
    rejects["reject_reason"] = reasons[rejected]

    clean = df.loc[~rejected, CLEAN_COLUMNS].copy()
    clean = clean.sort_values("source_id").reset_index(drop=True)

    stats = {
        "rows_in": rows_in,
        "rows_rejected": int(rejected.sum()),
        "rows_out": len(clean),
        "coords_nulled": int((~in_bounds & ~rejected).sum()),
        "coords_missing": int((~has_coords & ~rejected).sum()),
        "coords_out_of_bounds": int((has_coords & ~in_bounds & ~rejected).sum()),
        "source_hour_missing": int((src_hour.isna() & ~rejected).sum()),
        "source_hour_mismatch": int(
            (src_hour.notna() & (src_hour != df["hour"]) & ~rejected).sum()
        ),
    }
    for reason, n in reasons[rejected].value_counts().items():
        stats[f"rejected_{reason}"] = int(n)
    log.info("transform: %s", stats)
    return TransformResult(clean=clean, rejects=rejects, stats=stats)


def build_offense_dim(clean: pd.DataFrame) -> pd.DataFrame:
    pairs = clean[["ucr_code", "offense_name"]].drop_duplicates()
    groups = pairs["ucr_code"].map(UCR_GROUPS)
    out = pairs.assign(
        ucr_group=groups.str[0],
        category=groups.str[1],
        ucr_part=groups.str[2].astype(int),
        involves_firearm=pairs["offense_name"].str.contains(r"\bFirearm\b", regex=True)
        & ~pairs["offense_name"].str.contains(r"\bNo Firearm\b", regex=True),
    )
    return out.sort_values(["ucr_code", "offense_name"]).reset_index(drop=True)


def build_grid_dim(clean: pd.DataFrame) -> pd.DataFrame:
    gids = pd.Series(clean["grid_id"].dropna().unique(), dtype="Int64").sort_values()
    lat, lon = grid_center(gids)
    return pd.DataFrame(
        {"grid_id": gids.values, "center_lat": lat.values, "center_lon": lon.values}
    )
