-- v_top_offenses_by_district
-- Question: what are the five most common offense groups in each police
-- district, and what share of that district's incidents does each make up?
-- Techniques: CTE, RANK() partitioned by district, windowed SUM for share.
DROP VIEW IF EXISTS v_top_offenses_by_district;
CREATE VIEW v_top_offenses_by_district AS
WITH counts AS (
    SELECT
        f.district_code,
        o.ucr_group,
        count(*) AS incidents
    FROM fact_incident AS f
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    GROUP BY f.district_code, o.ucr_group
),
ranked AS (
    SELECT
        district_code,
        ucr_group,
        incidents,
        rank() OVER (PARTITION BY district_code ORDER BY incidents DESC) AS offense_rank,
        round(100.0 * incidents / sum(incidents) OVER (PARTITION BY district_code), 1)
            AS pct_of_district
    FROM counts
)
SELECT *
FROM ranked
WHERE offense_rank <= 5
ORDER BY district_code, offense_rank;
