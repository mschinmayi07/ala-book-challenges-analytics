# Phase 1: Python ETL

[Back to the main README](../README.md)

Phase 1 cleans the ALA challenge data (2016-2025) with Python and pandas and models it as a star schema for MySQL and Power BI (Phases 2 and 3). `rapidfuzz` does the fuzzy matching. For cross verification, each script prints sanity checks (row counts, null counts, warnings).

---

## 1. Input data

| File | What it is |
|---|---|
| `Cases-AZSU_2026.xlsx` | One row per **challenge case** (a person or group challenging materials at a library or school). 754 columns, but only 32 have names (the other 722 are "Unnamed"). |
| `combined_final_flagged.csv` | One row per unique **book**. A cleaned version of the ALA titles file, **enriched with Google Books** (author, publisher, ISBN, page count...). |
| `Titles-AZSU_2026.xlsx` | The original ALA titles file. Not used by the scripts and not included in this repo. |

The scripts never modify the raw files. In this repo, `summary` is `NOT_FOUND` on every row of `combined_final_flagged.csv` and `dim_title.csv` (see the [main README](../README.md#3-data-sources)).

## 2. Output tables

| Table | Type | Grain (one row = ...) | Primary key |
|---|---|---|---|
| `fact_case.csv` | Fact | one challenge case | `case_id` |
| `dim_title.csv` | Dimension | one unique book | `row_index` |
| `bridge_case_title.csv` | Bridge (many-to-many) | one title challenged in one case | `bridge_id` (surrogate key) |
| `bridge_case_reason.csv` | Bridge (many-to-many) | one reason given in one case | `(case_id, reason)` |
| `cases_clean_full.csv` | Staging | full cleaned cases, still with the raw comma-separated title lists | `case_id` |

The schema diagram and the reasons for the bridge tables and for using `row_index` instead of ISBN are in the [main README](../README.md#4-architecture).

## 3. Initial build

**Script 01, clean the cases.** Loads the 754-column sheet and repairs the 4 corrupted rows: someone ran Excel's "Split Text to Columns" on a giant title list, so book titles overwrote the year, state, location and counts. It rebuilds each title list from its scattered cells, blanks the metadata that was lost and flags the rows with `data_quality_flag`. It drops the 722 unnamed columns (nearly all stray values in them, 1,712 of 1,727, come from those 4 rows), renames the 32 named columns to snake_case and adds `case_id` (34 columns in the output).

**Script 02, fact table and reasons.** Splits each case's comma-separated `reasons_raw` into `bridge_case_reason` (32 distinct reasons, no typos) and builds `fact_case` without the raw title and reason text.

**Script 03, dimension and title bridge.** Builds `dim_title` from the Google-Books-enriched file. It drops `average_rating` (88% missing) and the per-year case counts, which are derived in SQL from the bridge instead of being stored twice. It creates a normalized `match_key` ("title by author", lowercase, no extra spaces or quotes). It splits each case's title list into one row per title with Python's `csv` module (titles such as "10,000 Dresses" contain commas), assigns each an `outcome` (retained, relocated, restricted, removed, withdrawn, bowdlerized, defaced, destroyed, stolen or unknown), and links it to `dim_title` by exact match, then by fuzzy match (a similarity score from 0 to 100).

First result: 94.7% exact, 4.2% fuzzy, 1.2% unmatched, with 2,004 rows flagged for manual review.

## 4. Issues found and fixes

Cross-checking the outputs exposed several problems that needed more than basic cleaning.

* Title matching: ALA records often list only the first author, while the title dataset contains full author names. I added a first-author matching step, tightened fuzzy-match acceptance to a score of 90 or higher, and kept weaker matches for review.
* Non-book items: Some challenges involved films, magazines, and other materials rather than books. I added classification rules using the case type and title, while preserving records instead of deleting them.
* Corrupted cases: Four rows had been damaged by an Excel text-to-columns operation. I recovered some metadata where values could be matched confidently, inferred years using the Titles file's yearly counts, and flagged the remaining uncertainty. Two nearly identical cases were also identified; one is flagged as a probable duplicate, not deleted.
* Data model and validation: I added a surrogate key to the title bridge, standardized column types, and separated book, non-book, and mixed cases so they can be filtered during analysis. A reconciliation script checks the outputs against the source data's yearly counts.

These changes reduced rows flagged for manual review from 2,004 to 61. The final results and remaining limitations are documented below.

## 5. Final results

| Check | Result |
|---|---|
| Rows | 5,373 cases / 10,380 books / 41,381 title rows / 7,050 reason rows |
| Exact matches | 39,242 `exact` + 1,355 `exact_first_author` + 1 `exact_title_only` (98.1% of rows) |
| Fuzzy | 39 (9 accepted, 30 kept as review candidates) |
| `non_book` / `corrupted_fragment` / `unmatched` | 527 / 186 / 31 |
| Rows with no `row_index` | 774 = 527 + 186 + 31 (744 rows with no match) + 30 weak fuzzy rows (score 60 to 78) that keep a best guess in `candidate_row_index` but no link |
| Rows needing manual review | 61 (0.15%), down from 2,004. All 61 are inside the 774: the 31 unmatched plus the 30 weak fuzzy rows |
| `item_type` | book 40,596 / non_book 538 / unknown 247 (the 538 non_book are the 527 `non_book` rows plus 11 rows that keep a link to a book record: 8 `exact` and 3 `exact_first_author`) |
| ALA Titles file vs `combined_final_flagged.csv` | 10,387 data rows (10,388 sheet rows with the header) vs 10,380. The 7 titles not in the CSV, by `row_index` in the Titles file: 1991 The Young Elites, 2890 I Will Greet the Sun Again, 4623 No Choirboy, 5526 Sofia Valdez, Future Prez, 8220 Love and Other Words, 9083 Too Much and Never Enough, 10000 Mockingbird. These were deliberately not backfilled. In the cases file they show up as 2 `unmatched` rows, 7 weak `fuzzy` rows and 2 `corrupted_fragment` rows |
| Titles in `dim_title` never linked from a case row | 98 (86 have yearly counts above 0 in the Titles file, 12 have none). 10,380 - 98 = 10,282 distinct titles in the bridge |
| `is_book` = False | 5 books |
| `case_scope` | book 4,914 / non_book 285 / mixed 173 / empty 1 |
| Duplicate `bridge_id` | 0 |
| Repeated `(case_id, row_index)` | 1 pair (a book and its "(Film)" variant in the same case) |
| Per-year check vs the Titles file | all cases +0.6%; without the duplicate case -1.1% |
| Per-title check vs the Titles file's yearly columns | about 90% match exactly |

## 6. Column guide for the key helper fields

| Column | Table | Meaning |
|---|---|---|
| `data_quality_flag` | fact_case | `row_corrupted_partial_recovery` or `row_corrupted_metadata_lost` for the 4 damaged rows |
| `year_source` | fact_case | `original` or `inferred_from_titles_file` |
| `case_scope`, `case_scope_source` | fact_case | book / non_book / mixed, and whether it came from `type_format` or was inferred |
| `probable_duplicate_of_case_id` | fact_case | set only for case 4564 |
| `match_method` | bridge_case_title | `exact`, `exact_first_author`, `exact_title_only`, `fuzzy`, `non_book`, `corrupted_fragment`, `unmatched` |
| `match_confidence` | bridge_case_title | similarity score, 0-100 |
| `candidate_row_index` | bridge_case_title | best guess for a weak fuzzy match, for manual review |
| `needs_manual_review` | bridge_case_title | True for weak fuzzy matches and unmatched rows |
| `item_type` | bridge_case_title | book / non_book / unknown. Filter on this for a books-only analysis |
| `outcome` | bridge_case_title | what happened to the title in that case |
| `is_book` | dim_title | False for non-books that slipped into the dimension |
| `match_key`, `match_key_first_author` | dim_title | normalized keys used for matching |

## 7. Known limitations

- **Corrupted rows:** year, state and `type_format` are unknown for the 4 corrupted cases. The year is inferred and the state is empty.
- **Possible duplicate:** case 4564 is flagged, not removed. Later phases exclude it through the `fact_case_deduped` view.
- **Titles that contain commas but are not in quotes** get split into fragments, which is why some weak matches remain for review.
- **Duplicate books in `dim_title`:** 4 pairs share a `match_key`. The lowest `row_index` receives all the bridge rows.
- **`total_cases`** in the Titles file exceeds the sum of its yearly columns for 1,326 titles. The cause is unknown, so it is not used for validation.
- **Case 2373** has no `case_scope`, so a `case_scope = 'book'` filter drops it (3 rows).
