"""Write clean, flat CSVs for Tableau or Power BI to exports/ (git-ignored).

Usage: python scripts/export_bi.py [path/to/warehouse.db]
"""

import logging
import sys

from phl_crime.config import Settings
from phl_crime.db import connect
from phl_crime.export import export_bi_csv

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = sys.argv[1] if len(sys.argv) > 1 else Settings().db_path
    for path in export_bi_csv(connect(db)):
        print(path)
