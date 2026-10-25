-- v_offense_risers_fallers
-- Question: comparing the latest full year with the year before, which offense
-- groups rose or fell the most, in raw count and in percent?
-- Techniques: CTEs, conditional aggregation with FILTER, scalar subquery for the
-- latest year, RANK() in both directions over the same change column.
DROP VIEW IF EXISTS v_offense_risers_fallers;
CREATE VIEW v_offense_risers_fallers AS
WITH latest AS (
    SELECT max(d.year) AS y
    FROM fact_incident AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
),
by_group AS (
    SELECT
        o.ucr_group,
        o.category,
        count(*) FILTER (WHERE d.year = (SELECT y FROM latest) - 1) AS prior_year,
        count(*) FILTER (WHERE d.year = (SELECT y FROM latest))     AS latest_year
    FROM fact_incident AS f
    JOIN dim_date    AS d ON d.date_key = f.date_key
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    GROUP BY o.ucr_group, o.category
)
SELECT
    (SELECT y FROM latest) AS latest_year_label,
    ucr_group,
    category,
    prior_year,
    latest_year,
    latest_year - prior_year AS change,
    round(100.0 * (latest_year - prior_year) / nullif(prior_year, 0), 1) AS pct_change,
    rank() OVER (ORDER BY latest_year - prior_year DESC) AS riser_rank,
    rank() OVER (ORDER BY latest_year - prior_year ASC)  AS faller_rank
FROM by_group
WHERE prior_year >= 50   -- ignore tiny groups where percent change is noise
ORDER BY change DESC;
