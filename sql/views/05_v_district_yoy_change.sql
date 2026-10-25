-- v_district_yoy_change
-- Question: which districts improved or worsened the most compared with the
-- previous year, overall and for violent offenses?
-- Techniques: CTE, LAG() partitioned by district, RANK() on percent change.
DROP VIEW IF EXISTS v_district_yoy_change;
CREATE VIEW v_district_yoy_change AS
WITH yearly AS (
    SELECT
        f.district_code,
        d.year,
        count(*) AS incidents,
        count(*) FILTER (WHERE o.category = 'Violent') AS violent
    FROM fact_incident AS f
    JOIN dim_date    AS d ON d.date_key = f.date_key
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    GROUP BY f.district_code, d.year
),
changes AS (
    SELECT
        district_code,
        year,
        incidents,
        lag(incidents) OVER (PARTITION BY district_code ORDER BY year) AS prev_incidents,
        violent,
        lag(violent) OVER (PARTITION BY district_code ORDER BY year) AS prev_violent
    FROM yearly
)
SELECT
    district_code,
    year,
    incidents,
    prev_incidents,
    round(100.0 * (incidents - prev_incidents) / nullif(prev_incidents, 0), 1) AS pct_change,
    violent,
    prev_violent,
    round(100.0 * (violent - prev_violent) / nullif(prev_violent, 0), 1) AS violent_pct_change,
    rank() OVER (
        PARTITION BY year
        ORDER BY 1.0 * (incidents - prev_incidents) / nullif(prev_incidents, 0)
    ) AS improvement_rank  -- 1 = largest decline
FROM changes
WHERE prev_incidents IS NOT NULL
ORDER BY year, improvement_rank;
