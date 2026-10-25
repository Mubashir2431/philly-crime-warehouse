-- v_grid_hotspots
-- Question: where are the densest clusters of incidents on the map, and what
-- percentile does each grid cell fall in?
-- Techniques: CTE, PERCENT_RANK(), NTILE(), share of total via window SUM.
DROP VIEW IF EXISTS v_grid_hotspots;
CREATE VIEW v_grid_hotspots AS
WITH cells AS (
    SELECT
        f.grid_id,
        count(*) AS incidents,
        count(*) FILTER (WHERE o.category = 'Violent') AS violent
    FROM fact_incident AS f
    JOIN dim_offense AS o ON o.offense_key = f.offense_key
    WHERE f.grid_id IS NOT NULL
    GROUP BY f.grid_id
)
SELECT
    c.grid_id,
    g.center_lat,
    g.center_lon,
    c.incidents,
    c.violent,
    round(100.0 * c.incidents / sum(c.incidents) OVER (), 3) AS pct_of_mapped,
    round(percent_rank() OVER (ORDER BY c.incidents), 4) AS pct_rank,
    ntile(10) OVER (ORDER BY c.incidents) AS decile
FROM cells AS c
JOIN dim_grid AS g ON g.grid_id = c.grid_id
ORDER BY c.incidents DESC;
