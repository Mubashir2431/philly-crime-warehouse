-- Reload the fact table for the load window from staging.
-- Delete-then-insert inside one transaction keeps the load idempotent:
-- running the same window twice leaves exactly one copy of every row.
-- Staging is filled one month at a time, so a complaint published twice in
-- different monthly batches can reach staging twice. ROW_NUMBER() keeps the
-- most recently published copy (highest source_id) of each complaint + offense.
-- Parameters: :start_key, :end_key (inclusive YYYYMMDD date keys)

DELETE FROM fact_incident
WHERE date_key BETWEEN :start_key AND :end_key;

INSERT INTO fact_incident (
    source_id, dc_key, date_key, hour, offense_key, district_code, grid_id,
    psa, location_block, dispatch_ts, lat, lon
)
WITH ranked AS (
    SELECT
        s.*,
        o.offense_key,
        row_number() OVER (
            PARTITION BY s.dc_key, o.offense_key
            ORDER BY s.source_id DESC
        ) AS copy_rank
    FROM stg_incident AS s
    JOIN dim_offense AS o
      ON o.ucr_code = s.ucr_code
     AND o.offense_name = s.offense_name
    WHERE s.date_key BETWEEN :start_key AND :end_key
)
SELECT
    source_id, dc_key, date_key, hour, offense_key, district_code, grid_id,
    psa, location_block, dispatch_ts, lat, lon
FROM ranked
WHERE copy_rank = 1;
