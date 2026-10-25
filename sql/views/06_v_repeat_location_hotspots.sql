-- v_repeat_location_hotspots
-- Question: which street blocks keep showing up year after year? A repeat
-- hotspot is a block with incidents in every year of the window, ranked by volume.
-- Techniques: several CTEs, COUNT(DISTINCT), ROW_NUMBER() to pick the most common
-- district and offense per block (SQLite has no MODE()), DENSE_RANK() for the ranking,
-- and a running SUM() window for the cumulative share of all incidents.
DROP VIEW IF EXISTS v_repeat_location_hotspots;
CREATE VIEW v_repeat_location_hotspots AS
WITH span AS (
    SELECT count(DISTINCT d.year) AS n_years
    FROM fact_incident AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
),
blocks AS (
    SELECT
        f.location_block,
        count(*) AS incidents,
        count(DISTINCT d.year) AS years_present,
        count(DISTINCT d.month_start) AS months_present,
        round(avg(f.lat), 5) AS lat,
        round(avg(f.lon), 5) AS lon
    FROM fact_incident AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
    WHERE f.location_block IS NOT NULL
    GROUP BY f.location_block
),
block_offense AS (
    SELECT
        f.location_block,
        o.ucr_group,
        row_number() OVER (
            PARTITION BY f.location_block ORDER BY count(*) DESC, o.ucr_group
        ) AS rn
    FROM fact_incident AS f
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    WHERE f.location_block IS NOT NULL
    GROUP BY f.location_block, o.ucr_group
),
block_district AS (
    SELECT
        location_block,
        district_code,
        row_number() OVER (
            PARTITION BY location_block ORDER BY count(*) DESC, district_code
        ) AS rn
    FROM fact_incident
    WHERE location_block IS NOT NULL
    GROUP BY location_block, district_code
)
SELECT
    dense_rank() OVER (ORDER BY b.incidents DESC) AS hotspot_rank,
    b.location_block,
    bd.district_code,
    b.incidents,
    b.years_present,
    b.months_present,
    bo.ucr_group AS most_common_offense,
    round(100.0 * sum(b.incidents) OVER (
              ORDER BY b.incidents DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
          ) / (SELECT count(*) FROM fact_incident), 2) AS cumulative_pct_of_city,
    b.lat,
    b.lon
FROM blocks AS b
CROSS JOIN span AS s
JOIN block_offense  AS bo ON bo.location_block = b.location_block AND bo.rn = 1
JOIN block_district AS bd ON bd.location_block = b.location_block AND bd.rn = 1
WHERE b.years_present = s.n_years
ORDER BY b.incidents DESC
LIMIT 100;
