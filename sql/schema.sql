-- Star schema for Philadelphia crime incidents (SQLite).
-- Safe to run repeatedly: every object is created only if missing.
-- Foreign keys are enforced because every connection runs PRAGMA foreign_keys = ON.

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_date (
    date_key      INTEGER PRIMARY KEY,                 -- YYYYMMDD
    full_date     TEXT    NOT NULL UNIQUE,             -- ISO 'YYYY-MM-DD'
    year          INTEGER NOT NULL,
    quarter       INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month         INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name    TEXT    NOT NULL,
    month_start   TEXT    NOT NULL,
    day_of_month  INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    iso_dow       INTEGER NOT NULL CHECK (iso_dow BETWEEN 1 AND 7),   -- 1 = Monday
    day_name      TEXT    NOT NULL,
    is_weekend    INTEGER NOT NULL CHECK (is_weekend IN (0, 1))
);

CREATE TABLE IF NOT EXISTS dim_hour (
    hour        INTEGER PRIMARY KEY CHECK (hour BETWEEN 0 AND 23),
    hour_label  TEXT NOT NULL,                         -- e.g. '07:00'
    daypart     TEXT NOT NULL                          -- Overnight / Morning / Afternoon / Evening
);

CREATE TABLE IF NOT EXISTS dim_offense (
    offense_key       INTEGER PRIMARY KEY AUTOINCREMENT,
    ucr_code          INTEGER NOT NULL,
    offense_name      TEXT    NOT NULL,
    ucr_group         TEXT    NOT NULL,
    category          TEXT    NOT NULL CHECK (category IN ('Violent', 'Property', 'Other')),
    ucr_part          INTEGER NOT NULL CHECK (ucr_part IN (1, 2)),
    involves_firearm  INTEGER NOT NULL DEFAULT 0 CHECK (involves_firearm IN (0, 1)),
    UNIQUE (ucr_code, offense_name)
);

CREATE TABLE IF NOT EXISTS dim_district (
    district_code  TEXT PRIMARY KEY CHECK (length(district_code) = 2),
    district_name  TEXT NOT NULL,
    area_sq_mi     REAL,                               -- from official boundary polygons
    center_lat     REAL,
    center_lon     REAL
);

-- 0.005 degree grid cells (about 550 m x 425 m) used for hotspot maps
CREATE TABLE IF NOT EXISTS dim_grid (
    grid_id     INTEGER PRIMARY KEY,
    center_lat  REAL NOT NULL,
    center_lon  REAL NOT NULL
);

-- ---------------------------------------------------------------------------
-- Fact: one row per offense on a police incident report
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_incident (
    incident_key    INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL UNIQUE,           -- objectid in the source table
    dc_key          TEXT    NOT NULL,                  -- police complaint number
    date_key        INTEGER NOT NULL REFERENCES dim_date (date_key),
    hour            INTEGER NOT NULL REFERENCES dim_hour (hour),
    offense_key     INTEGER NOT NULL REFERENCES dim_offense (offense_key),
    district_code   TEXT    NOT NULL REFERENCES dim_district (district_code),
    grid_id         INTEGER REFERENCES dim_grid (grid_id),
    psa             TEXT,
    location_block  TEXT,
    dispatch_ts     TEXT    NOT NULL,                  -- local wall time 'YYYY-MM-DD HH:MM:SS'
    lat             REAL,
    lon             REAL,
    loaded_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT uq_fact_dc_offense UNIQUE (dc_key, offense_key),
    CONSTRAINT ck_fact_coords CHECK ((lat IS NULL) = (lon IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_fact_date      ON fact_incident (date_key);
CREATE INDEX IF NOT EXISTS ix_fact_district  ON fact_incident (district_code, date_key);
CREATE INDEX IF NOT EXISTS ix_fact_offense   ON fact_incident (offense_key);
CREATE INDEX IF NOT EXISTS ix_fact_grid      ON fact_incident (grid_id) WHERE grid_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fact_block     ON fact_incident (location_block);

-- ---------------------------------------------------------------------------
-- Staging and pipeline metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_incident (
    source_id       INTEGER,
    dc_key          TEXT,
    dispatch_ts     TEXT,
    date_key        INTEGER,
    hour            INTEGER,
    district_code   TEXT,
    psa             TEXT,
    ucr_code        INTEGER,
    offense_name    TEXT,
    location_block  TEXT,
    lat             REAL,
    lon             REAL,
    grid_id         INTEGER
);

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    start_year      INTEGER NOT NULL,
    end_year        INTEGER NOT NULL,
    rows_extracted  INTEGER,
    rows_rejected   INTEGER,
    rows_loaded     INTEGER,
    dq_passed       INTEGER,
    status          TEXT    NOT NULL DEFAULT 'running',
    details         TEXT                               -- JSON run summary
);

CREATE TABLE IF NOT EXISTS dq_reject_summary (
    run_id   INTEGER NOT NULL REFERENCES etl_run_log (run_id) ON DELETE CASCADE,
    reason   TEXT    NOT NULL,
    n_rows   INTEGER NOT NULL,
    PRIMARY KEY (run_id, reason)
);
