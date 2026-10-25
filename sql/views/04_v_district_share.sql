-- v_district_share
-- Question: what share of citywide incidents does each district account for
-- each year, how dense is it per square mile, and where does it rank?
-- Techniques: CTE, SUM() OVER (PARTITION BY year), RANK(), join to district area.
DROP VIEW IF EXISTS v_district_share;
CREATE VIEW v_district_share AS
WITH yearly AS (
    SELECT
        d.year,
        f.district_code,
        count(*) AS incidents
    FROM fact_incident AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
    GROUP BY d.year, f.district_code
)
SELECT
    y.year,
    y.district_code,
    ds.district_name,
    y.incidents,
    round(100.0 * y.incidents / sum(y.incidents) OVER (PARTITION BY y.year), 2)
        AS pct_of_city,
    round(y.incidents / nullif(ds.area_sq_mi, 0), 1) AS incidents_per_sq_mi,
    rank() OVER (PARTITION BY y.year ORDER BY y.incidents DESC) AS volume_rank
FROM yearly AS y
JOIN dim_district AS ds ON ds.district_code = y.district_code
ORDER BY y.year, volume_rank;
