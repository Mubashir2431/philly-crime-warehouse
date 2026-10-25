-- v_monthly_trend
-- Question: how is citywide incident volume moving month to month, and how
-- does each month compare with the same month a year earlier?
-- Techniques: CTE, LAG(12) for year over year, rolling 3-month window average.
DROP VIEW IF EXISTS v_monthly_trend;
CREATE VIEW v_monthly_trend AS
WITH monthly AS (
    SELECT
        d.month_start,
        count(*) AS incidents,
        count(*) FILTER (WHERE o.category = 'Violent') AS violent,
        count(*) FILTER (WHERE o.category = 'Property') AS property
    FROM fact_incident AS f
    JOIN dim_date    AS d ON d.date_key = f.date_key
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    GROUP BY d.month_start
)
SELECT
    month_start,
    incidents,
    violent,
    property,
    round(avg(incidents) OVER w3, 1) AS rolling_3mo_avg,
    lag(incidents, 12) OVER w AS incidents_prior_year,
    round(100.0 * (incidents - lag(incidents, 12) OVER w)
          / nullif(lag(incidents, 12) OVER w, 0), 1) AS yoy_pct_change
FROM monthly
WINDOW
    w  AS (ORDER BY month_start),
    w3 AS (ORDER BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
ORDER BY month_start;
