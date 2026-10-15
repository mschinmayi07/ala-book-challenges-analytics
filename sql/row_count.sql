-- row_count.sql : row count of each table in banned_books_db.
-- Expected: dim_title 10,380 | fact_case 5,373 | bridge_case_title 41,381 | bridge_case_reason 7,050
-- (counts the base tables, so fact_case is 5,373 even though fact_case_deduped has 5,372)
SELECT 'dim_title' AS table_name, COUNT(*) AS row_count FROM dim_title
UNION ALL
SELECT 'fact_case', COUNT(*) FROM fact_case
UNION ALL
SELECT 'bridge_case_title', COUNT(*) FROM bridge_case_title
UNION ALL
SELECT 'bridge_case_reason', COUNT(*) FROM bridge_case_reason;
