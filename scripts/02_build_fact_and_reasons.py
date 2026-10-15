"""
Script 2: 02_build_fact_and_reasons.py

Step 1 - Build bridge_case_reason.csv  (one row per case_id + reason).
Step 2 - Build fact_case.csv           (case table without the title-list / reason text).

Reads:  data/clean/cases_clean_full.csv   (created by Script 1)
Run from the project folder:   python scripts/02_build_fact_and_reasons.py
"""

from pathlib import Path

import pandas as pd

from config import BOOK_TYPE_FORMATS, NON_BOOK_TYPE_FORMATS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
INPUT_FILE = CLEAN_DIR / "cases_clean_full.csv"
BRIDGE_REASON_FILE = CLEAN_DIR / "bridge_case_reason.csv"
FACT_FILE = CLEAN_DIR / "fact_case.csv"

# Columns that stay in the fact table, in this order
FACT_COLUMNS = [
    "case_id", "year", "year_source", "type_format", "case_scope", "case_scope_source",
    "state", "location", "initiator",
    "count_titles_challenged", "count_retained", "count_bowdlerized",
    "count_defaced", "count_destroyed", "count_stolen",
    "count_withdrawn_removed", "count_relocated", "count_restricted",
    "count_removed_required", "count_children_titles", "count_ya_titles",
    "count_censored", "count_known_outcomes",
    "adversarial_group_involvement", "adversarial_group_flag",
    "rated_on_target_list", "rated_on_target_flag",
    "probable_duplicate_of_case_id", "data_quality_flag",
]

# These columns are created in Step 2, so they are not in the input file
NEW_FLAG_COLUMNS = ["year_source", "case_scope", "case_scope_source", "adversarial_group_flag",
                    "rated_on_target_flag", "probable_duplicate_of_case_id"]

# case_scope: which kind of items a case is about (the type_format lists live in config.py).
#   "book"     - type_format in BOOK_TYPE_FORMATS
#   "non_book" - type_format in NON_BOOK_TYPE_FORMATS (films, music, magazines...): no books at all
#   "mixed"    - every other type_format (Display, Hate Crime, Program...): can hold books
#   empty      - type_format is empty (9 cases; Script 4 may fill some of them in)

# ---------------------------------------------------------------------------
# Load the cleaned cases from Script 1
# ---------------------------------------------------------------------------
cases = pd.read_csv(INPUT_FILE, encoding="utf-8")
print(f"Loaded {INPUT_FILE.name}: {len(cases)} rows x {len(cases.columns)} columns")


# ===========================================================================
# STEP 1 - bridge_case_reason.csv
# ===========================================================================
print("\n" + "=" * 70)
print("STEP 1: Build bridge_case_reason")
print("=" * 70)

# Before: how many cases actually have a reason listed?
cases_with_reason = cases["reasons_raw"].notna().sum()
print(f"Cases with a reasons_raw value:    {cases_with_reason}")
print(f"Cases with NO reasons_raw (blank): {len(cases) - cases_with_reason}  "
      f"(these get no rows in the bridge)")

# Keep only case_id and the reasons text, and skip cases with no reasons
reasons = cases[["case_id", "reasons_raw"]].dropna(subset=["reasons_raw"]).copy()

# Split "A,B" into a list ["A", "B"], then explode() turns each list item into
# its own row (case_id is repeated for each reason)
reasons["reason"] = reasons["reasons_raw"].str.split(",")
bridge_reason = reasons.explode("reason")

# Remove spaces around each reason, e.g. " Racism" -> "Racism"
bridge_reason["reason"] = bridge_reason["reason"].str.strip()

# An empty piece (e.g. from "A,,B" or a trailing comma) is not a real reason.
# Warn with the case_id instead of dropping it quietly.
empty_pieces = bridge_reason["reason"] == ""
for case_id in bridge_reason.loc[empty_pieces, "case_id"]:
    print(f"WARNING: case_id {case_id} has an empty reason piece in reasons_raw; skipping it")
bridge_reason = bridge_reason[~empty_pieces]

# The same reason listed twice for one case would double count later.
duplicates = bridge_reason.duplicated(subset=["case_id", "reason"], keep="first")
if duplicates.sum() > 0:
    print(f"WARNING: {duplicates.sum()} duplicate (case_id, reason) pairs found; keeping the first of each")
    print(bridge_reason[duplicates].to_string(index=False))
bridge_reason = bridge_reason[~duplicates]

# Keep only the two bridge columns
bridge_reason = bridge_reason[["case_id", "reason"]].reset_index(drop=True)

# After: sanity checks
print(f"\nBridge rows: {len(bridge_reason)}  "
      f"(from {bridge_reason['case_id'].nunique()} distinct cases)")
print(f"Average reasons per case (among cases with reasons): "
      f"{len(bridge_reason) / bridge_reason['case_id'].nunique():.2f}")

# Print every distinct reason with how often it appears, so typos stand out
reason_counts = bridge_reason["reason"].value_counts()
print(f"\n{len(reason_counts)} unique reason values (check these for typos / near-duplicates):")
for reason, count in reason_counts.items():
    print(f"  {count:5d}  {reason!r}")

bridge_reason.to_csv(BRIDGE_REASON_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {BRIDGE_REASON_FILE}")


# ===========================================================================
# STEP 2 - fact_case.csv
# ===========================================================================
print("\n" + "=" * 70)
print("STEP 2: Build fact_case")
print("=" * 70)

# Before: show which columns are about to be left out
dropped_columns = [col for col in cases.columns if col not in FACT_COLUMNS]
print(f"Columns before: {len(cases.columns)}")
print(f"Columns being dropped ({len(dropped_columns)}): {dropped_columns}")

# The derived columns (year_source and the two flags) are built below, so a
# "missing column" check must not expect them in the input file
columns_from_input = [col for col in FACT_COLUMNS if col not in NEW_FLAG_COLUMNS]

# Stop loudly if a column we want to keep is missing
missing = [col for col in columns_from_input if col not in cases.columns]
if missing:
    raise KeyError(f"Expected these columns in cases_clean_full.csv but did not find: {missing}")

# Start from the columns we keep from the input
fact_case = cases[columns_from_input].copy()

# The counts, year and rated_on_target_list were saved with blanks, so pandas may
# have read them as decimals (e.g. 2023.0). Convert them to whole numbers that
# allow blanks. astype("Int64") raises an error if a value has a real decimal part,
# so nothing is rounded silently.
number_columns = [col for col in columns_from_input
                  if col in ("year", "rated_on_target_list") or col.startswith("count_")]
for column in number_columns:
    fact_case[column] = fact_case[column].astype("Int64")

# year_source says where each year came from. Every year in the sheet is
# "original"; years that Script 4 infers later get "inferred_from_titles_file".
# A blank year gets a blank year_source.
fact_case["year_source"] = pd.NA
fact_case.loc[fact_case["year"].notna(), "year_source"] = "original"

# case_scope: work out book / non_book / mixed from type_format
case_scopes = []
for type_format in fact_case["type_format"]:
    if pd.isna(type_format):
        case_scopes.append(pd.NA)
    elif type_format in BOOK_TYPE_FORMATS:
        case_scopes.append("book")
    elif type_format in NON_BOOK_TYPE_FORMATS:
        case_scopes.append("non_book")
    else:
        case_scopes.append("mixed")
fact_case["case_scope"] = case_scopes

# case_scope_source says where the scope came from: "type_format" here. For the
# cases with no type_format it stays empty (Script 4 may infer a scope later
# and then writes "inferred_from_titles").
fact_case["case_scope_source"] = pd.NA
fact_case.loc[fact_case["case_scope"].notna(), "case_scope_source"] = "type_format"

print("\ncase_scope values:")
print(fact_case["case_scope"].value_counts(dropna=False).to_string())

# probable_duplicate_of_case_id: empty for every case for now. Script 4 fills it
# for a corrupted case that repeats another corrupted case's title list.
fact_case["probable_duplicate_of_case_id"] = pd.array([pd.NA] * len(fact_case), dtype="Int64")

# adversarial_group_flag: 1 if a group name is listed, 0 if the cell is empty.
# A cell with only spaces counts as empty. For the corrupted rows the cell was
# blanked because the value is UNKNOWN, so we leave the flag blank (NULL) there
# instead of claiming "no group".
group_text = fact_case["adversarial_group_involvement"].fillna("").astype(str).str.strip()
fact_case["adversarial_group_flag"] = (group_text != "").astype("Int64")
is_corrupted = fact_case["data_quality_flag"].notna()
fact_case.loc[is_corrupted, "adversarial_group_flag"] = pd.NA
print(f"\nadversarial_group_flag left blank for {int(is_corrupted.sum())} corrupted rows (value unknown)")

# rated_on_target_flag: 1 if the case has at least one title on a target list,
# 0 if the count is 0, blank if the count is unknown
fact_case["rated_on_target_flag"] = pd.NA
fact_case["rated_on_target_flag"] = fact_case["rated_on_target_flag"].astype("Int64")
fact_case.loc[fact_case["rated_on_target_list"] > 0, "rated_on_target_flag"] = 1
fact_case.loc[fact_case["rated_on_target_list"] == 0, "rated_on_target_flag"] = 0

# Put the columns in the final order
fact_case = fact_case[FACT_COLUMNS]
print("\nadversarial_group_flag values:")
print(fact_case["adversarial_group_flag"].value_counts(dropna=False).to_string())
print("\nrated_on_target_flag values:")
print(fact_case["rated_on_target_flag"].value_counts(dropna=False).to_string())

# After: sanity checks
print(f"Columns after:  {len(fact_case.columns)}")
print(f"Rows in:  {len(cases)}   Rows out: {len(fact_case)}   "
      f"(should be equal: {len(cases) == len(fact_case)})")
print(f"case_id unique? {fact_case['case_id'].is_unique}")
print("\nNull count per column:")
print(fact_case.isna().sum().to_string())

fact_case.to_csv(FACT_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {FACT_FILE}")

# Check the saved file: no whole number should be written like "2023.0"
saved_text = pd.read_csv(FACT_FILE, dtype=str, encoding="utf-8")
decimal_whole_numbers = 0
for column in saved_text.columns:
    looks_like_float = saved_text[column].str.fullmatch(r"-?\d+\.0", na=False)
    if looks_like_float.sum() > 0:
        print(f"WARNING: column '{column}' has {int(looks_like_float.sum())} values like '12.0'")
        decimal_whole_numbers += int(looks_like_float.sum())
print(f"Values written like '12.0' in {FACT_FILE.name}: {decimal_whole_numbers} (should be 0)")

# ---------------------------------------------------------------------------
# Plain-English summary
# ---------------------------------------------------------------------------
print("\n" + "-" * 70)
print("SUMMARY")
print(f"Script 2 read {INPUT_FILE.name} ({len(cases)} rows). It split the comma-separated "
      f"Reasons text into {len(bridge_reason)} (case_id, reason) rows covering "
      f"{bridge_reason['case_id'].nunique()} cases and {len(reason_counts)} distinct reasons "
      f"-> {BRIDGE_REASON_FILE.name}. It then removed the {len(dropped_columns)} title-list and "
      f"reasons text columns to make {FACT_FILE.name} with {len(fact_case)} rows x "
      f"{len(fact_case.columns)} columns (same row count as the input).")
print("-" * 70)
