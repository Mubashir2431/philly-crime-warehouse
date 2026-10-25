-- Populate the calendar and hour dimensions for the load window.
-- Parameters: :start_date, :end_date (inclusive ISO dates)
-- A recursive CTE generates one row per day, so no calendar file is needed.

INSERT OR IGNORE INTO dim_date (
    date_key, full_date, year, quarter, month, month_name, month_start,
    day_of_month, iso_dow, day_name, is_weekend
)
WITH RECURSIVE days (d) AS (
    SELECT date(:start_date)
    UNION ALL
    SELECT date(d, '+1 day') FROM days WHERE d < date(:end_date)
),
parts AS (
    SELECT
        d,
        CAST(strftime('%m', d) AS INTEGER) AS m,
        (CAST(strftime('%w', d) AS INTEGER) + 6) % 7 + 1 AS dow  -- %w gives 0 for Sunday
    FROM days
)
SELECT
    CAST(strftime('%Y%m%d', d) AS INTEGER),
    d,
    CAST(strftime('%Y', d) AS INTEGER),
    (m + 2) / 3,
    m,
    CASE m WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
           WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
           WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
           WHEN 10 THEN 'October' WHEN 11 THEN 'November' ELSE 'December' END,
    date(d, 'start of month'),
    CAST(strftime('%d', d) AS INTEGER),
    dow,
    CASE dow WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
             WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday'
             ELSE 'Sunday' END,
    dow IN (6, 7)
FROM parts;

INSERT OR IGNORE INTO dim_hour (hour, hour_label, daypart)
WITH RECURSIVE hours (h) AS (
    SELECT 0 UNION ALL SELECT h + 1 FROM hours WHERE h < 23
)
SELECT
    h,
    printf('%02d:00', h),
    CASE
        WHEN h < 6  THEN 'Overnight'
        WHEN h < 12 THEN 'Morning'
        WHEN h < 18 THEN 'Afternoon'
        ELSE 'Evening'
    END
FROM hours;
