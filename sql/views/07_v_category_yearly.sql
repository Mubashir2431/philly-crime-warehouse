-- v_category_yearly
-- Question: how do violent, property, and other offenses trend by year, and
-- what share of each category involves a firearm?
-- Techniques: CTE, conditional aggregation with FILTER, LAG(), windowed share.
DROP VIEW IF EXISTS v_category_yearly;
CREATE VIEW v_category_yearly AS
WITH yearly AS (
    SELECT
        d.year,
        o.category,
        count(*) AS incidents,
        count(*) FILTER (WHERE o.involves_firearm) AS firearm_incidents
    FROM fact_incident AS f
    JOIN dim_date    AS d ON d.date_key = f.date_key
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    GROUP BY d.year, o.category
)
SELECT
    year,
    category,
    incidents,
    round(100.0 * incidents / sum(incidents) OVER (PARTITION BY year), 1) AS pct_of_year,
    round(100.0 * (incidents - lag(incidents) OVER c) / nullif(lag(incidents) OVER c, 0), 1)
        AS yoy_pct_change,
    firearm_incidents,
    round(100.0 * firearm_incidents / nullif(incidents, 0), 1) AS pct_firearm
FROM yearly
WINDOW c AS (PARTITION BY category ORDER BY year)
ORDER BY year, category;
