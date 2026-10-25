"""Runtime configuration, read from environment variables with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CARTO_SQL_URL = "https://phl.carto.com/api/v2/sql"
INCIDENTS_TABLE = "incidents_part1_part2"

# Official Philadelphia Police district boundaries (OpenDataPhilly, ArcGIS FeatureServer).
DISTRICTS_GEOJSON_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/"
    "Boundaries_District/FeatureServer/0/query"
    "?where=1%3D1&outFields=dist_numc&outSR=4326&f=geojson"
)

# Columns pulled from the source table. Geometry blobs are skipped because
# point_x / point_y already carry the coordinates.
SOURCE_COLUMNS = [
    "cartodb_id",
    "objectid",
    "dc_dist",
    "psa",
    "dispatch_date_time",
    "dispatch_date",
    "dispatch_time",
    "hour",
    "dc_key",
    "location_block",
    "ucr_general",
    "text_general_code",
    "point_x",
    "point_y",
]


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("PHL_DB", str(REPO_ROOT / "data" / "warehouse.db")))
    )
    start_year: int = field(default_factory=lambda: int(os.getenv("START_YEAR", "2020")))
    end_year: int = field(default_factory=lambda: int(os.getenv("END_YEAR", "2025")))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", str(REPO_ROOT / "data")))
    )
    page_size: int = field(default_factory=lambda: int(os.getenv("PAGE_SIZE", "20000")))
    request_pause_s: float = field(
        default_factory=lambda: float(os.getenv("REQUEST_PAUSE_S", "1.0"))
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"
