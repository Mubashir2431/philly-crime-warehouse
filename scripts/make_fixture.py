"""Build tests/fixtures/sample_raw.csv: a small real sample plus known-bad rows.

The sample is drawn from the cached raw extract so CI can run the whole ETL
end to end without calling the API. A handful of rows are deliberately broken
so the rejection rules are exercised; tests assert on exactly these counts.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUT = ROOT / "tests" / "fixtures" / "sample_raw.csv"


def main() -> None:
    raw = pd.concat(
        (
            pd.read_csv(p, dtype=str, keep_default_na=False)
            for p in sorted(RAW_DIR.glob("incidents_20??_??.csv"))
        ),
        ignore_index=True,
    )
    raw = raw[raw["dc_dist"] != "04"]
    sample = (
        raw.assign(_y=raw["dispatch_date"].str[:4])
        .groupby("_y", group_keys=False)
        .sample(n=500, random_state=42)
        .drop(columns="_y")
        .reset_index(drop=True)
    )
    base = sample.iloc[0].copy()
    bad = []

    def broken(**changes):
        row = base.copy()
        row["objectid"] = str(900_000_000 + len(bad))
        row["cartodb_id"] = row["objectid"]
        row["dc_key"] = f"2099{len(bad):08d}.00000000"
        for k, v in changes.items():
            row[k] = v
        bad.append(row)

    broken(dispatch_date="", dispatch_time="")  # invalid_dispatch_date
    broken(dispatch_date="2019-12-31")  # date_out_of_range
    broken(ucr_general="9999")  # unknown_ucr_code
    broken(dc_dist="04")  # invalid_district
    broken(dc_dist="")  # invalid_district
    broken(point_x="0", point_y="0")  # kept, coordinates nulled
    broken(point_x="-80.1", point_y="25.8")  # kept, coordinates nulled
    dup = sample.iloc[1].copy()
    dup["objectid"] = str(int(dup["objectid"]) + 1)  # duplicate_dc_key_offense
    dup["cartodb_id"] = dup["objectid"]
    bad.append(dup)

    out = pd.concat([sample, pd.DataFrame(bad)], ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"wrote {len(out)} rows -> {OUT}")


if __name__ == "__main__":
    main()
