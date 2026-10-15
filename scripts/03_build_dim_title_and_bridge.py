"""
Script 3: 03_build_dim_title_and_bridge.py

Step 1 - Build dim_title.csv         (one row per unique book, primary key row_index).
Step 2 - Build bridge_case_title.csv (one row per case_id + challenged title, with the
                                      outcome and the matching row_index in dim_title).

Reads:  data/raw/combined_final_flagged.csv
        data/clean/cases_clean_full.csv   (created by Script 1)
Needs:  pip install rapidfuzz
Run from the project folder:   python scripts/03_build_dim_title_and_bridge.py
"""

import csv
import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from config import MIXED_TYPE_FORMATS, NON_BOOK_TITLE_MARKER, NON_BOOK_TYPE_FORMATS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
TITLES_FILE = PROJECT_DIR / "data" / "raw" / "combined_final_flagged.csv"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
CASES_FILE = CLEAN_DIR / "cases_clean_full.csv"
FACT_FILE = CLEAN_DIR / "fact_case.csv"
DIM_TITLE_FILE = CLEAN_DIR / "dim_title.csv"
BRIDGE_TITLE_FILE = CLEAN_DIR / "bridge_case_title.csv"
BRIDGE_REASON_FILE = CLEAN_DIR / "bridge_case_reason.csv"

# Columns removed from the dimension table (see issue #4). The case counts will
# be calculated from the bridge tables in SQL instead.
COLUMNS_TO_DROP_FROM_DIM = ["average_rating", "total_cases"] + [
    f"# {year} Cases" for year in range(2016, 2026)
]

# The dim file already has matching columns from the Google Books step. Rename
# them so they cannot be confused with the bridge table's own match columns.
DIM_COLUMNS_TO_RENAME = {
    "match_method": "gb_match_method",
    "match_confidence": "gb_match_confidence",
    "needs_manual_review": "gb_needs_manual_review",
}

# Outcome name -> the column in cases_clean_full.csv that lists those titles
OUTCOME_COLUMNS = {
    "retained": "titles_retained_raw",
    "relocated": "titles_relocated_raw",
    "restricted": "titles_restricted_raw",
    "removed_required": "titles_removed_required_raw",
    "withdrawn_removed": "titles_withdrawn_removed_raw",
    "bowdlerized": "titles_bowdlerized_raw",
    "defaced": "titles_defaced_raw",
    "destroyed": "titles_destroyed_raw",
    "stolen": "titles_stolen_raw",
}

# Fuzzy matching thresholds (0-100 scale)
MIN_FUZZY_SCORE = 60      # below this, we say "unmatched"
ACCEPT_FUZZY_SCORE = 90   # fuzzy matches at or above this are accepted automatically

# The type_format lists (NON_BOOK_TYPE_FORMATS, MIXED_TYPE_FORMATS) and the
# title-marker pattern live in config.py, shared with script 2.

# Numbers from the PREVIOUS version of this script, kept so we can print before/after
BEFORE_FIX = {
    "total": 41381, "exact": 39242, "exact_first_author": 1355, "exact_title_only": 1,
    "fuzzy": 39, "unmatched": 31, "non_book": 527, "corrupted_fragment": 186, "review": 61,
}
BEFORE_ITEM_TYPE = {"book": 40604, "non_book": 530, "unknown": 247}
BEFORE_IS_BOOK_FALSE = 3


# ---------------------------------------------------------------------------
# Small helper functions
# ---------------------------------------------------------------------------
# Curly quotes are replaced with straight ones so "Uncle Bobby’s Wedding" and
# "Uncle Bobby's Wedding" end up identical.
CURLY_QUOTES_TO_STRAIGHT = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def normalize(text):
    """Lowercase, make quotes straight, squeeze extra spaces, and strip
    spaces and quote marks from both ends of the text."""
    text = str(text).translate(CURLY_QUOTES_TO_STRAIGHT)
    text = text.lower()
    text = " ".join(text.split())      # turns any run of spaces/tabs/newlines into one space
    text = text.strip(" \"'")          # removes spaces and quote marks at the start and end
    return text


def split_title_list(cell_text):
    """Split a comma-separated title list into a list of titles.

    A plain text.split(",") breaks titles that contain a comma. The Excel
    export wraps those titles in double quotes (e.g. "10,000 Dresses by Marcus
    Ewert"), so we use Python's built-in csv reader, which understands quotes.
    skipinitialspace=True ignores a space right after a comma (the rebuilt title
    lists in the 4 corrupted rows were joined with ', ').
    """
    if pd.isna(cell_text):
        return []
    try:
        pieces = next(csv.reader([str(cell_text)], skipinitialspace=True))
    except csv.Error:
        # Extremely malformed text: fall back to a plain comma split
        print(f"WARNING: could not parse as quoted CSV, used plain comma split: {str(cell_text)[:60]!r}")
        pieces = str(cell_text).split(",")
    # Trim spaces and throw away empty pieces
    return [piece.strip() for piece in pieces if piece.strip() != ""]


def split_at_last_by(raw_title):
    """Split 'Some Title by Some Author' at the LAST ' by '.
    Returns (title_part, author_part); author_part is None if there is no ' by '."""
    text = raw_title.strip(" \"'")
    if " by " not in text:
        return text, None
    title_part, author_part = text.rsplit(" by ", 1)
    return title_part.strip(" \"'"), author_part.strip(" \"'")


def remove_trailing_by_na(raw_title):
    """Remove a trailing ' by N/A' (any capitalisation), e.g.
    'The Holy Bible by N/A' -> 'The Holy Bible'. ALA writes 'N/A' when a book has
    no author, and dim_title has no author for those books either."""
    text = raw_title.strip(" \"'")
    return re.sub(r"\s+by\s+n/a$", "", text, flags=re.IGNORECASE)


def remove_placeholder_author(raw_title):
    """If the title ends with ' by N/A' or ' by BOOK' (any capitalisation), return
    the title WITHOUT that ending. Otherwise return None.
    ALA uses N/A and BOOK as placeholders when a book's author is unknown."""
    text = raw_title.strip(" \"'")
    stripped = re.sub(r"\s+by\s+(n/a|book)$", "", text, flags=re.IGNORECASE)
    if stripped == text:
        return None
    return stripped


def make_matching_key(raw_title):
    """The text we compare with dim_title: ' by N/A' removed, then normalized.
    raw_title_string itself is never changed."""
    return normalize(remove_trailing_by_na(raw_title))


def non_book_author_part(raw_title):
    """If this ALA entry looks like a non-book (a display, magazine, film,
    program...), return the author part that gave it away. Otherwise return None.

    Rule: the part after the last ' by ' is entirely UPPERCASE (e.g. 'DISPLAY',
    'MAGAZINE'), or it is the same as the part before ' by ' (e.g.
    'HATE CRIME by HATE CRIME'). 'N/A' and 'BOOK' are the uppercase values we do
    NOT treat as a non-book: they are placeholders used when the author is unknown
    ('The Holy Bible by N/A', "Merriam-Webster's Elementary Dictionary by BOOK").
    """
    title_part, author_part = split_at_last_by(raw_title)
    if author_part is None or author_part == "":
        return None
    if author_part.upper() in ("N/A", "BOOK"):
        return None
    if author_part.isupper():
        return author_part
    if normalize(author_part) == normalize(title_part):
        return author_part
    return None


# ===========================================================================
# STEP 1 - dim_title.csv
# ===========================================================================
print("=" * 70)
print("STEP 1: Build dim_title")
print("=" * 70)

# Read EVERYTHING as text so isbn (leading zeros, multiple ISBNs in one cell)
# is never turned into a number. Empty cells become NaN.
dim_title = pd.read_csv(TITLES_FILE, dtype=str, encoding="utf-8")
print(f"Rows loaded:    {len(dim_title)}")
print(f"Columns loaded: {len(dim_title.columns)}")

# row_index is the primary key, so it must be present, whole-number, and unique
if dim_title["row_index"].isna().any():
    raise ValueError("row_index has blank values; it cannot be the primary key")
if not dim_title["row_index"].str.fullmatch(r"\d+").all():
    bad_rows = dim_title.index[~dim_title["row_index"].str.fullmatch(r"\d+")].tolist()
    raise ValueError(f"row_index is not a whole number in CSV rows {bad_rows}")
if dim_title["row_index"].duplicated().any():
    raise ValueError("row_index has duplicate values; it cannot be the primary key")
dim_title["row_index"] = dim_title["row_index"].astype(int)
print(f"row_index is unique, whole numbers, from {dim_title['row_index'].min()} "
      f"to {dim_title['row_index'].max()}")

# Hand fix: in the source file, Excel turned the book title "November 9"
# (Colleen Hoover) into the date 2026-11-09, so row_index 6155 has the title
# '2026-11-09 0:00:00'. We restore the real title BEFORE the match keys are
# built. The fix is only applied if the title still starts with '2026-11-09',
# so it can never overwrite a different title by accident.
print("\nHand fix for row_index 6155 (title damaged by Excel):")
fix_mask = dim_title["row_index"] == 6155
if fix_mask.sum() != 1:
    print("  WARNING: row_index 6155 not found exactly once; no fix applied")
else:
    print("  BEFORE:", dim_title.loc[fix_mask, ["row_index", "title", "author"]].iloc[0].to_dict())
    if str(dim_title.loc[fix_mask, "title"].iloc[0]).startswith("2026-11-09"):
        dim_title.loc[fix_mask, "title"] = "November 9"
    else:
        print("  WARNING: title does not start with '2026-11-09'; no fix applied")
    print("  AFTER: ", dim_title.loc[fix_mask, ["row_index", "title", "author"]].iloc[0].to_dict())

# year was stored as text like "2015.0". Convert it to a whole number that allows
# blanks (Int64). Values that are not a whole number (e.g. "1997-2007", "2 AD",
# "????") cannot be stored in a number column, so we print a warning with the
# row_index and the original text for each one before they become blank.
# (gb_match_confidence and the bridge's match_confidence are real decimal scores
# and are NOT touched.)
year_as_number = pd.to_numeric(dim_title["year"], errors="coerce")
year_not_a_number = dim_title["year"].notna() & year_as_number.isna()
year_has_decimals = year_as_number.notna() & (year_as_number % 1 != 0)
for position in dim_title.index[year_not_a_number | year_has_decimals]:
    print(f"WARNING: row_index {dim_title.loc[position, 'row_index']}: year {dim_title.loc[position, 'year']!r} "
          f"is not a whole number, so it becomes blank")
year_as_number[year_has_decimals] = pd.NA
dim_title["year"] = year_as_number.astype("Int64")
print(f"year converted to Int64: {int(dim_title['year'].notna().sum())} values, "
      f"{int(dim_title['year'].isna().sum())} blank "
      f"({int((year_not_a_number | year_has_decimals).sum())} of the blanks were non-numeric text)")

# Drop the unusable / duplicated-information columns
dim_title = dim_title.drop(columns=COLUMNS_TO_DROP_FROM_DIM)
print(f"Columns after dropping {len(COLUMNS_TO_DROP_FROM_DIM)}: {len(dim_title.columns)}")

# Rename the Google Books match columns (they would clash with the bridge columns)
dim_title = dim_title.rename(columns=DIM_COLUMNS_TO_RENAME)
print(f"Renamed: {DIM_COLUMNS_TO_RENAME}")

# Put row_index first because it is the primary key
other_columns = [col for col in dim_title.columns if col != "row_index"]
dim_title = dim_title[["row_index"] + other_columns]

# Build two matching keys for every book:
#   match_key              = normalized "title by author1, author2"   (all authors)
#   match_key_first_author = normalized "title by author1"            (first author only)
# ALA lists only the first author, so the second key matches more books exactly.
# A few books have no author. For them both keys use only the title, and we
# print a warning so you know their key looks different.
match_keys = []
match_keys_first_author = []
for position in range(len(dim_title)):
    title = dim_title["title"].iloc[position]
    author = dim_title["author"].iloc[position]
    row_index = dim_title["row_index"].iloc[position]
    if pd.isna(title):
        print(f"WARNING: row_index {row_index} has no title; its match keys will be empty")
        match_keys.append("")
        match_keys_first_author.append("")
    elif pd.isna(author):
        print(f"WARNING: row_index {row_index} ({title!r}) has no author; "
              f"match keys use the title only")
        match_keys.append(normalize(title))
        match_keys_first_author.append(normalize(title))
    else:
        first_author = author.split(",")[0].strip()    # text before the first comma
        match_keys.append(normalize(title + " by " + author))
        match_keys_first_author.append(normalize(title + " by " + first_author))
dim_title["match_key"] = match_keys
dim_title["match_key_first_author"] = match_keys_first_author

# Two different row_index values with the same key would make exact matching
# ambiguous. Report them (Step 2 uses the lowest row_index).
duplicate_keys = dim_title[dim_title["match_key"].duplicated(keep=False)].sort_values("match_key")
if len(duplicate_keys) > 0:
    print(f"\nWARNING: {duplicate_keys['match_key'].nunique()} match_keys are shared by more than "
          f"one row_index (possible duplicate books in the dimension):")
    print(duplicate_keys[["row_index", "match_key"]].to_string(index=False))

duplicate_first_author_keys = dim_title[
    dim_title["match_key_first_author"].duplicated(keep=False)
].sort_values("match_key_first_author")
if len(duplicate_first_author_keys) > 0:
    print(f"\nWARNING: {duplicate_first_author_keys['match_key_first_author'].nunique()} "
          f"match_key_first_author values are shared by more than one row_index "
          f"(the lowest row_index wins). First 10:")
    print(duplicate_first_author_keys[["row_index", "match_key_first_author"]].head(10).to_string(index=False))

print("\nNull count per column:")
print(dim_title.isna().sum().to_string())
print("\nSample rows:")
print(dim_title[["row_index", "title", "author", "match_key", "match_key_first_author"]]
      .sample(3, random_state=1).to_string(index=False))

dim_title.to_csv(DIM_TITLE_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {DIM_TITLE_FILE}  ({len(dim_title)} rows x {len(dim_title.columns)} columns)")


# ===========================================================================
# STEP 2 - bridge_case_title.csv
# ===========================================================================
print("\n" + "=" * 70)
print("STEP 2: Build bridge_case_title")
print("=" * 70)

cases = pd.read_csv(CASES_FILE, dtype=str, encoding="utf-8")
print(f"Loaded {CASES_FILE.name}: {len(cases)} cases")

# The corrupted cases (flag starts with "row_corrupted"): their title lists were
# rebuilt from scattered cells, and some fragments cannot be repaired.
corrupted_case_ids = set(
    cases.loc[cases["data_quality_flag"].notna(), "case_id"].astype(int).tolist()
)
print(f"Corrupted case_ids: {sorted(corrupted_case_ids)}")

# ---- 2a + 2b: one row per (case, title), and work out each title's outcome ----
bridge_rows = []
duplicate_titles_dropped = 0
count_mismatch_cases = 0

for position in range(len(cases)):
    case_id = int(cases["case_id"].iloc[position])

    # The titles challenged in this case
    challenged_titles = split_title_list(cases["titles_challenged_raw"].iloc[position])

    # The case's type_format (Book, Film, Display...). It is empty for corrupted cases.
    case_type_format = cases["type_format"].iloc[position]

    # For this case, build a set of normalized titles for each outcome column.
    # Example: outcome_sets["retained"] = {"title a by x", "title b by y"}
    # make_matching_key is used here AND for the challenged titles below, so a
    # title ending in ' by N/A' is treated the same way in both places.
    outcome_sets = {}
    for outcome_name, column_name in OUTCOME_COLUMNS.items():
        outcome_titles = split_title_list(cases[column_name].iloc[position])
        outcome_sets[outcome_name] = {make_matching_key(title) for title in outcome_titles}

    # Sanity check: does the number of titles we found agree with the sheet's own count?
    expected_count = cases["count_titles_challenged"].iloc[position]
    if pd.notna(expected_count) and int(float(expected_count)) != len(challenged_titles):
        count_mismatch_cases += 1

    titles_seen_in_this_case = set()
    for raw_title in challenged_titles:
        normalized_title = make_matching_key(raw_title)

        # If the same title appears twice in one case, keep only the first
        if normalized_title in titles_seen_in_this_case:
            duplicate_titles_dropped += 1
            print(f"WARNING: case_id {case_id}: title listed twice, keeping one: {raw_title!r}")
            continue
        titles_seen_in_this_case.add(normalized_title)

        # Which outcome column(s) contain this title?
        outcomes_found = []
        for outcome_name in OUTCOME_COLUMNS:
            if normalized_title in outcome_sets[outcome_name]:
                outcomes_found.append(outcome_name)

        if len(outcomes_found) == 0:
            outcome = "unknown"
        else:
            outcome = outcomes_found[0]
            if len(outcomes_found) > 1:
                print(f"WARNING: case_id {case_id}: title appears in more than one outcome "
                      f"{outcomes_found}, using '{outcome}': {raw_title!r}")

        bridge_rows.append({
            "case_id": case_id,
            "raw_title_string": raw_title,
            "normalized_title": normalized_title,   # helper column, removed before saving
            "type_format": case_type_format,         # helper column, removed before saving
            "outcome": outcome,
        })

bridge = pd.DataFrame(bridge_rows)
print(f"\nSplit titles: {len(bridge)} (case_id, title) rows from {bridge['case_id'].nunique()} cases")
print(f"Duplicate titles within a case dropped: {duplicate_titles_dropped}")
print(f"Cases where the number of titles found differs from count_titles_challenged: "
      f"{count_mismatch_cases} (expected for non-book items like 'HATE CRIME by HATE CRIME' "
      f"and the 4 corrupted rows)")

# ---- 2c: match each title to dim_title ----
# Dictionaries for exact lookups: key text -> row_index.
# If several dim rows share a key we keep the LOWEST row_index, so we go through
# the dim rows in row_index order and only store a key the first time we see it.
sorted_dim = dim_title.sort_values("row_index")
exact_lookup_all_authors = {}
exact_lookup_first_author = {}
for row_index, key_all, key_first in zip(sorted_dim["row_index"], sorted_dim["match_key"],
                                         sorted_dim["match_key_first_author"]):
    if key_all not in exact_lookup_all_authors:
        exact_lookup_all_authors[key_all] = row_index
    if key_first not in exact_lookup_first_author:
        exact_lookup_first_author[key_first] = row_index

# Title-only lookup (normalized dim title -> row_index), used for titles whose
# author is a placeholder (N/A, BOOK). A title is only included if it occurs
# EXACTLY ONCE in dim_title; ambiguous titles are left out on purpose.
normalized_dim_titles = dim_title["title"].map(normalize)
title_counts = normalized_dim_titles.value_counts()
title_only_lookup = {}
for row_index, normalized_dim_title in zip(dim_title["row_index"], normalized_dim_titles):
    if title_counts[normalized_dim_title] == 1:
        title_only_lookup[normalized_dim_title] = row_index
print(f"Title-only lookup: {len(title_only_lookup)} titles occur exactly once in dim_title "
      f"({len(title_counts) - len(title_only_lookup)} ambiguous titles are excluded)")

# For printing: which raw titles used the title-only lookup, and what they matched
title_only_examples = {}

# Lists for the fuzzy search (same order, so a position in one is a position in
# the other). If two keys tie, rapidfuzz returns the first one = the lowest row_index.
fuzzy_keys = sorted_dim["match_key_first_author"].tolist()
fuzzy_row_indexes = sorted_dim["row_index"].tolist()

# Many titles repeat across cases, so fuzzy-match each distinct string only once
fuzzy_cache = {}
# Which uppercase / repeated author parts made us call something a non-book (for printing)
non_book_author_counts = {}

result_row_index = []
result_candidate = []
result_method = []
result_confidence = []
# For non_book rows: which rule caught them ("type_format", "author_rule" or "both")
result_non_book_rule = []

for raw_title, normalized_title, type_format in zip(
        bridge["raw_title_string"], bridge["normalized_title"], bridge["type_format"]):
    row_index = pd.NA
    candidate = pd.NA
    confidence = pd.NA
    non_book_rule = ""

    if normalized_title in exact_lookup_all_authors:
        # Rule 1: identical to "title by all authors"
        method = "exact"
        row_index = exact_lookup_all_authors[normalized_title]
        confidence = 100.0

    elif normalized_title in exact_lookup_first_author:
        # Rule 2: identical to "title by first author"
        method = "exact_first_author"
        row_index = exact_lookup_first_author[normalized_title]
        confidence = 100.0

    elif (remove_placeholder_author(raw_title) is not None
          and normalize(remove_placeholder_author(raw_title)) in title_only_lookup):
        # Rule 3: the author is a placeholder (' by N/A' / ' by BOOK'). Remove it and
        # look the bare title up among the titles that occur only once in dim_title.
        method = "exact_title_only"
        row_index = title_only_lookup[normalize(remove_placeholder_author(raw_title))]
        confidence = 100.0
        title_only_examples[raw_title] = row_index

    elif (type_format in NON_BOOK_TYPE_FORMATS) or (non_book_author_part(raw_title) is not None):
        # Rule 4: the CASE is a film / music / magazine ... (type_format), or
        # Rule 5: the author part is UPPERCASE / repeats the title.
        # Either way this is not a book, so there is nothing to link to dim_title.
        method = "non_book"
        author_part = non_book_author_part(raw_title)
        caught_by_type_format = type_format in NON_BOOK_TYPE_FORMATS
        if caught_by_type_format and author_part is not None:
            non_book_rule = "both"
        elif caught_by_type_format:
            non_book_rule = "type_format"
        else:
            non_book_rule = "author_rule"
        if author_part is not None:
            non_book_author_counts[author_part] = non_book_author_counts.get(author_part, 0) + 1

    else:
        # Rule 6: fuzzy match against the first-author keys.
        # fuzz.ratio gives a 0-100 similarity between two whole strings.
        if normalized_title not in fuzzy_cache:
            best_key, best_score, best_position = process.extractOne(
                normalized_title, fuzzy_keys, scorer=fuzz.ratio
            )
            fuzzy_cache[normalized_title] = (fuzzy_row_indexes[best_position], round(best_score, 1))
        best_row_index, best_score = fuzzy_cache[normalized_title]
        confidence = best_score

        if best_score >= ACCEPT_FUZZY_SCORE:
            # Close enough to accept: fill row_index
            method = "fuzzy"
            row_index = best_row_index
        elif best_score >= MIN_FUZZY_SCORE:
            # Plausible but not sure: keep row_index empty, remember the best
            # candidate for a human to check
            method = "fuzzy"
            candidate = best_row_index
        else:
            # Too different from everything
            method = "unmatched"

    result_row_index.append(row_index)
    result_candidate.append(candidate)
    result_method.append(method)
    result_confidence.append(confidence)
    result_non_book_rule.append(non_book_rule)

bridge["row_index"] = pd.array(result_row_index, dtype="Int64")
bridge["candidate_row_index"] = pd.array(result_candidate, dtype="Int64")
bridge["match_method"] = result_method
bridge["match_confidence"] = pd.array(result_confidence, dtype="Float64")
bridge["non_book_rule"] = result_non_book_rule     # helper column, removed before saving

# Corrupted cases: a title that stays "unmatched", or only has an unaccepted fuzzy
# candidate (confidence < 90, no row_index), is almost always a broken fragment
# (e.g. 'Animals' or 'and More! by National Geographic""'). We do not try to stitch
# fragments together; we label them so they are not counted as failures. The
# candidate_row_index and match_confidence stay on the row for reference.
# Fuzzy matches >= 90 (row_index filled) and non_book rows are left alone.
in_corrupted_case = bridge["case_id"].isin(corrupted_case_ids)
is_unmatched_now = bridge["match_method"] == "unmatched"
is_unaccepted_fuzzy_now = (bridge["match_method"] == "fuzzy") & bridge["row_index"].isna()
bridge.loc[in_corrupted_case & (is_unmatched_now | is_unaccepted_fuzzy_now), "match_method"] = "corrupted_fragment"

# needs_manual_review: fuzzy matches we did NOT accept, and all unmatched rows.
# non_book and corrupted_fragment rows do not need review.
is_unaccepted_fuzzy = (bridge["match_method"] == "fuzzy") & bridge["row_index"].isna()
is_unmatched = bridge["match_method"] == "unmatched"
bridge["needs_manual_review"] = is_unaccepted_fuzzy | is_unmatched

# Print the author parts that made us classify rows as non_book, so the rule can be checked
print("\n" + "-" * 70)
print("NON-BOOK RULE CHECK: author parts that triggered 'non_book' (author part: rows)")
for author_part, number in sorted(non_book_author_counts.items(), key=lambda item: -item[1]):
    print(f"  {number:5d}  {author_part!r}")
print(f"({len(non_book_author_counts)} distinct values, {sum(non_book_author_counts.values())} rows)")

# Rows reclassified as non_book by the NEW type_format rule only (the old
# uppercase-author rule would not have caught them). Grouped by type_format
# with 5 sample titles each, so the list can be checked.
caught_by_type_format_only = bridge[bridge["non_book_rule"] == "type_format"]
print(f"\nNEW type_format rule: {len(caught_by_type_format_only)} rows became non_book that the "
      f"author rule alone would not have caught "
      f"(another {int((bridge['non_book_rule'] == 'both').sum())} rows were caught by both rules)")
for type_format_value, group in caught_by_type_format_only.groupby("type_format"):
    print(f"  {type_format_value} ({len(group)} rows):")
    for sample_title in group["raw_title_string"].drop_duplicates().head(5):
        print(f"      {sample_title[:80]!r}")

# Exact matches that happen inside non-book-type cases (reported only, not changed)
in_non_book_case = bridge["type_format"].isin(NON_BOOK_TYPE_FORMATS)
exact_in_non_book_cases = bridge[in_non_book_case & bridge["match_method"].isin(["exact", "exact_first_author"])]
print(f"\nExact / exact_first_author matches inside non-book-type cases (left as matched): "
      f"{len(exact_in_non_book_cases)}")
print(exact_in_non_book_cases.groupby(["type_format", "match_method"]).size().to_string())

# Fix 6: rows where ' by N/A' was removed before matching
na_rows = bridge[bridge["raw_title_string"].map(
    lambda text: remove_trailing_by_na(text) != text.strip(" \"'"))]
print(f"\nRows where a trailing ' by N/A' was removed before matching: {len(na_rows)}")
if len(na_rows) > 0:
    print(na_rows["match_method"].value_counts().to_string())
    print(na_rows.groupby(["raw_title_string", "match_method"]).size().to_string())

# Rows that used the title-only lookup (placeholder author N/A or BOOK)
title_only_rows = bridge[bridge["match_method"] == "exact_title_only"]
print(f"\nRows matched by 'exact_title_only' (placeholder author removed): {len(title_only_rows)}")
dim_text_by_row_index = dict(zip(dim_title["row_index"],
                                 dim_title["title"] + " | author: " + dim_title["author"].fillna("(none)")))
print(f"Distinct titles that used it: {len(title_only_examples)}. Up to 20 (ALA string -> dim title | author):")
for raw_title, matched_row_index in list(title_only_examples.items())[:20]:
    print(f"  {raw_title[:55]!r} -> row_index {matched_row_index}: {dim_text_by_row_index[matched_row_index][:70]!r}")

# ---- 2d: give every row its bridge_id (the new primary key) ----
# The helper columns (normalized_title, type_format, non_book_rule) stay until the
# file is saved, because later printouts use them.
bridge.insert(0, "bridge_id", range(1, len(bridge) + 1))

# ---- 2e: match quality: before vs after ----
total_rows = len(bridge)
review_count = int(bridge["needs_manual_review"].sum())

print("\n" + "-" * 70)
print("MATCH QUALITY: before (previous version) vs after (this version)")
print(f"{'match_method':20s} {'before':>8s} {'before %':>9s} {'after':>8s} {'after %':>9s}")
for method in ["exact", "exact_first_author", "exact_title_only", "fuzzy", "unmatched",
               "non_book", "corrupted_fragment"]:
    after_number = int((bridge["match_method"] == method).sum())
    before_number = BEFORE_FIX[method]
    print(f"{method:20s} {before_number:8d} {before_number / BEFORE_FIX['total'] * 100:8.1f}% "
          f"{after_number:8d} {after_number / total_rows * 100:8.1f}%")
print(f"{'TOTAL ROWS':20s} {BEFORE_FIX['total']:8d} {'':9s} {total_rows:8d}")
print(f"{'needs_manual_review':20s} {BEFORE_FIX['review']:8d} {BEFORE_FIX['review'] / BEFORE_FIX['total'] * 100:8.1f}% "
      f"{review_count:8d} {review_count / total_rows * 100:8.1f}%")

fuzzy_rows = bridge[bridge["match_method"] == "fuzzy"]
print(f"\nFuzzy rows accepted (confidence >= {ACCEPT_FUZZY_SCORE}, row_index filled): "
      f"{int(fuzzy_rows['row_index'].notna().sum())}")
print(f"Fuzzy rows kept as candidates only ({MIN_FUZZY_SCORE}-{ACCEPT_FUZZY_SCORE - 1}, "
      f"row_index empty, candidate_row_index filled): {int(fuzzy_rows['row_index'].isna().sum())}")

# 15 random fuzzy matches, with the dim key they were matched to
key_by_row_index = dict(zip(dim_title["row_index"], dim_title["match_key_first_author"]))
sample = fuzzy_rows.sample(min(15, len(fuzzy_rows)), random_state=42)
print("\n15 random fuzzy matches (title in ALA data  ->  dim match_key_first_author):")
for _, row in sample.iterrows():
    if pd.notna(row["row_index"]):
        matched_key = key_by_row_index[int(row["row_index"])]
        status = "ACCEPTED"
    else:
        matched_key = key_by_row_index[int(row["candidate_row_index"])]
        status = "candidate"
    print(f"  [{row['match_confidence']:5.1f} {status:9s}] {row['raw_title_string'][:55]!r}")
    print(f"                           -> {matched_key[:70]!r}")

# What is left for a human to review?
print("\n" + "-" * 70)
print(f"REVIEW ROWS LEFT: needs_manual_review = True for {review_count} rows. By match_method:")
review_rows = bridge[bridge["needs_manual_review"]]
print(review_rows["match_method"].value_counts().to_string())

# 30 random review rows with the candidate book (if there is one) so they can be eyeballed
title_by_row_index = dict(zip(dim_title["row_index"], dim_title["title"] + " by " + dim_title["author"].fillna("?")))
print("\n30 random review rows (ALA string | best candidate in dim_title | confidence):")
for _, row in review_rows.sample(min(30, len(review_rows)), random_state=42).iterrows():
    if pd.notna(row["candidate_row_index"]):
        candidate_text = title_by_row_index[int(row["candidate_row_index"])]
    else:
        candidate_text = "(no candidate)"
    print(f"  [{row['match_method']:9s} {row['match_confidence']:5.1f}] {row['raw_title_string'][:50]!r}"
          f"  |  {candidate_text[:50]!r}")

# Review rows by the case's type_format and match_method (corrupted cases have no type_format)
print(f"\nReview rows by type_format and match_method (previous round: {BEFORE_FIX['review']} review rows):")
review_by_type = review_rows.assign(type_format=review_rows["type_format"].fillna("(empty)"))
print(pd.crosstab(review_by_type["type_format"], review_by_type["match_method"], margins=True).to_string())

# 20 random review rows from the case types that can hold both books and other items
mixed_review_rows = review_rows[review_rows["type_format"].isin(MIXED_TYPE_FORMATS)]
print(f"\n20 random review rows from Display / Hate Crime / Program / Reading List / ... cases "
      f"({len(mixed_review_rows)} such rows):")
for _, row in mixed_review_rows.sample(min(20, len(mixed_review_rows)), random_state=42).iterrows():
    if pd.notna(row["candidate_row_index"]):
        candidate_text = title_by_row_index[int(row["candidate_row_index"])]
    else:
        candidate_text = "(no candidate)"
    print(f"  [{row['type_format'][:10]:10s} {row['match_method']:9s} {row['match_confidence']:5.1f}] "
          f"{row['raw_title_string'][:45]!r}  |  {candidate_text[:45]!r}")

print("\nRows per match_method:")
print(bridge["match_method"].value_counts().to_string())
print("\nRows per outcome:")
print(bridge["outcome"].value_counts().to_string())

# Check that outcome lists in the cases sheet were fully used. A gap means an outcome
# column lists a title that was not found among that case's challenged titles, or a
# title that sits in two outcome columns (we keep only the first).
print("\nOutcome check (titles listed in the outcome column vs. bridge rows given that outcome):")
for outcome_name, column_name in OUTCOME_COLUMNS.items():
    listed = sum(len(split_title_list(cell)) for cell in cases[column_name])
    assigned = (bridge["outcome"] == outcome_name).sum()
    note = "" if listed == assigned else "   <-- differs"
    print(f"  {outcome_name:18s} listed {listed:5d}   assigned {assigned:5d}{note}")

# The 4 corrupted cases: titles found and how they were matched
print("\nCorrupted cases: bridge rows per case_id and match_method")
corrupted_bridge = bridge[bridge["case_id"].isin(corrupted_case_ids)]
print(pd.crosstab(corrupted_bridge["case_id"], corrupted_bridge["match_method"], margins=True).to_string())

# ===========================================================================
# STEP 3 - is_book (dim_title) and item_type (bridge)
# ===========================================================================
print("\n" + "=" * 70)
print("STEP 3: is_book for dim_title, item_type for the bridge")
print("=" * 70)

# case_scope was created by Script 2 (book / non_book / mixed / empty). Add it to every bridge row.
fact_scope = pd.read_csv(FACT_FILE, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")
scope_by_case = dict(zip(fact_scope["case_id"].astype(int), fact_scope["case_scope"]))
bridge["case_scope"] = bridge["case_id"].map(scope_by_case)       # helper column, removed before saving
print("case_scope of the bridge rows:")
print(bridge["case_scope"].value_counts(dropna=False).to_string())

# dim_title.is_book. A book is NOT a book (False) only if it has at least one
# linked bridge row AND every one of those rows sits in a case whose case_scope is
# "non_book". A book that also appears in any "book" or "mixed" case (or in a
# corrupted case with no scope) stays True, and so does a book with no bridge rows.
linked_rows = bridge[bridge["row_index"].notna()].copy()
linked_rows["row_index"] = linked_rows["row_index"].astype(int)
linked_rows["in_non_book_case"] = linked_rows["case_scope"] == "non_book"
rows_per_book = linked_rows.groupby("row_index").agg(
    bridge_rows=("bridge_id", "size"),
    rows_in_non_book_cases=("in_non_book_case", "sum"),
)
not_a_book_ids = rows_per_book.index[rows_per_book["bridge_rows"] == rows_per_book["rows_in_non_book_cases"]]
dim_title["is_book"] = ~dim_title["row_index"].isin(not_a_book_ids)

print(f"\nis_book = False from the bridge rule: {int((~dim_title['is_book']).sum())} of {len(dim_title)} dim rows "
      f"(dim rows with no bridge row stay True: "
      f"{int((~dim_title['row_index'].isin(rows_per_book.index)).sum())})")

# Extra rule: a dim title with a film / screenplay / musical / TV marker in
# parentheses or brackets, e.g. "The Help (Screenplay)", is an adaptation, not a
# book. (The marker pattern in config.py does not match "[series]" or "(Series)".)
has_marker = dim_title["title"].fillna("").map(lambda text: bool(NON_BOOK_TITLE_MARKER.search(text)))
newly_false = has_marker & dim_title["is_book"]
print(f"\nTitle-marker rule: {int(has_marker.sum())} dim titles carry a non-book marker, "
      f"{int(newly_false.sum())} of them changed from True to False:")
for _, row in dim_title[has_marker].iterrows():
    linked_count = int((linked_rows["row_index"] == row["row_index"]).sum())
    print(f"  row_index {row['row_index']} | {row['title']!r} | author {row['author']!r} | "
          f"bridge rows linked: {linked_count}")
print(f"  Bridge rows linked to these dim rows: {int(linked_rows['row_index'].isin(dim_title.loc[has_marker, 'row_index']).sum())}")
dim_title.loc[has_marker, "is_book"] = False

# Confirm that real book series ("[series]" / "(Series)") were NOT changed
series_titles = dim_title[dim_title["title"].fillna("").str.contains(r"\[series\]|\(series\)", case=False, regex=True)]
print(f"\n{len(series_titles)} dim titles contain '[series]' or '(Series)'; "
      f"{int((~series_titles['is_book']).sum())} of them have is_book = False from any rule. 5 samples:")
for _, row in series_titles.head(5).iterrows():
    print(f"  row_index {row['row_index']} | {row['title']!r} | is_book {row['is_book']}")

print(f"\nis_book = False in total: {int((~dim_title['is_book']).sum())} "
      f"(previous round: {BEFORE_IS_BOOK_FALSE})")

# Which case types did the non-books come from? (30 samples)
type_formats_by_book = linked_rows.groupby("row_index")["type_format"].agg(
    lambda values: ", ".join(sorted(set(values.fillna("(empty)")))))
print("\n30 sample dim rows with is_book = False (row_index | title | author | type_format of their cases):")
sample_non_books = dim_title[~dim_title["is_book"]].sample(min(30, int((~dim_title["is_book"]).sum())),
                                                            random_state=42)
for _, row in sample_non_books.iterrows():
    print(f"  {row['row_index']:6d} | {str(row['title'])[:45]!r} | {str(row['author'])[:25]!r} | "
          f"{type_formats_by_book.get(row['row_index'], '(no bridge rows)')}")

# Authors written entirely in capitals are usually placeholders (BOOK, TEXTBOOK, PLAY, VARIOUS...)
uppercase_authors = dim_title[dim_title["author"].fillna("").str.isupper()]
print(f"\n{len(uppercase_authors)} dim rows have an all-uppercase author (is_book shown):")
for _, row in uppercase_authors.sort_values("author").iterrows():
    print(f"  {row['row_index']:6d} | {str(row['author'])[:20]:20s} | is_book {row['is_book']} | {str(row['title'])[:50]!r}")

# Save dim_title again, now with is_book
dim_title.to_csv(DIM_TITLE_FILE, index=False, encoding="utf-8")
print(f"\nSaved again with is_book: {DIM_TITLE_FILE}  ({len(dim_title)} rows x {len(dim_title.columns)} columns)")

# bridge.item_type, checked in this order:
#   1. match_method is non_book                          -> non_book
#   2. the case's case_scope is non_book (a film, music
#      ... case), even if a row_index is filled          -> non_book
#      (row_index and match_method stay as they are: the case challenged the
#       film, not the novel. is_book in dim_title is NOT changed by this rule.)
#   3. row_index filled and the dim row has is_book True  -> book
#   4. row_index filled and the dim row has is_book False -> non_book
#   5. anything else (no row_index)                      -> unknown
is_book_by_row_index = dict(zip(dim_title["row_index"], dim_title["is_book"]))
item_types = []
rows_changed_by_case_scope = []    # rows where rule 2 turned a "book" into "non_book"
for position in range(len(bridge)):
    method = bridge["match_method"].iloc[position]
    row_index = bridge["row_index"].iloc[position]
    scope = bridge["case_scope"].iloc[position]
    linked_book_is_book = pd.notna(row_index) and is_book_by_row_index[int(row_index)]

    if method == "non_book":
        item_type = "non_book"
    elif scope == "non_book":
        item_type = "non_book"
        if linked_book_is_book:
            rows_changed_by_case_scope.append(position)
    elif pd.notna(row_index):
        item_type = "book" if linked_book_is_book else "non_book"
    else:
        item_type = "unknown"
    item_types.append(item_type)
bridge["item_type"] = item_types

print(f"\nRule 2 (case_scope is non_book) changed {len(rows_changed_by_case_scope)} rows from 'book' to 'non_book':")
for position in rows_changed_by_case_scope:
    row = bridge.iloc[position]
    print(f"  case {row['case_id']} [{row['type_format']}]: {row['raw_title_string'][:60]!r} "
          f"(row_index {row['row_index']}, {row['match_method']})")

# Keep a full copy (with the helper columns) for the final checks, then narrow to the final columns
bridge_full = bridge.copy()
bridge = bridge[["bridge_id", "case_id", "raw_title_string", "row_index", "candidate_row_index",
                 "outcome", "item_type", "match_method", "match_confidence", "needs_manual_review"]]

print("\nitem_type counts:")
print(bridge["item_type"].value_counts().to_string())
print("\nitem_type by match_method:")
print(pd.crosstab(bridge["match_method"], bridge["item_type"], margins=True).to_string())

# Per-year rows: all rows vs book rows. The 4 corrupted cases have no year at this
# point (Script 4 infers it later), so their rows are not in this table.
fact_years = fact_scope[["case_id", "year"]].copy()
fact_years["case_id"] = fact_years["case_id"].astype(int)
bridge_with_year = bridge.merge(fact_years, on="case_id", how="left")
print(f"\nBridge rows per year (rows without a year yet, left out: {int(bridge_with_year['year'].isna().sum())}):")
per_year_all = bridge_with_year.groupby("year").size().rename("all_rows")
per_year_book = bridge_with_year[bridge_with_year["item_type"] == "book"].groupby("year").size().rename("book_rows")
print(pd.concat([per_year_all, per_year_book], axis=1).fillna(0).astype(int).to_string())

bridge.to_csv(BRIDGE_TITLE_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {BRIDGE_TITLE_FILE}")


# ===========================================================================
# FINAL CHECKS
# ===========================================================================
print("\n" + "=" * 70)
print("FINAL CHECKS")
print("=" * 70)

# 1. bridge_id must be unique; (case_id, row_index) pairs should rarely repeat
duplicate_ids = int(bridge["bridge_id"].duplicated().sum())
print(f"1a. Duplicate bridge_id: {duplicate_ids} (should be 0)")

matched_rows = bridge[bridge["row_index"].notna()]
duplicate_pairs_mask = matched_rows.duplicated(subset=["case_id", "row_index"], keep=False)
duplicate_pair_rows = matched_rows[duplicate_pairs_mask]
number_of_duplicate_pairs = len(duplicate_pair_rows.drop_duplicates(subset=["case_id", "row_index"]))
print(f"1b. Duplicate (case_id, row_index) pairs among rows with a row_index: {number_of_duplicate_pairs}")
if number_of_duplicate_pairs > 0:
    print(duplicate_pair_rows.sort_values(["case_id", "row_index"])
          [["bridge_id", "case_id", "row_index", "raw_title_string", "match_method", "match_confidence"]]
          .to_string(index=False))

# Every filled row_index (and candidate) must exist in dim_title
valid_row_indexes = set(dim_title["row_index"])
bad_row_index = int((~bridge["row_index"].dropna().isin(valid_row_indexes)).sum())
bad_candidate = int((~bridge["candidate_row_index"].dropna().isin(valid_row_indexes)).sum())
print(f"    row_index values not in dim_title: {bad_row_index}; "
      f"candidate_row_index values not in dim_title: {bad_candidate} (both should be 0)")

# 2. Match breakdown with percentages, compared with the previous round
print(f"2.  Match breakdown ({total_rows} rows; previous round in brackets):")
for method in ["exact", "exact_first_author", "exact_title_only", "fuzzy", "non_book",
               "corrupted_fragment", "unmatched"]:
    number = int((bridge["match_method"] == method).sum())
    print(f"      {method:20s} {number:6d}  {number / total_rows * 100:5.1f}%   [{BEFORE_FIX[method]}]")
print(f"    needs_manual_review: {review_count} = {review_count / total_rows * 100:.1f}% of rows "
      f"[{BEFORE_FIX['review']}]")

# 3. Two specific titles
print("3.  Specific titles:")
suicide_rows = bridge[bridge["raw_title_string"].str.startswith("What Motivates Suicide Bombers?")]
for _, row in suicide_rows.iterrows():
    print(f"      {row['raw_title_string'][:50]!r}: {row['match_method']}, row_index {row['row_index']}, "
          f"item_type {row['item_type']}")
court_rows = bridge_full[bridge_full["raw_title_string"].str.startswith("A Court of Thorns and Roses")
                         & (bridge_full["type_format"] == "Display")]
print(f"      'A Court of Thorns and Roses' in Display cases: {len(court_rows)} rows, "
      f"methods {court_rows['match_method'].value_counts().to_dict()}")

# 4. non_book rows inside "book" cases can only come from the author rule
non_book_in_book_cases = bridge_full[(bridge_full["match_method"] == "non_book")
                                     & (bridge_full["case_scope"] == "book")]
print(f"4.  non_book rows in cases with case_scope 'book': {len(non_book_in_book_cases)}; "
      f"by rule: {non_book_in_book_cases['non_book_rule'].value_counts().to_dict()} "
      f"(only 'author_rule' is expected)")
for _, row in non_book_in_book_cases.head(10).iterrows():
    print(f"      case {row['case_id']} [{row['type_format']}]: {row['raw_title_string'][:70]!r}")

# 5. item_type, is_book and case_scope counts (before = previous round)
fact_check = pd.read_csv(FACT_FILE, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")
print("5.  item_type counts in the bridge (previous round in brackets):")
for item_type in ["book", "non_book", "unknown"]:
    print(f"      {item_type:10s} {int((bridge['item_type'] == item_type).sum()):6d}   [{BEFORE_ITEM_TYPE[item_type]}]")
print(f"    is_book = False in dim_title: {int((~dim_title['is_book']).sum())}   [{BEFORE_IS_BOOK_FALSE}]")
print("    case_scope and case_scope_source counts in fact_case (Script 4 may fill more later):")
print(fact_check["case_scope"].value_counts(dropna=False).to_string())
print(fact_check["case_scope_source"].value_counts(dropna=False).to_string())

# 5b. No bridge row in a non_book-scope case may be a 'book'
book_in_non_book_case = bridge_full[(bridge_full["case_scope"] == "non_book") & (bridge_full["item_type"] == "book")]
print(f"5b. Bridge rows with item_type 'book' in a case_scope 'non_book' case: {len(book_in_non_book_case)} (must be 0)")

# 5d. dim_title.year must be written as whole numbers (no "2015.0")
saved_dim_year = pd.read_csv(DIM_TITLE_FILE, dtype=str, usecols=["year"], encoding="utf-8")["year"]
decimal_years = int(saved_dim_year.str.fullmatch(r"\d+\.0", na=False).sum())
print(f"5d. dim_title.year values like '2015.0' in the saved file: {decimal_years} (must be 0); "
      f"non-blank years: {int(saved_dim_year.notna().sum())}")

# 5c. Scripts 2 and 3 must not define NON_BOOK_TYPE_FORMATS themselves any more (config.py does)
for script_name in ["02_build_fact_and_reasons.py", "03_build_dim_title_and_bridge.py"]:
    script_lines = (Path(__file__).resolve().parent / script_name).read_text(encoding="utf-8").splitlines()
    definitions = [line for line in script_lines if line.strip().startswith("NON_BOOK_TYPE_FORMATS =")]
    imports = [line for line in script_lines if line.startswith("from config import")]
    print(f"5c. {script_name}: lines defining NON_BOOK_TYPE_FORMATS = {len(definitions)} (must be 0); "
          f"import line: {imports}")

# 6. fact_case checks
decimal_values = 0
for column in fact_check.columns:
    decimal_values += int(fact_check[column].str.fullmatch(r"-?\d+\.0", na=False).sum())
print(f"6.  fact_case: {len(fact_check)} rows (expected 5373), values like '12.0': {decimal_values}, "
      f"probable_duplicate_of_case_id filled for {int(fact_check['probable_duplicate_of_case_id'].notna().sum())} rows "
      f"(Script 4 fills it)")

# 7. Row counts in vs out for every file
print("7.  Row counts:")
raw_titles_rows = len(pd.read_csv(TITLES_FILE, dtype=str, encoding="utf-8"))
files_to_report = [
    ("combined_final_flagged.csv (input)", raw_titles_rows),
    ("cases_clean_full.csv", len(cases)),
    ("fact_case.csv", len(fact_check)),
    ("bridge_case_reason.csv", len(pd.read_csv(BRIDGE_REASON_FILE, encoding="utf-8"))),
    ("dim_title.csv", len(dim_title)),
    ("bridge_case_title.csv", len(bridge)),
]
for name, number in files_to_report:
    print(f"      {name:36s} {number:7d}")
print(f"    cases_clean_full -> fact_case equal? {len(cases) == len(fact_check)}")
print(f"    combined_final_flagged -> dim_title equal? {raw_titles_rows == len(dim_title)}")

# ---------------------------------------------------------------------------
# Plain-English summary
# ---------------------------------------------------------------------------
exact_total = int(bridge["match_method"].isin(["exact", "exact_first_author", "exact_title_only"]).sum())
print("\n" + "-" * 70)
print("SUMMARY")
print(f"Script 3 read {TITLES_FILE.name} and wrote dim_title.csv with {len(dim_title)} books "
      f"(row_index as the key, two match-key columns, an is_book flag, average_rating and the yearly case "
      f"counts removed, Google Books match columns renamed with a gb_ prefix; "
      f"{int((~dim_title['is_book']).sum())} books flagged is_book = False). It then split every "
      f"case's challenged-title list into {total_rows} rows from {len(cases)} cases, gave each a "
      f"bridge_id, labelled it with its outcome (or 'unknown') and an item_type, and linked it to a book: "
      f"{exact_total} exact (all authors, first author or title only), "
      f"{int(((bridge['match_method'] == 'fuzzy') & bridge['row_index'].notna()).sum())} accepted fuzzy, "
      f"{int((bridge['match_method'] == 'non_book').sum())} non-books, "
      f"{int((bridge['match_method'] == 'corrupted_fragment').sum())} broken fragments from corrupted cases, "
      f"and {review_count} rows flagged for manual review in {BRIDGE_TITLE_FILE.name}.")
print("-" * 70)
