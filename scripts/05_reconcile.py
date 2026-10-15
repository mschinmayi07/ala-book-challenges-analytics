"""
Script 5: 05_reconcile.py   (REPORT ONLY - changes nothing)

Compares how many times each book appears in bridge_case_title.csv with the yearly
case counts stored in combined_final_flagged.csv ("# 2016 Cases" ... "# 2025 Cases").
The sum of the yearly columns is the validation reference. The "total_cases" column
is printed for information only (see section 5a).

Reads:  data/clean/bridge_case_title.csv, data/clean/fact_case.csv,
        data/clean/dim_title.csv, data/raw/combined_final_flagged.csv
Writes: nothing (printout only)
Run from the project folder:   python scripts/05_reconcile.py
"""

import re
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
BRIDGE_FILE = CLEAN_DIR / "bridge_case_title.csv"
FACT_FILE = CLEAN_DIR / "fact_case.csv"
TITLES_FILE = PROJECT_DIR / "data" / "raw" / "combined_final_flagged.csv"

YEARS = list(range(2016, 2026))
YEAR_COLUMNS = [f"# {year} Cases" for year in YEARS]


# ---------------------------------------------------------------------------
# Load the files
# ---------------------------------------------------------------------------
bridge = pd.read_csv(BRIDGE_FILE, encoding="utf-8")
bridge["row_index"] = bridge["row_index"].astype("Int64")
fact = pd.read_csv(FACT_FILE, encoding="utf-8")
titles = pd.read_csv(TITLES_FILE, dtype=str, encoding="utf-8")

print(f"bridge_case_title rows: {len(bridge)}   fact_case rows: {len(fact)}   titles file rows: {len(titles)}")

# The counts in the titles file were read as text; convert them to numbers and
# warn (with the row_index) about anything that is not a number instead of hiding it.
titles["row_index"] = titles["row_index"].astype(int)
for column in YEAR_COLUMNS + ["total_cases"]:
    as_number = pd.to_numeric(titles[column], errors="coerce")
    failed = titles[column].notna() & as_number.isna()
    for row_index in titles.loc[failed, "row_index"]:
        print(f"WARNING: row_index {row_index}: '{column}' is not a number: "
              f"{titles.loc[titles['row_index'] == row_index, column].iloc[0]!r}")
    if as_number.isna().sum() > 0:
        print(f"WARNING: '{column}' has {int(as_number.isna().sum())} blank/non-numeric values (treated as 0)")
    titles[column] = as_number.fillna(0).astype(int)

# Only bridge rows that were actually linked to a book can be compared
matched = bridge[bridge["row_index"].notna()].copy()
print(f"Bridge rows with a row_index: {len(matched)} of {len(bridge)}")

# Cases whose year was inferred by Script 4 (the 4 formerly corrupted cases)
inferred_case_ids = fact.loc[fact["year_source"] == "inferred_from_titles_file", "case_id"].tolist()
print(f"Cases with an inferred year: {inferred_case_ids}")


# ===========================================================================
# 1. Per year: bridge vs the sum of the yearly columns
# ===========================================================================
print("\n" + "=" * 70)
print("1. PER YEAR: bridge rows (with a row_index) vs sum of '# YEAR Cases'")
print("=" * 70)


def build_year_table(bridge_rows):
    """Compare bridge rows per year with the titles file; returns a table with an ALL row."""
    with_year = bridge_rows.merge(fact[["case_id", "year"]], on="case_id", how="left")
    no_year = int(with_year["year"].isna().sum())
    per_year = with_year.groupby("year").size()
    table_rows = []
    for year in YEARS:
        bridge_count = int(per_year.get(year, 0))
        titles_count = int(titles[f"# {year} Cases"].sum())
        difference = bridge_count - titles_count
        percent = difference / titles_count * 100 if titles_count > 0 else float("nan")
        table_rows.append({"year": year, "bridge": bridge_count, "titles_file": titles_count,
                           "difference": difference, "pct_difference": round(percent, 1)})
    table = pd.DataFrame(table_rows)
    all_bridge = int(table["bridge"].sum())
    all_titles = int(table["titles_file"].sum())
    table.loc[len(table)] = {"year": "ALL", "bridge": all_bridge, "titles_file": all_titles,
                             "difference": all_bridge - all_titles,
                             "pct_difference": round((all_bridge - all_titles) / all_titles * 100, 1)}
    return table, no_year


year_table, rows_without_year = build_year_table(matched)
print(f"Bridge rows whose case still has no year, left out of the table: {rows_without_year}")
print(year_table.to_string(index=False))

# For comparison: the same table WITHOUT the cases whose year was inferred
year_table_without_inferred, _ = build_year_table(matched[~matched["case_id"].isin(inferred_case_ids)])
all_row = year_table.iloc[-1]
all_row_without = year_table_without_inferred.iloc[-1]
print(f"\nDeficit (bridge - titles file) WITH the inferred years:    {int(all_row['difference'])} "
      f"({all_row['pct_difference']}%)")
print(f"Deficit if the inferred cases were left out:               {int(all_row_without['difference'])} "
      f"({all_row_without['pct_difference']}%)")

# Second table: the same comparison WITHOUT the bridge rows of cases marked as a
# probable duplicate of another case (probable_duplicate_of_case_id is filled)
duplicate_case_ids = fact.loc[fact["probable_duplicate_of_case_id"].notna(), "case_id"].tolist()
print(f"\nCases marked as probable duplicates: {duplicate_case_ids} "
      f"({int(matched['case_id'].isin(duplicate_case_ids).sum())} bridge rows with a row_index)")
year_table_no_duplicates, _ = build_year_table(matched[~matched["case_id"].isin(duplicate_case_ids)])

side_by_side = year_table[["year", "titles_file", "bridge", "difference", "pct_difference"]].copy()
side_by_side["bridge_no_dup"] = year_table_no_duplicates["bridge"]
side_by_side["diff_no_dup"] = year_table_no_duplicates["difference"]
side_by_side["pct_no_dup"] = year_table_no_duplicates["pct_difference"]
print("\nPer-year comparison: all cases vs excluding probable-duplicate cases")
print(side_by_side.to_string(index=False))
all_row_no_dup = year_table_no_duplicates.iloc[-1]

# Do the inferred cases contain the same books? If two of them share almost all
# their books, the titles file may have counted that list only once.
print("\nBook overlap between the inferred cases (shared books / jaccard = shared / all distinct books):")
books_per_case = {case_id: set(matched.loc[matched["case_id"] == case_id, "row_index"].astype(int))
                  for case_id in inferred_case_ids}
most_similar_pair = None
most_similar_jaccard = 0.0
for position, first_case in enumerate(inferred_case_ids):
    for second_case in inferred_case_ids[position + 1:]:
        shared = len(books_per_case[first_case] & books_per_case[second_case])
        all_distinct = len(books_per_case[first_case] | books_per_case[second_case])
        jaccard = shared / all_distinct if all_distinct > 0 else 0.0
        if shared > 0:
            print(f"  cases {first_case} and {second_case}: {shared} shared books, jaccard {jaccard:.2f}")
        if jaccard > most_similar_jaccard:
            most_similar_jaccard = jaccard
            most_similar_pair = (first_case, second_case)
if most_similar_pair is not None:
    smaller_case = min(most_similar_pair, key=lambda case_id: len(books_per_case[case_id]))
    smaller_case_rows = matched[matched["case_id"] == smaller_case]
    smaller_case_year = int(fact.loc[fact["case_id"] == smaller_case, "year"].iloc[0])
    year_difference_now = int(year_table.loc[year_table["year"] == smaller_case_year, "difference"].iloc[0])
    print(f"  Most similar pair: {most_similar_pair} (jaccard {most_similar_jaccard:.2f}). "
          f"The year-{smaller_case_year} difference is now {year_difference_now}; "
          f"case {smaller_case} contributes {len(smaller_case_rows)} of its rows.")


# ===========================================================================
# 2. Per title: bridge count vs the sum of the yearly columns
# ===========================================================================
print("\n" + "=" * 70)
print("2. PER TITLE: bridge count vs sum of the yearly columns (the reference)")
print("=" * 70)

bridge_counts = matched.groupby("row_index").size().rename("bridge_count")
per_title = titles[["row_index", "title", "author", "total_cases"]].copy()
per_title["year_sum"] = titles[YEAR_COLUMNS].sum(axis=1).values   # same row order as titles
per_title = per_title.merge(bridge_counts, left_on="row_index", right_index=True, how="left")
per_title["bridge_count"] = per_title["bridge_count"].fillna(0).astype(int)
per_title["shortfall"] = per_title["year_sum"] - per_title["bridge_count"]   # positive = bridge has fewer

equal = int((per_title["shortfall"] == 0).sum())
fewer = int((per_title["shortfall"] > 0).sum())
more = int((per_title["shortfall"] < 0).sum())
print(f"Titles in the titles file: {len(per_title)}")
print(f"  bridge count == yearly sum: {equal}  ({equal / len(per_title) * 100:.1f}%)")
print(f"  bridge count  < yearly sum: {fewer}  ({fewer / len(per_title) * 100:.1f}%)")
print(f"  bridge count  > yearly sum: {more}  ({more / len(per_title) * 100:.1f}%)")
print(f"Sum of yearly columns: {int(per_title['year_sum'].sum())}   "
      f"sum of bridge counts: {int(per_title['bridge_count'].sum())}")

display_columns = ["row_index", "title", "year_sum", "bridge_count", "shortfall"]


def short(table):
    """Shorten long titles so the printout stays readable."""
    table = table.copy()
    table["title"] = table["title"].str.slice(0, 55)
    return table


print("\n15 biggest SHORTFALLS (yearly sum higher than the bridge):")
biggest_shortfalls = per_title.sort_values("shortfall", ascending=False).head(15)
print(short(biggest_shortfalls[display_columns]).to_string(index=False))

print("\n15 biggest EXCESSES (bridge higher than the yearly sum):")
biggest_excesses = per_title.sort_values("shortfall", ascending=True).head(15)
print(short(biggest_excesses[display_columns]).to_string(index=False))


# ===========================================================================
# 3. Titles that never appear in the bridge
# ===========================================================================
print("\n" + "=" * 70)
print("3. TITLES WITH NO BRIDGE ROWS AT ALL")
print("=" * 70)
never_in_bridge = per_title[per_title["bridge_count"] == 0].sort_values("year_sum", ascending=False)
print(f"{len(never_in_bridge)} row_index values never appear in the bridge "
      f"({int((never_in_bridge['year_sum'] > 0).sum())} of them have a yearly sum > 0).")
print(short(never_in_bridge[["row_index", "title", "year_sum", "total_cases"]]).to_string(index=False))


# ===========================================================================
# 4. For the 10 biggest shortfalls: what spellings are in the bridge?
# ===========================================================================
print("\n" + "=" * 70)
print("4. SPELLING CHECK for the 10 biggest shortfalls")
print("=" * 70)
print("For each title we search the bridge's raw_title_string for the title's first")
print("3 words (case-insensitive) and list what we find.")

titles_with_unlinked_variants = 0
ten_biggest = per_title.sort_values("shortfall", ascending=False).head(10)
for _, book in ten_biggest.iterrows():
    first_three_words = str(book["title"]).split()[:3]
    # Build a search pattern like  the\s+chocolate\s+war  (re.escape makes
    # punctuation safe to search for literally)
    pattern = r"\s+".join(re.escape(word) for word in first_three_words)
    found = bridge[bridge["raw_title_string"].str.contains(pattern, case=False, regex=True, na=False)]
    unlinked_hits = found[found["row_index"].isna()]
    if len(unlinked_hits) > 0:
        titles_with_unlinked_variants += 1

    print(f"\n--- row_index {book['row_index']}: {book['title']!r} by {book['author']!r} "
          f"(yearly sum {book['year_sum']}, bridge count {book['bridge_count']}) ---")
    print(f"Search words: {' '.join(first_three_words)!r}  ->  {len(found)} bridge rows, "
          f"{found['raw_title_string'].nunique()} distinct strings, "
          f"{len(unlinked_hits)} of the rows have no row_index")

    variants = (found.groupby(["raw_title_string", "match_method"], dropna=False)
                .agg(rows=("bridge_id", "size"), row_index=("row_index", "first"))
                .reset_index().sort_values("rows", ascending=False).head(12))
    for _, variant in variants.iterrows():
        linked = variant["row_index"]
        linked_text = "-" if pd.isna(linked) else str(linked)
        print(f"   {variant['rows']:4d} x {variant['raw_title_string'][:70]!r}  "
              f"[{variant['match_method']}, row_index {linked_text}]")


# ===========================================================================
# 5. Other checks
# ===========================================================================
print("\n" + "=" * 70)
print("5. OTHER CHECKS")
print("=" * 70)

# ---- 5a. total_cases: INFORMATIONAL, UNEXPLAINED ----
total_bigger = int((per_title["total_cases"] > per_title["year_sum"]).sum())
total_equal = int((per_title["total_cases"] == per_title["year_sum"]).sum())
total_smaller = int((per_title["total_cases"] < per_title["year_sum"]).sum())
total_gap = int(per_title["total_cases"].sum() - per_title["year_sum"].sum())
print("5a. total_cases vs the yearly sum  [INFORMATIONAL, UNEXPLAINED - not used for any conclusion]")
print(f"    equal: {total_equal}   total_cases bigger: {total_bigger}   total_cases smaller: {total_smaller}")
print(f"    sum of total_cases: {int(per_title['total_cases'].sum())}   "
      f"sum of yearly columns: {int(per_title['year_sum'].sum())}   gap: {total_gap}")
per_title["shortfall_vs_total"] = per_title["total_cases"] - per_title["bridge_count"]
print(f"    bridge vs total_cases: {int((per_title['shortfall_vs_total'] == 0).sum())} equal, "
      f"{int((per_title['shortfall_vs_total'] > 0).sum())} fewer, "
      f"{int((per_title['shortfall_vs_total'] < 0).sum())} more")

# ---- 5b. The 10 biggest remaining shortfalls ----
print("\n5b. 10 biggest shortfalls against the yearly sum (same as section 2, with total_cases for information):")
ten_table = ten_biggest[["row_index", "title", "year_sum", "total_cases", "bridge_count", "shortfall"]].copy()
ten_table["title"] = ten_table["title"].str.slice(0, 40)
print(ten_table.to_string(index=False))

# ---- 5c. Duplicate books in the dimension split their counts ----
dim = pd.read_csv(CLEAN_DIR / "dim_title.csv", dtype=str, encoding="utf-8")
dim["row_index"] = dim["row_index"].astype(int)
dim = dim.merge(per_title[["row_index", "year_sum", "bridge_count"]], on="row_index")
duplicate_group = dim[dim["match_key"].duplicated(keep=False)].sort_values(["match_key", "row_index"])
print("\n5c. Dimension rows that share a match_key (lowest row_index receives all the bridge rows):")
print(duplicate_group[["row_index", "match_key", "year_sum", "bridge_count"]]
      .assign(match_key=lambda df: df["match_key"].str.slice(0, 50)).to_string(index=False))
for key, group in duplicate_group.groupby("match_key"):
    print(f"    {key[:50]!r}: combined yearly sum {int(group['year_sum'].sum())}, "
          f"combined bridge {int(group['bridge_count'].sum())}")

# ---- 5d. Titles in the dimension that look like Excel turned them into a date ----
looks_like_date = dim["title"].str.match(r"^\d{4}-\d{2}-\d{2}", na=False)
print(f"\n5d. dim_title titles that look like an Excel date instead of a name: {int(looks_like_date.sum())}")
if looks_like_date.sum() > 0:
    print(dim.loc[looks_like_date, ["row_index", "title", "author", "year_sum", "bridge_count"]].to_string(index=False))


# ===========================================================================
# SUMMARY (every number below is computed from the data above)
# ===========================================================================
worst_year_row = year_table.iloc[:-1].sort_values("difference").iloc[0]      # most negative difference
best_year_row = year_table.iloc[:-1].sort_values("difference").iloc[-1]      # most positive difference
# the year with the largest absolute difference once the duplicate case(s) are excluded
no_dup_by_size = year_table_no_duplicates.iloc[:-1].assign(
    size=lambda table: table["difference"].abs()).sort_values("size", ascending=False)
no_dup_worst_year = no_dup_by_size.iloc[0]
biggest_shortfall = per_title.sort_values("shortfall", ascending=False).iloc[0]
ten_shortfall_total = int(ten_biggest["shortfall"].sum())

print("\n" + "=" * 70)
print("SUMMARY (nothing was changed)")
print("=" * 70)
print(f"""
Reference used: the sum of the yearly columns ('# 2016 Cases' ... '# 2025 Cases').

1. Per year (section 1). Including the inferred years of cases {inferred_case_ids}, the bridge has
   {int(all_row['bridge'])} linked rows against {int(all_row['titles_file'])} in the titles file: a difference of
   {int(all_row['difference'])} ({all_row['pct_difference']}%). Without the inferred cases the difference would be
   {int(all_row_without['difference'])} ({all_row_without['pct_difference']}%). The largest single-year difference is
   {int(worst_year_row['year'])}: {int(worst_year_row['difference'])} ({worst_year_row['pct_difference']}%). The largest overshoot is
   {int(best_year_row['year'])}: +{int(best_year_row['difference'])} ({best_year_row['pct_difference']}%).
   The two most similar inferred cases are {most_similar_pair} with a jaccard overlap of
   {most_similar_jaccard:.2f} (see section 1). Excluding the probable-duplicate case(s)
   {duplicate_case_ids}, the bridge has {int(all_row_no_dup['bridge'])} linked rows and the difference to the titles
   file is {int(all_row_no_dup['difference'])} ({all_row_no_dup['pct_difference']}%); the biggest single-year
   difference becomes {int(no_dup_worst_year['year'])}: {int(no_dup_worst_year['difference'])} ({no_dup_worst_year['pct_difference']}%).

2. Per title (section 2). {equal} of {len(per_title)} titles ({equal / len(per_title) * 100:.1f}%) have exactly the
   yearly-sum count in the bridge; {fewer} have fewer and {more} have more. The biggest shortfall is
   {biggest_shortfall['title']!r} ({int(biggest_shortfall['shortfall'])}); the 10 biggest shortfalls add up to
   {ten_shortfall_total} rows. {len(never_in_bridge)} titles never appear in the bridge.

3. Spelling check (section 4). For {titles_with_unlinked_variants} of the 10 biggest shortfall titles, the search
   found at least one bridge row with no row_index (a possible spelling variant or
   fragment). For the other {10 - titles_with_unlinked_variants}, every matching row is already linked to a book, so
   the gap is not a matching problem.

4. total_cases (section 5a) is larger than the sum of the yearly columns for {total_bigger} titles
   (total {int(per_title['total_cases'].sum())} vs {int(per_title['year_sum'].sum())}, a gap of {total_gap}). The cause is unknown, so
   total_cases is NOT used for validation; only the yearly columns are.

5. Dimension issues (section 5): {duplicate_group['match_key'].nunique()} groups of books share a match_key in dim_title
   (the lowest row_index takes all the bridge rows), and {int(looks_like_date.sum())} titles look like Excel dates.
""")
