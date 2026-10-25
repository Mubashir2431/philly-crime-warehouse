-- v_hour_weekday_heatmap
-- Question: when during the week do incidents happen? One cell per
-- weekday and hour, with an index relative to the average cell (1.0 = average).
-- Techniques: GROUP BY on two dimensions, window AVG over the whole result.
DROP VIEW IF EXISTS v_hour_weekday_heatmap;
CREATE VIEW v_hour_weekday_heatmap AS
WITH cells AS (
    SELECT
        d.iso_dow,
        d.day_name,
        f.hour,
        count(*) AS incidents
    FROM fact_incident AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
    GROUP BY d.iso_dow, d.day_name, f.hour
)
SELECT
    iso_dow,
    day_name,
    hour,
    incidents,
    round(incidents / avg(incidents) OVER (), 2) AS index_vs_avg_cell
FROM cells
ORDER BY iso_dow, hour;
