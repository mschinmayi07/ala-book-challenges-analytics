-- =====================================================================
-- schema.sql  --  banned_books_db (MySQL 8.x)
--
-- Run with banned_books_db already selected (scripts/load_to_mysql.py does
-- this for you; in Workbench, double-click the schema first).
--
-- Re-runnable: drops the view fact_case_deduped and all four tables (children
-- before parents), then rebuilds them. The view is created last; it hides
-- case 4564, the probable duplicate of case 3939, from the queries that use it.
--
-- Deviations from the course SQL list (all unavoidable, none are extra
-- query features):
--   * DROP TABLE ... IF EXISTS : the IF EXISTS modifier is what makes the
--     file safe to re-run.
--   * ENGINE / DEFAULT CHARSET / COLLATE table options : required to get
--     utf8mb4 on every text column (Renee with accent, Saenz with accent,
--     etc.). utf8mb4_unicode_ci is used instead of MySQL 8's default so the
--     file also loads on 5.7 and MariaDB. Note it is case- and
--     accent-insensitive, so GROUP BY on a text column merges strings that
--     differ only by case or accents.
--   * Column data types (INT, VARCHAR, TEXT, DECIMAL, BOOLEAN) : types, not
--     statements; you asked for them.
--
-- Column sizes were chosen from the real CSVs in data/clean/ (max observed
-- length noted where it matters), with headroom.
-- =====================================================================

DROP VIEW IF EXISTS fact_case_deduped;

DROP TABLE IF EXISTS bridge_case_reason;
DROP TABLE IF EXISTS bridge_case_title;
DROP TABLE IF EXISTS fact_case;
DROP TABLE IF EXISTS dim_title;


-- ---------------------------------------------------------------------
-- dim_title : one row per distinct title/edition (10,380 rows)
-- ---------------------------------------------------------------------
CREATE TABLE dim_title (
    row_index              INT            NOT NULL,
    title                  VARCHAR(255)   NOT NULL,           -- max seen: 205
    author                 VARCHAR(150),                      -- max 69; 4 NULLs
    year                   INT,                               -- publication year; 475 NULLs; 15 source values are corrupt (e.g. 202, 20224, 2003202) and are loaded as-is
    publisher              VARCHAR(150),                      -- max 51
    isbn                   VARCHAR(700),                      -- max 616: some rows hold several ISBNs, and one holds a pasted "Subjects:" blob
    fiction_or_nonfiction  VARCHAR(20),                       -- 'Fiction' / 'Non-Fiction' / NULL
    age_group              VARCHAR(20),                       -- 'Children' / 'Young Adult' / 'Adult' / NULL
    language               VARCHAR(20)    NOT NULL,           -- 'en', 'pt-BR', ... or 'NOT_FOUND'
    page_count             INT,                               -- source has 'NOT_FOUND' on 511 rows; the loader stores those as NULL
    summary                TEXT           NOT NULL,           -- max 4,687 chars; 'NOT_FOUND' when there is no Google Books match
    maturity_rating        VARCHAR(20)    NOT NULL,           -- 'MATURE' / 'NOT_MATURE' / 'NOT_FOUND'
    gb_match_method        VARCHAR(30)    NOT NULL,           -- 'isbn' / 'title_author_fuzzy' / 'NOT_FOUND'
    gb_match_confidence    DECIMAL(5,2)   NOT NULL,           -- 0-100
    gb_needs_manual_review BOOLEAN        NOT NULL,
    match_key              VARCHAR(300)   NOT NULL,           -- max 223
    match_key_first_author VARCHAR(300)   NOT NULL,           -- max 223
    is_book                BOOLEAN        NOT NULL,
    PRIMARY KEY (row_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- fact_case : one row per challenge case (5,373 rows)
-- The 7 "corrupted" cases have NULL in every count_* / flag column.
-- ---------------------------------------------------------------------
CREATE TABLE fact_case (
    case_id                        INT           NOT NULL,
    year                           INT           NOT NULL,    -- 2016-2025
    year_source                    VARCHAR(30)   NOT NULL,    -- 'original' / 'inferred_from_titles_file'
    type_format                    VARCHAR(30),               -- 9 NULLs
    case_scope                     VARCHAR(20),               -- 'book' / 'mixed' / 'non_book'; 1 NULL
    case_scope_source              VARCHAR(30),               -- 1 NULL
    state                          VARCHAR(3),                -- 2-letter code, plus 'INT'; 4 NULLs
    location                       VARCHAR(30),               -- 4 NULLs
    initiator                      VARCHAR(40),               -- 116 NULLs (plus 200 'None Given')
    count_titles_challenged        INT,
    count_retained                 INT,
    count_bowdlerized              INT,
    count_defaced                  INT,
    count_destroyed                INT,
    count_stolen                   INT,
    count_withdrawn_removed        INT,
    count_relocated                INT,
    count_restricted               INT,
    count_removed_required         INT,
    count_children_titles          INT,
    count_ya_titles                INT,
    count_censored                 INT,
    count_known_outcomes           INT,
    adversarial_group_involvement  VARCHAR(500),              -- max 435; NULL on 5,050 rows
    adversarial_group_flag         BOOLEAN,                   -- 0/1 in the CSV; 4 NULLs
    rated_on_target_list           INT,
    rated_on_target_flag           BOOLEAN,                   -- 0/1 in the CSV; 7 NULLs
    probable_duplicate_of_case_id  INT,                       -- only case 4564 is set (-> 3939); deliberately NOT a foreign key
    data_quality_flag              VARCHAR(50),               -- 4 non-NULL rows
    PRIMARY KEY (case_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- bridge_case_title : which titles were challenged in which case (41,381 rows)
-- ---------------------------------------------------------------------
CREATE TABLE bridge_case_title (
    bridge_id            INT           NOT NULL AUTO_INCREMENT,
    case_id              INT           NOT NULL,
    raw_title_string     VARCHAR(300)  NOT NULL,   -- max seen: 223 (you suggested TEXT; VARCHAR is enough and stays groupable/indexable)
    row_index            INT,                      -- NULL for non-book rows and truly unmatched titles (774 rows)
    candidate_row_index  INT,                      -- soft "best guess for review"; intentionally NO foreign key
    outcome              VARCHAR(30)   NOT NULL,   -- e.g. 'retained', 'withdrawn_removed', 'unknown'
    item_type            VARCHAR(20)   NOT NULL,   -- 'book' / 'non_book' / 'unknown'
    match_method         VARCHAR(30)   NOT NULL,   -- 'exact', 'fuzzy', 'unmatched', ...
    match_confidence     DECIMAL(5,2),             -- 23.5-100; 527 NULLs (the non_book rows)
    needs_manual_review  BOOLEAN       NOT NULL,
    PRIMARY KEY (bridge_id),
    FOREIGN KEY (case_id)   REFERENCES fact_case (case_id)   ON DELETE CASCADE,
    FOREIGN KEY (row_index) REFERENCES dim_title (row_index) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- bridge_case_reason : reasons given per case (7,050 rows), composite PK
-- ---------------------------------------------------------------------
CREATE TABLE bridge_case_reason (
    case_id  INT           NOT NULL,
    reason   VARCHAR(100)  NOT NULL,   -- max seen: 47
    PRIMARY KEY (case_id, reason),
    FOREIGN KEY (case_id) REFERENCES fact_case (case_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- Indexes for the columns filtered / joined on most.
-- (InnoDB already builds an index for each foreign key; for
-- bridge_case_title.row_index it silently replaces that automatic one with
-- the explicit index below, so there is no duplicate.)
-- ---------------------------------------------------------------------
CREATE INDEX idx_fact_case_year        ON fact_case (year);
CREATE INDEX idx_fact_case_state       ON fact_case (state);
CREATE INDEX idx_bridge_case_title_row ON bridge_case_title (row_index);

-- ---------------------------------------------------------------------
-- fact_case_deduped : fact_case without probable duplicates (5,372 rows).
-- Currently this removes only case 4564 (duplicate of 3939). Because it is
-- SELECT *, re-run this file if columns are ever added to fact_case.
-- ---------------------------------------------------------------------
CREATE VIEW fact_case_deduped AS
SELECT * FROM fact_case
WHERE probable_duplicate_of_case_id IS NULL;
