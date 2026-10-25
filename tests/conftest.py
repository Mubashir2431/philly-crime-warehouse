"""Shared fixtures.

By default the warehouse tests build a fresh SQLite file from the committed
fixture sample (tests/fixtures/sample_raw.csv), so they need no network and no
large download. Set PHL_TEST_DB=path/to/warehouse.db to point the same
data-quality tests at a full build instead.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from phl_crime.db import connect
from phl_crime.pipeline import main

FIXTURE = Path(__file__).parent / "fixtures" / "sample_raw.csv"


def build_fixture_db(tmp: Path, *extra: str) -> Path:
    db = tmp / "warehouse.db"
    code = main(
        [
            "run",
            "--raw-file",
            str(FIXTURE),
            "--db",
            str(db),
            "--out-dir",
            str(tmp / "processed"),
            "--no-dashboard-export",
            *extra,
        ]
    )
    assert code == 0, "fixture ETL run reported failed data-quality checks"
    return db


@pytest.fixture(scope="session")
def built_db(tmp_path_factory) -> Path:
    if os.getenv("PHL_TEST_DB"):
        return Path(os.environ["PHL_TEST_DB"])
    return build_fixture_db(tmp_path_factory.mktemp("wh"))


@pytest.fixture(scope="session")
def is_fixture_db() -> bool:
    return not os.getenv("PHL_TEST_DB")


@pytest.fixture()
def conn(built_db) -> sqlite3.Connection:
    c = connect(built_db)
    yield c
    c.close()
