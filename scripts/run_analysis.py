"""Run the SQL analysis views against the warehouse and write CSVs to analysis/results/.

Usage: python scripts/run_analysis.py [path/to/warehouse.db]
"""

import logging
import sys

from phl_crime.analysis import run_analysis
from phl_crime.config import Settings
from phl_crime.db import connect

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = sys.argv[1] if len(sys.argv) > 1 else Settings().db_path
    run_analysis(connect(db))
