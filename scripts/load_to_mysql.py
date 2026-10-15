"""
load_to_mysql.py  --  Phase 2: build banned_books_db and load the 4 clean CSVs.

What it does
  1. Connects to your local MySQL server and creates banned_books_db if missing.
  2. Runs sql/schema.sql (drops + recreates the 4 tables, so re-running is safe).
  3. Loads data/clean/{dim_title,fact_case,bridge_case_title,bridge_case_reason}.csv
     with batched plain INSERT statements (no LOAD DATA INFILE).
  4. Prints CSV row count vs MySQL row count for every table.

Usage
  pip install pandas pymysql
  set MYSQL_PASSWORD=your_password        (PowerShell: $env:MYSQL_PASSWORD="...")
  python scripts/load_to_mysql.py

cases_clean_full.csv is a staging file and is intentionally never read.
"""

import os
import re
import sys
from pathlib import Path

import pandas as pd
import pymysql

# ---------------------------------------------------------------------------
# CONNECTION SETTINGS -- edit these for your own machine.
# Each value can also come from an environment variable (MYSQL_HOST, MYSQL_PORT,
# MYSQL_USER, MYSQL_PASSWORD) so you needn't type the password into this file;
# otherwise just edit the fallback string in each line.
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("MYSQL_HOST", "localhost")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_USER = os.environ.get("MYSQL_USER", "root")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD", "your_password_here")
DB_NAME = "banned_books_db"

BATCH_SIZE = 1000  # rows per INSERT batch

# ---------------------------------------------------------------------------
# Paths (relative to this file, so the script works from any directory)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
SCHEMA_FILE = PROJECT_ROOT / "sql" / "schema.sql"

# ---------------------------------------------------------------------------
# Table definitions, in load order (parents before children).
# Column kinds: "int", "dec" (decimal), "bool", "str".
# Anything not listed is "str". Column order here == INSERT column order.
# ---------------------------------------------------------------------------
TABLES = {
    "dim_title": {
        "int": ["row_index", "year", "page_count"],
        "dec": ["gb_match_confidence"],
        "bool": ["gb_needs_manual_review", "is_book"],
        "str": ["title", "author", "publisher", "isbn", "fiction_or_nonfiction",
                "age_group", "language", "summary", "maturity_rating",
                "gb_match_method", "match_key", "match_key_first_author"],
    },
    "fact_case": {
        "int": ["case_id", "year", "count_titles_challenged", "count_retained",
                "count_bowdlerized", "count_defaced", "count_destroyed",
                "count_stolen", "count_withdrawn_removed", "count_relocated",
                "count_restricted", "count_removed_required",
                "count_children_titles", "count_ya_titles", "count_censored",
                "count_known_outcomes", "rated_on_target_list",
                "probable_duplicate_of_case_id"],
        "dec": [],
        "bool": ["adversarial_group_flag", "rated_on_target_flag"],
        "str": ["year_source", "type_format", "case_scope", "case_scope_source",
                "state", "location", "initiator", "adversarial_group_involvement",
                "data_quality_flag"],
    },
    "bridge_case_title": {
        "int": ["bridge_id", "case_id", "row_index", "candidate_row_index"],
        "dec": ["match_confidence"],
        "bool": ["needs_manual_review"],
        "str": ["raw_title_string", "outcome", "item_type", "match_method"],
    },
    "bridge_case_reason": {
        "int": ["case_id"],
        "dec": [],
        "bool": [],
        "str": ["reason"],
    },
}

# dim_title.page_count holds the text 'NOT_FOUND' on rows with no Google Books
# match; the MySQL column is INT, so those become NULL (counted and reported).
NOT_FOUND_TO_NULL = {"dim_title": ["page_count"]}


def read_csv(table):
    """Read a CSV as raw strings. keep_default_na=False stops pandas turning
    real text such as 'None' or 'NA' into NULL; only empty cells are NULL."""
    path = CLEAN_DIR / f"{table}.csv"
    return pd.read_csv(path, dtype=str, keep_default_na=False,
                       na_values=[""], encoding="utf-8")


def to_bool(v):
    if v in ("True", "1", "1.0"):
        return 1
    if v in ("False", "0", "0.0"):
        return 0
    raise ValueError(f"unexpected boolean value: {v!r}")


def convert(df, table):
    """Return (list_of_row_tuples, column_names) with Python types MySQL expects."""
    spec = TABLES[table]
    cols = spec["int"] + spec["dec"] + spec["bool"] + spec["str"]
    # keep the CSV's own column order for the INSERT
    cols = [c for c in df.columns if c in cols]
    missing = set(sum(spec.values(), [])) - set(df.columns)
    if missing:
        sys.exit(f"{table}.csv is missing expected columns: {sorted(missing)}")
    unexpected = set(df.columns) - set(cols)
    if unexpected:
        sys.exit(f"{table}.csv has columns not in the schema: {sorted(unexpected)}")

    df = df[cols].copy()
    for col in NOT_FOUND_TO_NULL.get(table, []):
        n = (df[col] == "NOT_FOUND").sum()
        df.loc[df[col] == "NOT_FOUND", col] = pd.NA
        print(f"    note: {table}.{col}: {n} 'NOT_FOUND' values stored as NULL")

    for c in spec["int"]:
        df[c] = df[c].map(lambda v: None if pd.isna(v) else int(float(v)), na_action=None)
    for c in spec["dec"]:
        df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v))  # MySQL parses the decimal text exactly
    for c in spec["bool"]:
        df[c] = df[c].map(lambda v: None if pd.isna(v) else to_bool(v))
    for c in spec["str"]:
        df[c] = df[c].map(lambda v: None if pd.isna(v) else v)

    rows = [tuple(None if pd.isna(v) else v for v in rec)
            for rec in df.itertuples(index=False, name=None)]
    return rows, cols


def run_schema(cur):
    """Execute sql/schema.sql one statement at a time."""
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    # drop full-line '--' comments, then split on ';'
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    # also strip trailing '-- ...' comments (none of them contain quotes or ';')
    text = re.sub(r"[ \t]+--[^\n]*", "", text)
    for stmt in (s.strip() for s in text.split(";")):
        if stmt:
            cur.execute(stmt)


def load_table(conn, table):
    df = read_csv(table)
    rows, cols = convert(df, table)
    col_list = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            # pymysql turns executemany on an INSERT into one multi-row INSERT
            cur.executemany(sql, rows[i:i + BATCH_SIZE])
    conn.commit()
    return len(df)


def main():
    # 1) connect without a default DB, make sure the DB exists
    server = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                             password=DB_PASSWORD, charset="utf8mb4")
    with server.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    server.close()

    # 2) reconnect inside the database and (re)build the tables
    conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                           password=DB_PASSWORD, database=DB_NAME,
                           charset="utf8mb4", autocommit=False)
    with conn.cursor() as cur:
        run_schema(cur)
    conn.commit()
    print(f"Schema created in {DB_NAME} from {SCHEMA_FILE.name}\n")

    # 3) load, parents first
    csv_counts = {}
    for table in TABLES:
        print(f"Loading {table} ...")
        csv_counts[table] = load_table(conn, table)

    # 4) verify
    print(f"\n{'table':<22}{'CSV rows':>10}{'MySQL rows':>12}  status")
    ok = True
    with conn.cursor() as cur:
        for table, n_csv in csv_counts.items():
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            n_db = cur.fetchone()[0]
            match = n_db == n_csv
            ok &= match
            print(f"{table:<22}{n_csv:>10,}{n_db:>12,}  {'OK' if match else 'MISMATCH'}")
    conn.close()
    if not ok:
        sys.exit("\nRow counts differ -- investigate before using the database.")
    print("\nAll row counts match.")


if __name__ == "__main__":
    main()
