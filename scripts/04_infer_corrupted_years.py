"""
Script 4: 04_infer_corrupted_years.py

The 4 corrupted cases have no year (it was overwritten in the Excel sheet). But the
Titles file says how many cases each book had in each year, and we know how many
times each book appears in the OTHER (healthy) cases. So for each corrupted case:

    leftover for a book in a year = Titles-file count - count in healthy cases
                                    (never below 0)

Adding up the leftovers over all of the case's books shows which year the case
most likely belongs to. The year is computed here, never typed in by hand.

Reads:   data/clean/bridge_case_title.csv, data/clean/fact_case.csv,
         data/raw/combined_final_flagged.csv
Updates: data/clean/fact_case.csv (year and year_source for accepted cases only)
Run from the project folder:   python scripts/04_infer_corrupted_years.py
"""

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths and settings
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
BRIDGE_FILE = CLEAN_DIR / "bridge_case_title.csv"
FACT_FILE = CLEAN_DIR / "fact_case.csv"
TITLES_FILE = PROJECT_DIR / "data" / "raw" / "combined_final_flagged.csv"

YEARS = list(range(2016, 2026))

# A year is accepted only if BOTH are true:
MIN_SHARE_OF_TOTAL = 0.80       # it holds at least 80% of all the leftover
MIN_SHARE_OF_BOOKS = 0.50       # its leftover is at least 50% of the matched books in the case

# Two corrupted cases whose book lists overlap at least this much (Jaccard) are
# treated as the same list entered twice
MIN_JACCARD_FOR_DUPLICATE = 0.90

# A case with no type_format gets case_scope "book" (or "non_book") if at least
# this share of its book / non_book bridge rows has that item_type
MIN_SHARE_FOR_SCOPE = 0.90

# ...AND at least this share of ALL the case's rows (unknown rows included) must be
# book / non_book rows, so a scope is never based on a small minority of the case
MIN_COVERAGE_FOR_SCOPE = 0.50

# ---------------------------------------------------------------------------
# Load the files
# ---------------------------------------------------------------------------
print("=" * 70)
print("Load files")
print("=" * 70)

bridge = pd.read_csv(BRIDGE_FILE, encoding="utf-8")
bridge["row_index"] = bridge["row_index"].astype("Int64")

# fact_case is read as TEXT (empty cell = missing, nothing else), so that when we
# write it back every value looks exactly as before (no 2023 -> 2023.0 problems).
fact = pd.read_csv(FACT_FILE, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")
fact["case_id"] = fact["case_id"].astype(int)

titles = pd.read_csv(TITLES_FILE, dtype=str, encoding="utf-8")
titles["row_index"] = titles["row_index"].astype(int)
print(f"bridge rows: {len(bridge)}   fact_case rows: {len(fact)}   titles rows: {len(titles)}")

# Convert the yearly columns of the Titles file to numbers. Anything that is not
# a number gets a printed warning with its row_index (and is counted as 0).
for year in YEARS:
    column = f"# {year} Cases"
    as_number = pd.to_numeric(titles[column], errors="coerce")
    failed = titles[column].notna() & as_number.isna()
    for row_index in titles.loc[failed, "row_index"]:
        print(f"WARNING: row_index {row_index}: '{column}' is not a number: "
              f"{titles.loc[titles['row_index'] == row_index, column].iloc[0]!r}")
    if as_number.isna().sum() > 0:
        print(f"WARNING: '{column}' has {int(as_number.isna().sum())} blank/non-numeric values (treated as 0)")
    titles[column] = as_number.fillna(0).astype(int)

# Titles-file counts as a lookup: titles_by_year[row_index][year]
titles_counts = titles.set_index("row_index")[[f"# {year} Cases" for year in YEARS]]
titles_counts.columns = YEARS

# ---------------------------------------------------------------------------
# Which cases are corrupted, and how often does each book appear in the healthy cases?
# ---------------------------------------------------------------------------
is_corrupted = fact["data_quality_flag"].fillna("").str.startswith("row_corrupted")
corrupted_case_ids = fact.loc[is_corrupted, "case_id"].tolist()
print(f"Corrupted cases: {corrupted_case_ids}")

# Healthy bridge rows that are linked to a book, with the case's year added
healthy_rows = bridge[bridge["row_index"].notna() & ~bridge["case_id"].isin(corrupted_case_ids)].copy()
fact_years = fact[["case_id", "year"]].copy()
fact_years["year"] = pd.to_numeric(fact_years["year"], errors="coerce")
healthy_rows = healthy_rows.merge(fact_years, on="case_id", how="left")
rows_without_year = healthy_rows["year"].isna().sum()
if rows_without_year > 0:
    print(f"WARNING: {int(rows_without_year)} healthy bridge rows have no year and cannot be counted")
healthy_rows = healthy_rows.dropna(subset=["year"])
healthy_rows["year"] = healthy_rows["year"].astype(int)

# How many healthy bridge rows does each (book, year) have?
healthy_counts = healthy_rows.groupby(["row_index", "year"]).size()
print(f"Healthy bridge rows used: {len(healthy_rows)}")


# ===========================================================================
# Infer the year for each corrupted case
# ===========================================================================
print("\n" + "=" * 70)
print("Infer years")
print("=" * 70)

fact_before = fact[is_corrupted][["case_id", "year", "year_source", "state", "type_format",
                                  "probable_duplicate_of_case_id", "data_quality_flag"]].copy()
for case_id in corrupted_case_ids:
    # 1. The distinct books this case is linked to
    case_rows = bridge[(bridge["case_id"] == case_id) & bridge["row_index"].notna()]
    book_ids = sorted(case_rows["row_index"].astype(int).unique().tolist())

    # 2 + 3. For each year, add up (Titles-file count - healthy-case count) over the
    # books, never letting a single book go below 0
    leftover_per_year = {}
    for year in YEARS:
        year_total = 0
        for book_id in book_ids:
            if book_id not in titles_counts.index:
                continue    # (cannot happen: every row_index was checked against dim_title)
            titles_file_count = int(titles_counts.loc[book_id, year])
            healthy_count = int(healthy_counts.get((book_id, year), 0))
            year_total = year_total + max(0, titles_file_count - healthy_count)
        leftover_per_year[year] = year_total

    total_leftover = sum(leftover_per_year.values())
    print(f"\ncase_id {case_id}: {len(book_ids)} matched books, total leftover {total_leftover}")
    print(f"  {'year':>6s} {'leftover':>9s} {'share':>7s}")
    for year in YEARS:
        share = leftover_per_year[year] / total_leftover * 100 if total_leftover > 0 else 0
        print(f"  {year:6d} {leftover_per_year[year]:9d} {share:6.1f}%")

    # 4. Pick the year with the largest leftover, but only if it is convincing
    if total_leftover == 0:
        print(f"  WARNING: case_id {case_id}: no leftover at all, year stays blank")
        continue
    best_year = max(leftover_per_year, key=leftover_per_year.get)
    best_sum = leftover_per_year[best_year]
    share_of_total = best_sum / total_leftover
    share_of_books = best_sum / len(book_ids)
    print(f"  Best year {best_year}: {share_of_total * 100:.1f}% of the leftover "
          f"(needs >= {MIN_SHARE_OF_TOTAL * 100:.0f}%), leftover is {share_of_books * 100:.1f}% of the matched "
          f"books (needs >= {MIN_SHARE_OF_BOOKS * 100:.0f}%)")

    if share_of_total >= MIN_SHARE_OF_TOTAL and share_of_books >= MIN_SHARE_OF_BOOKS:
        # 5. Accept: write the year (as text, to match how the file is stored)
        fact.loc[fact["case_id"] == case_id, "year"] = str(best_year)
        fact.loc[fact["case_id"] == case_id, "year_source"] = "inferred_from_titles_file"
        print(f"  ACCEPTED: year {best_year}")
    else:
        print(f"  WARNING: case_id {case_id}: no year is convincing enough, year stays blank")

# state, type_format and data_quality_flag are deliberately left as they were


# ===========================================================================
# Find corrupted cases that repeat another corrupted case's title list
# ===========================================================================
print("\n" + "=" * 70)
print("Probable duplicates among the corrupted cases")
print("=" * 70)

# The distinct books (row_index values) linked to each corrupted case
books_by_case = {}
for case_id in corrupted_case_ids:
    case_books = bridge.loc[(bridge["case_id"] == case_id) & bridge["row_index"].notna(), "row_index"]
    books_by_case[case_id] = set(case_books.astype(int))

flag_by_case = dict(zip(fact["case_id"], fact["data_quality_flag"]))
LOST_FLAG = "row_corrupted_metadata_lost"

for position, first_case in enumerate(corrupted_case_ids):
    for second_case in corrupted_case_ids[position + 1:]:
        shared = len(books_by_case[first_case] & books_by_case[second_case])
        all_distinct = len(books_by_case[first_case] | books_by_case[second_case])
        jaccard = shared / all_distinct if all_distinct > 0 else 0.0
        if jaccard < MIN_JACCARD_FOR_DUPLICATE:
            continue

        # Which case is the duplicate? The one with LESS recovered metadata:
        #   1. the one flagged 'metadata_lost' (the other was partly recovered)
        #   2. if both have the same flag, the one with fewer matched books
        #   3. if still tied, the one with the higher case_id
        first_is_lost = flag_by_case[first_case] == LOST_FLAG
        second_is_lost = flag_by_case[second_case] == LOST_FLAG
        if first_is_lost != second_is_lost:
            duplicate_case = first_case if first_is_lost else second_case
            reason = "it is flagged 'row_corrupted_metadata_lost' and the other one is not"
        elif len(books_by_case[first_case]) != len(books_by_case[second_case]):
            duplicate_case = min(first_case, second_case, key=lambda case_id: len(books_by_case[case_id]))
            reason = "it has fewer matched books"
        else:
            duplicate_case = max(first_case, second_case)
            reason = "the flags and book counts are equal, so the higher case_id is used"
        original_case = first_case if duplicate_case == second_case else second_case

        print(f"Pair {first_case} and {second_case}: {shared} shared books of {all_distinct} distinct "
              f"(Jaccard {jaccard:.2f}, threshold {MIN_JACCARD_FOR_DUPLICATE})")
        existing = fact.loc[fact["case_id"] == duplicate_case, "probable_duplicate_of_case_id"].iloc[0]
        if pd.notna(existing):
            print(f"  WARNING: case {duplicate_case} is already marked as a duplicate of {existing}; not changed")
            continue
        fact.loc[fact["case_id"] == duplicate_case, "probable_duplicate_of_case_id"] = str(original_case)
        print(f"  -> case {duplicate_case} marked probable_duplicate_of_case_id = {original_case}, "
              f"because {reason}")
        print(f"     (flags: case {first_case} = {flag_by_case[first_case]}, "
              f"case {second_case} = {flag_by_case[second_case]})")

# Nothing else about the duplicate case is changed or removed.


# ===========================================================================
# Fill case_scope for cases that have no type_format
# ===========================================================================
print("\n" + "=" * 70)
print("case_scope for cases with no type_format")
print("=" * 70)

# Without a type_format these cases have no case_scope, so a 'case_scope = book'
# filter would drop all their rows. We look at what the case's rows are instead.
scope_counts_before = fact["case_scope"].value_counts(dropna=False).to_dict()
empty_scope_case_ids = fact.loc[fact["case_scope"].isna(), "case_id"].tolist()
print(f"Cases with an empty case_scope: {len(empty_scope_case_ids)} {empty_scope_case_ids}")

scope_table_rows = []
for case_id in empty_scope_case_ids:
    case_rows = bridge[bridge["case_id"] == case_id]
    book_rows = int((case_rows["item_type"] == "book").sum())
    non_book_rows = int((case_rows["item_type"] == "non_book").sum())
    unknown_rows = int((case_rows["item_type"] == "unknown").sum())

    # Only book / non_book rows vote ('decided' rows); unknown rows do not vote,
    # but they count in the total, which is used for the coverage test below
    decided_rows = book_rows + non_book_rows
    total_rows = decided_rows + unknown_rows
    inferred_scope = None
    if decided_rows > 0:
        book_share = book_rows / decided_rows
        non_book_share = non_book_rows / decided_rows
        coverage = decided_rows / total_rows
        # BOTH tests must pass: the share of one type among decided rows (90%)
        # and the coverage of decided rows among all rows (50%)
        if coverage >= MIN_COVERAGE_FOR_SCOPE:
            if book_share >= MIN_SHARE_FOR_SCOPE:
                inferred_scope = "book"
            elif non_book_share >= MIN_SHARE_FOR_SCOPE:
                inferred_scope = "non_book"
        shown_share = f"{max(book_share, non_book_share) * 100:.1f}% {'book' if book_share >= non_book_share else 'non_book'}"
        shown_coverage = f"{coverage * 100:.1f}%"
    else:
        shown_share = "no book/non_book rows"
        shown_coverage = "0.0%"

    if inferred_scope is not None:
        fact.loc[fact["case_id"] == case_id, "case_scope"] = inferred_scope
        fact.loc[fact["case_id"] == case_id, "case_scope_source"] = "inferred_from_titles"
    scope_table_rows.append({"case_id": case_id, "book": book_rows, "non_book": non_book_rows,
                             "unknown": unknown_rows, "share": shown_share,
                             "coverage": shown_coverage,
                             "result": inferred_scope if inferred_scope is not None else "left empty"})

print(pd.DataFrame(scope_table_rows).to_string(index=False))

# Before / after for the corrupted rows
fact_after = fact[is_corrupted][["case_id", "year", "year_source", "state", "type_format",
                                 "probable_duplicate_of_case_id", "data_quality_flag"]]
print("\nBEFORE:")
print(fact_before.to_string(index=False))
print("\nAFTER:")
print(fact_after.to_string(index=False))

# Save (same text values as before for every other cell)
fact.to_csv(FACT_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {FACT_FILE}")


# ===========================================================================
# FINAL CHECKS
# ===========================================================================
print("\n" + "=" * 70)
print("FINAL CHECKS")
print("=" * 70)

saved = pd.read_csv(FACT_FILE, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")

# 5. The corrupted cases
print("5.  Corrupted cases: inferred year, year_source, bridge rows with a row_index")
for case_id in corrupted_case_ids:
    row = saved[saved["case_id"] == str(case_id)].iloc[0]
    linked = int(((bridge["case_id"] == case_id) & bridge["row_index"].notna()).sum())
    print(f"      case_id {case_id}: year {row['year']}, year_source {row['year_source']}, "
          f"bridge rows with a row_index: {linked}")

# 6. fact_case checks
decimal_values = 0
for column in saved.columns:
    decimal_values += int(saved[column].str.fullmatch(r"-?\d+\.0", na=False).sum())
corrupted_saved = saved[saved["data_quality_flag"].fillna("").str.startswith("row_corrupted")]
print(f"6.  fact_case: {len(saved)} rows (expected 5373); values like '12.0': {decimal_values} (should be 0); "
      f"'year_source' present: {'year_source' in saved.columns}")
print(f"    adversarial_group_flag still blank for the corrupted rows: "
      f"{bool(corrupted_saved['adversarial_group_flag'].isna().all())}")
print("    year_source values:")
print(saved["year_source"].value_counts(dropna=False).to_string())
print(f"    rows with a blank year: {int(saved['year'].isna().sum())}")
marked = saved[saved["probable_duplicate_of_case_id"].notna()]
print(f"    probable_duplicate_of_case_id is filled for {len(marked)} rows: "
      f"{dict(zip(marked['case_id'], marked['probable_duplicate_of_case_id']))} "
      f"(corrupted cases only: {bool(marked['case_id'].astype(int).isin(corrupted_case_ids).all())})")
print("3.  case_scope (before this script -> after):")
print(f"      before: {scope_counts_before}")
print(f"      after:  {saved['case_scope'].value_counts(dropna=False).to_dict()}")
print(f"    case_scope_source after: {saved['case_scope_source'].value_counts(dropna=False).to_dict()}")
print(f"    case_scope value counts (expected book 4914 / non_book 285 / mixed 173 / empty 1): "
      f"book {int((saved['case_scope'] == 'book').sum())} / non_book {int((saved['case_scope'] == 'non_book').sum())} / "
      f"mixed {int((saved['case_scope'] == 'mixed').sum())} / empty {int(saved['case_scope'].isna().sum())}")
empty_scope_left = saved.loc[saved["case_scope"].isna(), "case_id"].tolist()
print(f"    cases still empty: {empty_scope_left}; their case_scope_source is empty: "
      f"{bool(saved.loc[saved['case_scope'].isna(), 'case_scope_source'].isna().all())}")

# 4. No bridge row in a non_book-scope case may be a 'book' (uses the updated case_scope)
scope_by_case = dict(zip(saved["case_id"].astype(int), saved["case_scope"]))
bridge_scope = bridge["case_id"].map(scope_by_case)
violations = bridge[(bridge_scope == "non_book") & (bridge["item_type"] == "book")]
print(f"4.  Bridge rows with item_type 'book' in a 'non_book'-scope case: {len(violations)} (must be 0)")
print(f"    item_type counts: {bridge['item_type'].value_counts().to_dict()} "
      f"(previous round: book 40604 / non_book 530 / unknown 247)")
book_rows_in_scope_book = int(((bridge_scope == 'book') & (bridge['item_type'] == 'book')).sum())
print(f"    'book' rows now reachable with a case_scope = 'book' filter: {book_rows_in_scope_book} "
      f"of {int((bridge['item_type'] == 'book').sum())}")

# 8. Scripts 2 and 3 must not define NON_BOOK_TYPE_FORMATS any more (config.py does)
for script_name in ["02_build_fact_and_reasons.py", "03_build_dim_title_and_bridge.py"]:
    script_lines = (Path(__file__).resolve().parent / script_name).read_text(encoding="utf-8").splitlines()
    definitions = [line for line in script_lines if line.strip().startswith("NON_BOOK_TYPE_FORMATS =")]
    print(f"8.  {script_name}: lines defining NON_BOOK_TYPE_FORMATS = {len(definitions)} (must be 0)")

# 7. Row counts for every output file
print("7.  Row counts of every output file:")
for file_name in ["cases_clean_full.csv", "fact_case.csv", "bridge_case_reason.csv",
                  "dim_title.csv", "bridge_case_title.csv"]:
    file_rows = len(pd.read_csv(CLEAN_DIR / file_name, dtype=str, keep_default_na=False, encoding="utf-8"))
    print(f"      {file_name:26s} {file_rows:7d}")

# ---------------------------------------------------------------------------
# Plain-English summary
# ---------------------------------------------------------------------------
accepted = saved[saved["year_source"] == "inferred_from_titles_file"]
print("\n" + "-" * 70)
print("SUMMARY")
print(f"Script 4 looked at {len(corrupted_case_ids)} corrupted cases. For each, it compared the Titles "
      f"file's yearly counts with how often each of the case's books appears in healthy cases, and "
      f"accepted a year only when one year held at least {MIN_SHARE_OF_TOTAL * 100:.0f}% of the leftover. "
      f"{len(accepted)} cases got an inferred year ({dict(zip(accepted['case_id'], accepted['year']))}). "
      f"It also compared the cases' book lists and marked {len(marked)} probable duplicate "
      f"({dict(zip(marked['case_id'], marked['probable_duplicate_of_case_id']))}); "
      f"fact_case.csv still has {len(saved)} rows.")
print("-" * 70)
