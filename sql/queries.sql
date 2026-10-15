-- =====================================================================
-- queries.sql  --  banned_books_db
-- Run with banned_books_db selected. Every statement uses only the
-- course-approved SQL vocabulary.
--
-- Notes that apply to several queries:
--   * ROUND() is not on the approved list, so percentages are rounded with
--     CAST(... AS DECIMAL(p,2) ), which rounds to 2 decimal places in MySQL.
--   * Counting "times challenged" = counting rows in bridge_case_title
--     (one row per title per case). Only rows with a non-NULL row_index can
--     be joined to dim_title, so non-book and unmatched rows drop out of
--     every query that joins to dim_title.
--   * DUPLICATE-CASE NOTE: case_id 4564 is flagged
--     probable_duplicate_of_case_id = 3939 and may be double-counting 2023.
--     The view fact_case_deduped (see schema.sql) leaves it out, and queries
--     1, 3, 5, 6, 7, 9 and 10 read from that view instead of fact_case.
--     Queries 2, 4 and 8 still read fact_case, but case 4564 cannot affect
--     them (NULL counts, no reason rows). To include 4564 again anywhere,
--     swap fact_case_deduped back to fact_case in that query.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1a. Top 15 most-challenged titles overall
-- Grouped by row_index (not just title text) because many different books share
-- a title, e.g. ten different books called "Abortion".
-- ---------------------------------------------------------------------
SELECT t.row_index,
       t.title,
       t.author,
       COUNT(*) AS times_challenged
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped f ON f.case_id = b.case_id
INNER JOIN dim_title AS t ON b.row_index = t.row_index
GROUP BY t.row_index, t.title, t.author
ORDER BY times_challenged DESC, t.title
LIMIT 15;


-- ---------------------------------------------------------------------
-- 1b. Top 15 most-challenged authors overall
-- (rows with a NULL author are skipped)
-- ---------------------------------------------------------------------
SELECT t.author,
       COUNT(*) AS times_challenged
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped f ON f.case_id = b.case_id
INNER JOIN dim_title AS t ON b.row_index = t.row_index
WHERE t.author IS NOT NULL
GROUP BY t.author
ORDER BY times_challenged DESC, t.author
LIMIT 15;


-- ---------------------------------------------------------------------
-- 2. Year-over-year % change in total titles challenged
-- NOTE: case_id 4564 is flagged probable_duplicate_of_case_id = 3939 and may be
-- double-counting 2023's totals. This query reads fact_case directly (not the
-- deduped view), but it makes no difference: both 4564 and 3939 are "corrupted"
-- rows whose count_titles_challenged is NULL, so they add nothing to this SUM.
-- The duplicate only mattered for queries that count bridge_case_title rows
-- (1, 3, 5, 6, 7, 9): 3939 has 731 bridge rows and 4564 has 738, and those
-- queries now exclude 4564 through fact_case_deduped.
-- The first year has no previous year, so its pct_change is NULL.
-- ---------------------------------------------------------------------
WITH yearly AS (
    SELECT year,
           SUM(count_titles_challenged) AS total_titles
    FROM fact_case
    GROUP BY year
),
with_prev AS (
    SELECT year,
           total_titles,
           LAG(total_titles) OVER (ORDER BY year) AS prev_year_total
    FROM yearly
)
SELECT year,
       total_titles,
       prev_year_total,
       CAST(100.0 * (total_titles - prev_year_total) / NULLIF(prev_year_total, 0)
            AS DECIMAL(10,2)) AS pct_change
FROM with_prev
ORDER BY year;


-- ---------------------------------------------------------------------
-- 3. Most-challenged title in each year
-- Counted per book (row_index) so same-named books don't merge. RANK() keeps
-- ties, so a year can return more than one row.
-- The window result can't be filtered in the same SELECT, hence the 2nd CTE.
-- ---------------------------------------------------------------------
WITH title_counts AS (
    SELECT c.year,
           t.row_index,
           t.title,
           t.author,
           COUNT(*) AS times_challenged
    FROM bridge_case_title AS b
    INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
    INNER JOIN dim_title AS t ON b.row_index = t.row_index
    GROUP BY c.year, t.row_index, t.title, t.author
),
ranked AS (
    SELECT year,
           row_index,
           title,
           author,
           times_challenged,
           RANK() OVER (PARTITION BY year ORDER BY times_challenged DESC) AS rnk
    FROM title_counts
)
SELECT year, title, author, times_challenged, rnk
FROM ranked
WHERE rnk = 1
ORDER BY year, title;


-- ---------------------------------------------------------------------
-- 4. Reason frequency by year (number of cases citing each reason)
-- ---------------------------------------------------------------------
SELECT c.year,
       r.reason,
       COUNT(*) AS case_count
FROM bridge_case_reason AS r
INNER JOIN fact_case AS c ON r.case_id = c.case_id
GROUP BY c.year, r.reason
ORDER BY c.year, case_count DESC, r.reason;


-- ---------------------------------------------------------------------
-- 5a. Initiator type vs. outcome (counts of challenged-title rows)
-- ---------------------------------------------------------------------
SELECT COALESCE(c.initiator, '(missing)') AS initiator,
       b.outcome,
       COUNT(*) AS title_rows
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
GROUP BY c.initiator, b.outcome
ORDER BY initiator, title_rows DESC;


-- ---------------------------------------------------------------------
-- 5b. Retained-rate per initiator
-- retained_pct         = retained rows / ALL rows for that initiator (as specified)
-- retained_pct_known   = retained rows / rows whose outcome is not 'unknown'
--                        (about half of all rows are 'unknown', which drags the
--                        first measure down, so both are shown)
-- ---------------------------------------------------------------------
SELECT COALESCE(c.initiator, '(missing)') AS initiator,
       COUNT(*) AS title_rows,
       SUM(CASE WHEN b.outcome = 'retained' THEN 1 ELSE 0 END) AS retained_rows,
       CAST(100.0 * SUM(CASE WHEN b.outcome = 'retained' THEN 1 ELSE 0 END) / COUNT(*)
            AS DECIMAL(5,2)) AS retained_pct,
       CAST(100.0 * SUM(CASE WHEN b.outcome = 'retained' THEN 1 ELSE 0 END)
            / NULLIF(SUM(CASE WHEN b.outcome <> 'unknown' THEN 1 ELSE 0 END), 0)
            AS DECIMAL(5,2)) AS retained_pct_known
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
GROUP BY c.initiator
ORDER BY retained_pct DESC;


-- ---------------------------------------------------------------------
-- 6. Fiction vs. non-fiction share of challenges per year
-- Titles with no fiction/non-fiction value show as 'Unknown' and are part of
-- the denominator, so each year's percentages add to 100.
-- ---------------------------------------------------------------------
WITH yearly_counts AS (
    SELECT c.year,
           COALESCE(t.fiction_or_nonfiction, 'Unknown') AS category,
           COUNT(*) AS challenges
    FROM bridge_case_title AS b
    INNER JOIN dim_title AS t ON b.row_index = t.row_index
    INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
    GROUP BY c.year, COALESCE(t.fiction_or_nonfiction, 'Unknown')
)
SELECT year,
       category,
       challenges,
       CAST(100.0 * challenges / SUM(challenges) OVER (PARTITION BY year)
            AS DECIMAL(5,2)) AS pct_of_year
FROM yearly_counts
ORDER BY year, category;


-- ---------------------------------------------------------------------
-- 7. Titles rated MATURE by Google Books but shelved as Children's
-- LEFT JOIN keeps a title even if it has no challenge rows (it would show 0).
-- ---------------------------------------------------------------------
SELECT t.row_index,
       t.title,
       t.author,
       t.maturity_rating,
       t.age_group,
       COUNT(c.case_id) AS times_challenged
FROM dim_title AS t
LEFT JOIN bridge_case_title AS b ON t.row_index = b.row_index
LEFT JOIN fact_case_deduped AS c ON b.case_id = c.case_id
WHERE t.maturity_rating = 'MATURE'
  AND t.age_group = 'Children'
GROUP BY t.row_index, t.title, t.author, t.maturity_rating, t.age_group
ORDER BY times_challenged DESC, t.title;


-- ---------------------------------------------------------------------
-- 8. Total titles challenged per state, with running cumulative total
-- NOTE: case_id 4564 is flagged probable_duplicate_of_case_id = 3939 and may be
-- double-counting 2023's totals. This query reads fact_case directly (not the
-- deduped view), but as with query 2 both rows have NULL count_titles_challenged
-- (and NULL state), so neither affects these sums at all.
-- A NULL state (4 corrupted cases) and the code 'INT' (international) each
-- appear as their own group. state is added to the window ORDER BY only to
-- make the running total deterministic when two states tie.
-- ---------------------------------------------------------------------
WITH state_totals AS (
    SELECT state,
           SUM(count_titles_challenged) AS total_titles
    FROM fact_case
    GROUP BY state
)
SELECT state,
       total_titles,
       SUM(total_titles) OVER (ORDER BY total_titles DESC, state
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_total
FROM state_totals
ORDER BY total_titles DESC, state;


-- ---------------------------------------------------------------------
-- 9a. Rows needing manual review, by match_method (where review effort sits)
-- ---------------------------------------------------------------------
SELECT b.match_method,
       COUNT(*) AS rows_needing_review
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
WHERE b.needs_manual_review = TRUE
GROUP BY b.match_method
ORDER BY rows_needing_review DESC;


-- ---------------------------------------------------------------------
-- 9b. The actual rows behind 9a (so you can work through them)
-- ---------------------------------------------------------------------
SELECT b.match_method,
       b.bridge_id,
       b.case_id,
       b.raw_title_string,
       b.row_index,
       b.candidate_row_index,
       b.match_confidence
FROM bridge_case_title AS b
INNER JOIN fact_case_deduped AS c ON b.case_id = c.case_id
WHERE b.needs_manual_review = TRUE
ORDER BY b.match_method, b.match_confidence DESC, b.bridge_id;


-- ---------------------------------------------------------------------
-- 10. Cases rated on the target list vs. not
-- removal_rate_pct = SUM(count_withdrawn_removed) / SUM(count_titles_challenged).
-- The 7 corrupted cases (NULL flag) are excluded by IS NOT NULL.
-- ---------------------------------------------------------------------
SELECT CASE WHEN rated_on_target_flag = TRUE THEN 'On target list'
            ELSE 'Not on target list' END AS target_group,
       COUNT(*) AS cases,
       AVG(count_titles_challenged) AS avg_titles_per_case,
       SUM(count_withdrawn_removed) AS total_removed,
       SUM(count_titles_challenged) AS total_titles,
       CAST(100.0 * SUM(count_withdrawn_removed) / NULLIF(SUM(count_titles_challenged), 0)
            AS DECIMAL(5,2)) AS removal_rate_pct
FROM fact_case_deduped
WHERE rated_on_target_flag IS NOT NULL
GROUP BY rated_on_target_flag
ORDER BY rated_on_target_flag DESC;
