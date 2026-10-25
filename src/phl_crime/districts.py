"""Police district dimension built from the official boundary polygons."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .transform import VALID_DISTRICTS

EARTH_RADIUS_MI = 3958.8
SQ_MI_PER_SQ_DEG_LAT = (math.pi / 180 * EARTH_RADIUS_MI) ** 2


def ring_area_sq_mi(ring: list[list[float]]) -> float:
    """Planar shoelace area after a local equirectangular projection.

    At city scale (about 15 miles across) this is within a fraction of a
    percent of a proper equal-area projection, without a GIS dependency.
    """
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    xs = [p[0] * math.cos(lat0) for p in ring]
    ys = [p[1] for p in ring]
    twice = sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(ring) - 1))
    return abs(twice) / 2 * SQ_MI_PER_SQ_DEG_LAT


def polygon_area_sq_mi(geometry: dict) -> float:
    polys = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        polys = [polys]
    total = 0.0
    for poly in polys:
        outer, *holes = poly
        total += ring_area_sq_mi(outer) - sum(ring_area_sq_mi(h) for h in holes)
    return total


def build_district_dim(geojson_path: Path | None, clean: pd.DataFrame) -> pd.DataFrame:
    """One row per district code seen in the data or in the boundary file."""
    areas: dict[str, float] = {}
    if geojson_path and geojson_path.exists():
        geo = json.loads(geojson_path.read_text(encoding="utf-8"))
        for feat in geo["features"]:
            code = str(feat["properties"]["dist_numc"]).zfill(2)
            areas[code] = areas.get(code, 0.0) + polygon_area_sq_mi(feat["geometry"])

    centers = clean.groupby("district_code")[["lat", "lon"]].median()
    codes = sorted(set(clean["district_code"].dropna()) | (set(areas) & VALID_DISTRICTS))
    rows = []
    for code in codes:
        name = "Airport (77th)" if code == "77" else f"District {code}"
        lat, lon = centers.loc[code] if code in centers.index else (None, None)
        area = areas.get(code)
        rows.append(
            {
                "district_code": code,
                "district_name": name,
                "area_sq_mi": round(area, 3) if area else None,
                "center_lat": round(float(lat), 6) if pd.notna(lat) else None,
                "center_lon": round(float(lon), 6) if pd.notna(lon) else None,
            }
        )
    return pd.DataFrame(rows)
