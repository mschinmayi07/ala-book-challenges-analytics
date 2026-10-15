"""
Script 1: 01_clean_cases.py

Step 1 - Load the full 754-column Cases sheet and repair the 4 corrupted rows.
Step 2 - Drop the 722 empty "Unnamed" columns, rename the 32 real columns to
         snake_case, add a case_id, and save data/clean/cases_clean_full.csv.

Run from the project folder:   python scripts/01_clean_cases.py
"""

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths (the raw file is only ever READ, never written to)
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_FILE = PROJECT_DIR / "data" / "raw" / "Cases-AZSU_2026.xlsx"
CLEAN_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_FILE = CLEAN_DIR / "cases_clean_full.csv"

CLEAN_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Column groups (original Excel names)
# ---------------------------------------------------------------------------
# These are the columns whose real values were overwritten in the 4 corrupted
# rows. For those rows we set all of them to NaN because the values are lost.
# The 9 outcome-list columns are included too: they hold title fragments in the
# corrupted rows, and leaving them in would make Script 3 think every title
# had an outcome.
METADATA_COLUMNS_TO_BLANK = [
    "YEAR",
    "Type-format +",
    "State",
    "Location +",
    "Initiator +",
    "Reasons",
    "Adversarial Group Involvement",
    "Rated Titles from Booklooks/RatedBooks/TakeBackTheClassroom Target Lists",
    "Titles Retained Without Restriction",
    "Titles Relocated",
    "Titles Restricted",
    "Titles Removed from Required",
    "Titles Withdrawn/Removed",
    "Titles Bowdlerized",
    "Titles Defaced",
    "Titles Destroyed",
    "Titles Stolen",
    "Count Titles Challenged",
    "Count Titles Retained",
    "Count Titles Bowdlerized",
    "Count Titles Defaced",
    "Count Titles Destroyed",
    "Count Titles Stolen",
    "Count Titles Withdrawn/Removed",
    "Count Titles Relocated",
    "Count Titles Restricted",
    "Count Titles Removed from Required",
    "Count Children's Titles Challenged",
    "Count YA Titles Challenged",
    "Count Titles Censored",
    "Count Titles with Known Outcomes",
]

# Exact rename mapping: original Excel column name -> snake_case name
RENAME_MAP = {
    "YEAR": "year",
    "Type-format +": "type_format",
    "State": "state",
    "Titles Challenged": "titles_challenged_raw",
    "Titles Retained Without Restriction": "titles_retained_raw",
    "Titles Relocated": "titles_relocated_raw",
    "Titles Restricted": "titles_restricted_raw",
    "Titles Removed from Required": "titles_removed_required_raw",
    "Titles Withdrawn/Removed": "titles_withdrawn_removed_raw",
    "Titles Bowdlerized": "titles_bowdlerized_raw",
    "Titles Defaced": "titles_defaced_raw",
    "Titles Destroyed": "titles_destroyed_raw",
    "Titles Stolen": "titles_stolen_raw",
    "Location +": "location",
    "Initiator +": "initiator",
    "Count Titles Challenged": "count_titles_challenged",
    "Reasons": "reasons_raw",
    "Adversarial Group Involvement": "adversarial_group_involvement",
    "Rated Titles from Booklooks/RatedBooks/TakeBackTheClassroom Target Lists": "rated_on_target_list",
    "Count Titles Retained": "count_retained",
    "Count Titles Bowdlerized": "count_bowdlerized",
    "Count Titles Defaced": "count_defaced",
    "Count Titles Destroyed": "count_destroyed",
    "Count Titles Stolen": "count_stolen",
    "Count Titles Withdrawn/Removed": "count_withdrawn_removed",
    "Count Titles Relocated": "count_relocated",
    "Count Titles Restricted": "count_restricted",
    "Count Titles Removed from Required": "count_removed_required",
    "Count Children's Titles Challenged": "count_children_titles",
    "Count YA Titles Challenged": "count_ya_titles",
    "Count Titles Censored": "count_censored",
    "Count Titles with Known Outcomes": "count_known_outcomes",
}

# After renaming, these columns should hold whole numbers
# (rated_on_target_list is a count too, even though its name does not start with count_)
NUMBER_COLUMNS = ["year", "rated_on_target_list"] + [
    name for name in RENAME_MAP.values() if name.startswith("count_")
]

# In the corrupted rows, the original Location cell sits at the start of a
# "tail" of cells pushed to the right end of the row. These are the only
# location values in the data, and we use them to find where the tail starts.
KNOWN_LOCATIONS = ["Public Library", "School Library", "School"]


# ===========================================================================
# STEP 1 - Load the full sheet and fix the 4 corrupted rows
# ===========================================================================
print("=" * 70)
print("STEP 1: Load full sheet and fix corrupted rows")
print("=" * 70)

# Load every column (all 754). We need the "Unnamed" columns for now because
# the scattered title fragments in the 4 bad rows live in them.
cases = pd.read_excel(RAW_FILE)

print(f"Rows loaded:    {len(cases)}")
print(f"Columns loaded: {len(cases.columns)}")
print(f"Non-null cells in columns 33-754 (the 'Unnamed' ones): "
      f"{int(cases.iloc[:, 32:].notna().sum().sum())}")

# Find the corrupted rows: YEAR should be a whole number like 2023. In the bad
# rows it holds a book title, so converting it to a number fails (-> NaN).
# errors="coerce" is deliberate here: it is how we FIND the bad rows, and we
# then print every one of them below.
year_as_number = pd.to_numeric(cases["YEAR"], errors="coerce")
year_has_value = cases["YEAR"].notna()
corrupted_mask = year_as_number.isna() & year_has_value
corrupted_index = cases.index[corrupted_mask].tolist()

print(f"\nRows where YEAR is not a parseable number: {len(corrupted_index)}")
if len(corrupted_index) != 4:
    print(f"WARNING: expected exactly 4 corrupted rows but found {len(corrupted_index)}. "
          f"Check the list above before trusting the output.")

# Also warn about rows where YEAR is simply empty (those are NOT the corrupted
# kind, but the user should know about them).
empty_year_rows = cases.index[~year_has_value].tolist()
if empty_year_rows:
    print(f"WARNING: {len(empty_year_rows)} rows have an empty YEAR (not treated as "
          f"corrupted). DataFrame row numbers: {empty_year_rows}")

# Add the flag column; blank (NaN) for every row by default
cases["data_quality_flag"] = pd.NA

# Build the lists of "known good" values from the 5,369 healthy rows. We use
# them below to recognise the real location / initiator / reasons cells that
# were pushed to the end of three of the corrupted rows.
healthy_rows = cases.drop(index=corrupted_index)

# Known initiators: every distinct non-empty Initiator value (spaces trimmed)
known_initiators = set(healthy_rows["Initiator +"].dropna().astype(str).str.strip())

# Known reasons: split each Reasons cell on commas and collect the distinct pieces
known_reasons = set()
for reasons_cell in healthy_rows["Reasons"].dropna():
    for piece in str(reasons_cell).split(","):
        if piece.strip() != "":
            known_reasons.add(piece.strip())
print(f"\nKnown values taken from healthy rows: {len(KNOWN_LOCATIONS)} locations, "
      f"{len(known_initiators)} initiators, {len(known_reasons)} reasons")

for row_number in corrupted_index:
    # Take every non-empty cell across ALL columns, left to right, as text
    all_cells = cases.loc[row_number, cases.columns[:-1]]  # [:-1] skips the flag column we just added
    cells = [str(cell) for cell in all_cells.dropna()]

    # Look for the "tail": scanning from the RIGHT, find the first cell that is
    # exactly a known location. That cell and everything after it is the tail
    # (location, initiator, count, reasons, numbers); everything before it is titles.
    tail_start = None
    for position in range(len(cells) - 1, -1, -1):
        if cells[position].strip() in KNOWN_LOCATIONS:
            tail_start = position
            break

    if tail_start is None:
        # No tail (this happens in one row): every cell is a title fragment
        title_cells = cells
        tail_cells = []
    else:
        title_cells = cells[:tail_start]
        tail_cells = cells[tail_start:]

    # Overwrite the metadata columns with NaN first. Then we put back ONLY the
    # three things we can recognise safely. Numbers in the tail are NOT recovered:
    # we cannot tell which count column each one belonged to.
    cases.loc[row_number, METADATA_COLUMNS_TO_BLANK] = pd.NA

    recovered_location = None
    recovered_initiator = None
    recovered_reasons = None

    if len(tail_cells) > 0:
        # Location: the first tail cell (that is how we found the tail)
        recovered_location = tail_cells[0].strip()

        # Initiator: the next cell, but only if it is a known initiator
        if len(tail_cells) > 1 and tail_cells[1].strip() in known_initiators:
            recovered_initiator = tail_cells[1].strip()
        else:
            print(f"  WARNING: row {row_number}: the cell after the location "
                  f"({tail_cells[1:2]}) is not a known initiator, so initiator stays blank")

        # Reasons: split every tail cell on commas (in one row all reasons are
        # packed in a single cell) and keep only pieces that exactly match a known reason
        recovered_reason_list = []
        for cell in tail_cells:
            for piece in cell.split(","):
                piece = piece.strip()
                if piece in known_reasons and piece not in recovered_reason_list:
                    recovered_reason_list.append(piece)
        if len(recovered_reason_list) > 0:
            recovered_reasons = ",".join(recovered_reason_list)

        cases.loc[row_number, "Location +"] = recovered_location
        cases.loc[row_number, "Initiator +"] = recovered_initiator
        cases.loc[row_number, "Reasons"] = recovered_reasons
        flag = "row_corrupted_partial_recovery"
    else:
        flag = "row_corrupted_metadata_lost"

    # Store the rebuilt title list (cells BEFORE the tail only) and mark the row
    cases.loc[row_number, "Titles Challenged"] = ", ".join(title_cells)
    cases.loc[row_number, "data_quality_flag"] = flag

    # Excel row = DataFrame row + 2 (+1 because Excel counts from 1,
    # +1 because row 1 of the sheet is the header)
    print(f"  Fixed DataFrame row {row_number} (Excel row {row_number + 2}, case_id {row_number + 1}):")
    print(f"      title cells found: {len(title_cells)}   tail found: {'yes' if tail_start is not None else 'no'}"
          f"   flag: {flag}")
    print(f"      recovered location:  {recovered_location!r}")
    print(f"      recovered initiator: {recovered_initiator!r}")
    print(f"      recovered reasons:   {recovered_reasons!r}")
    if len(tail_cells) > 0:
        print(f"      tail cells left unused ({len(tail_cells)} in tail): {tail_cells}")

print("\nNOTE: In these rows YEAR, Type-format, State, Adversarial Group Involvement, Rated")
print("Titles, the 9 outcome-title columns and all Count columns are set to NaN (not guessed).")
print("Location, Initiator and Reasons are recovered only where they exactly match a known value.")


# ===========================================================================
# STEP 2 - Drop Unnamed columns, rename, add case_id, save
# ===========================================================================
print("\n" + "=" * 70)
print("STEP 2: Drop 'Unnamed' columns, rename to snake_case, add case_id")
print("=" * 70)

# Sanity check before: how many columns are Unnamed vs. named?
unnamed_columns = [col for col in cases.columns if str(col).startswith("Unnamed:")]
named_columns = [col for col in cases.columns if col not in unnamed_columns]
print(f"Columns before: {len(cases.columns)}  "
      f"(named, incl. data_quality_flag: {len(named_columns)}, unnamed: {len(unnamed_columns)})")

# Make sure every column in the rename mapping really exists in the sheet.
# If a header was misspelled we want to stop loudly, not silently skip it.
missing_columns = [col for col in RENAME_MAP if col not in cases.columns]
if missing_columns:
    raise KeyError(f"These expected columns were not found in the sheet: {missing_columns}")

# Keep only the 31 named columns plus our flag. Dropping the Unnamed ones is
# safe now because the fragments in them have already been used above.
cases = cases.drop(columns=unnamed_columns)

# Rename the original headers to snake_case
cases = cases.rename(columns=RENAME_MAP)

# Convert year and the count columns to whole numbers. Anything that is
# non-empty but not a number gets a printed warning with its row number
# instead of being turned into NaN quietly.
for column in NUMBER_COLUMNS:
    converted = pd.to_numeric(cases[column], errors="coerce")
    failed = cases[column].notna() & converted.isna()
    for row_number in cases.index[failed]:
        print(f"WARNING: row {row_number} (case_id {row_number + 1}): "
              f"column '{column}' has non-numeric value {cases.loc[row_number, column]!r}")
    cases[column] = converted.astype("Int64")  # Int64 allows whole numbers AND blanks

# Add a simple sequential case_id starting at 1, and put it first
cases.insert(0, "case_id", range(1, len(cases) + 1))

# Final column order: case_id first, then the sheet order, flag last (already last)
print(f"Columns after:  {len(cases.columns)}")
print(f"case_id unique? {cases['case_id'].is_unique}  "
      f"(min {cases['case_id'].min()}, max {cases['case_id'].max()})")

print("\nNull count per column (after cleaning):")
print(cases.isna().sum().to_string())

print("\nRows per year:")
print(cases["year"].value_counts(dropna=False).sort_index().to_string())

print("\nCorrupted rows in the final table:")
print(cases.loc[cases["data_quality_flag"].notna(),
                ["case_id", "year", "state", "location", "initiator", "reasons_raw",
                 "titles_challenged_raw", "data_quality_flag"]]
      .assign(titles_challenged_raw=lambda df: df["titles_challenged_raw"].str[:40] + "...")
      .assign(reasons_raw=lambda df: df["reasons_raw"].str[:40])
      .to_string(index=False))

# Save (utf-8 so accented characters in book titles survive)
cases.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
print(f"\nSaved: {OUTPUT_FILE}")

# Check the saved file: no whole number should be written like "2023.0".
# We read it back as text so pandas does not hide anything.
saved_text = pd.read_csv(OUTPUT_FILE, dtype=str, encoding="utf-8")
decimal_whole_numbers = 0
for column in saved_text.columns:
    looks_like_float = saved_text[column].str.fullmatch(r"-?\d+\.0", na=False)
    if looks_like_float.sum() > 0:
        print(f"WARNING: column '{column}' has {int(looks_like_float.sum())} values like '12.0'")
        decimal_whole_numbers += int(looks_like_float.sum())
print(f"Values written like '12.0' in {OUTPUT_FILE.name}: {decimal_whole_numbers} (should be 0)")

# ---------------------------------------------------------------------------
# Plain-English summary
# ---------------------------------------------------------------------------
print("\n" + "-" * 70)
print("SUMMARY")
print(f"Script 1 read {RAW_FILE.name} ({len(cases)} rows x 754 columns). It found "
      f"{len(corrupted_index)} corrupted rows (DataFrame rows {corrupted_index}), rebuilt each "
      f"one's title list from its scattered cells (leaving out the shifted-right tail), recovered "
      f"location/initiator/reasons where the tail allowed it, blanked everything else that was "
      f"lost, and flagged them ('row_corrupted_partial_recovery' or "
      f"'row_corrupted_metadata_lost'). It then dropped {len(unnamed_columns)} "
      f"Unnamed columns, renamed the rest to snake_case, added case_id 1-{len(cases)}, and wrote "
      f"{len(cases)} rows x {len(cases.columns)} columns to {OUTPUT_FILE.name}.")
print("-" * 70)
