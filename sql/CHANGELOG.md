# SQL changes after the first Phase 2 delivery

Reviewed 2026-10-06. Everything below was re-tested on a scratch MySQL 8.0.46 server: the loader runs, all four row counts match the CSVs, and all 13 queries plus `row_count.sql` execute.

## sql/schema.sql

| Change | Detail |
|---|---|
| Added `DROP VIEW IF EXISTS fact_case_deduped;` | First statement in the file (before the table drops), so the file stays re-runnable. |
| Added `CREATE VIEW fact_case_deduped` | Last statement: `SELECT * FROM fact_case WHERE probable_duplicate_of_case_id IS NULL`. Returns 5,372 of 5,373 cases; the only case excluded is 4564 (probable duplicate of 3939). |

Tables, columns, keys and indexes are unchanged.

## sql/queries.sql

Queries now read from the view `fact_case_deduped`, so case 4564 is excluded:

| Query | Change | Effect on results |
|---|---|---|
| 1a, 1b | `INNER JOIN fact_case_deduped` added | Counts drop slightly (e.g. "All Boys Aren't Blue" 309 to 308, "The Bluest Eye" 247 to 246). |
| 3 | `fact_case` replaced by `fact_case_deduped` | None: same winners as before. |
| 5a, 5b | `fact_case` replaced by `fact_case_deduped` | Counts and rates shift slightly. |
| 6 | `fact_case` replaced by `fact_case_deduped` | Counts and percentages shift slightly. |
| 7 | Added a second `LEFT JOIN fact_case_deduped`; `COUNT(b.bridge_id)` became `COUNT(c.case_id)` | None in current data; titles still listed with 0 if all their challenges were in excluded cases. |
| 9a, 9b | `INNER JOIN fact_case_deduped` added; columns now qualified with `b.` | None: none of the 61 review rows belong to case 4564. |
| 10 | `FROM fact_case` replaced by `FROM fact_case_deduped` | None: case 4564 has a NULL flag and was already excluded. |

Unchanged (still on `fact_case`): queries 2, 4 and 8. Case 4564 does not affect them: its counts are NULL (2, 8) and it has no reason rows (4).

In total the view removes 738 `bridge_case_title` rows (all of case 4564's).

## sql/row_count.sql (new)

One `UNION ALL` query returning the row count of each of the four tables.
Expected: dim_title 10,380; fact_case 5,373; bridge_case_title 41,381; bridge_case_reason 7,050.
It counts the base tables, not the view, so fact_case still shows 5,373.

## Comment updates (done)

- `queries.sql` header, queries 2 and 8: notes now say that queries 1, 3, 5, 6, 7, 9 and 10 exclude case 4564 via the view, and that 2, 4 and 8 read `fact_case` but are unaffected by it.
- `schema.sql`: header mentions the view, and the view has its own comment (including that `SELECT *` means the file should be re-run if `fact_case` gains columns).
- `row_count.sql`: header comment with the expected counts.
