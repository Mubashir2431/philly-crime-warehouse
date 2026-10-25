-- v_incident: the fact table joined to every dimension.
-- Question: what does one fully described incident row look like?
-- Used by the BI export so the joins live in one place.
DROP VIEW IF EXISTS v_incident;
CREATE VIEW v_incident AS
SELECT
    f.incident_key,
    f.dc_key,
    f.dispatch_ts,
    d.full_date,
    d.year,
    d.month,
    d.month_start,
    d.iso_dow,
    d.day_name,
    d.is_weekend,
    f.hour,
    h.daypart,
    o.ucr_code,
    o.offense_name,
    o.ucr_group,
    o.category,
    o.ucr_part,
    o.involves_firearm,
    f.district_code,
    ds.district_name,
    f.psa,
    f.location_block,
    f.grid_id,
    f.lat,
    f.lon
FROM fact_incident AS f
JOIN dim_date     AS d  ON d.date_key = f.date_key
JOIN dim_hour     AS h  ON h.hour = f.hour
JOIN dim_offense  AS o  ON o.offense_key = f.offense_key
JOIN dim_district AS ds ON ds.district_code = f.district_code;
