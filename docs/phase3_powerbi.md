# Phase 3: Power BI Dashboard

[Back to the main README](../README.md)

Interactive dashboard on the ALA banned and challenged books data (2016-2025), connected to the local MySQL database built in Phase 2.

**Files**
- [`powerbi/dashboard.pdf`](../powerbi/dashboard.pdf): static export of the report pages
- [`images/`](../images/): `01_overview.jpg`, `02_reasons.jpg`, `03_who_where.jpg`, `04_title_detail.jpg`
- `dashboard.pbix` (the Power BI report) is **not** in this repo. It embeds a full copy of the data, including the Google Books text. 

![Overview page](../images/01_overview.jpg)

---

## 1. Dashboard Overview

| Page | Question it answers | Visuals |
|---|---|---|
| **Overview** | How big is the problem and how has it changed? | 4 KPI cards, line chart by year, state map |
| **Reasons & Content** | What gets challenged, and why? | Reasons bar chart, age group chart, fiction vs. non-fiction donut, Title Explorer table |
| **Who & Where** | Who challenges books, and where? | Initiator bar chart, library type donut, top 15 states |
| **Title Detail** (drill-through) | What is this specific book? | Title, summary, maturity rating, age group, author, fiction/non-fiction cards |

## 2. Connection and Setup

1. Installed Power BI Desktop and the MySQL connector (MySQL Connector/NET), which Power BI's MySQL source requires.
2. **Get Data → MySQL database** with server `localhost:3306`, database `banned_books_db`, Database credentials (a local MySQL user). 
3. Mode: **Import**. Chosen over DirectQuery because the database is small and local; Import is faster and makes the `.pbix` self-contained.
4. Loaded five objects: `dim_title`, `fact_case`, `fact_case_deduped` (view), `bridge_case_title`, `bridge_case_reason`.
5. In Power Query, checked column types. `isbn` stays **text**, since ISBNs lose leading zeros as numbers. Power BI prefixed table names with the database name (for example `banned_books_db fact_case_deduped`), so all five tables were **renamed to drop the prefix** and keep DAX readable.

## 3. Data Model

Star/snowflake schema: one fact table, one dimension, two bridge tables resolving the many-to-many fields. All relationships are one-to-many with **single** cross-filter direction.

| From (1) | To (many) | Key |
|---|---|---|
| `fact_case_deduped` | `bridge_case_title` | `case_id` |
| `fact_case_deduped` | `bridge_case_reason` | `case_id` |
| `dim_title` | `bridge_case_title` | `row_index` |

**Decision: `fact_case_deduped` instead of `fact_case`.** Power BI auto-linked `fact_case` to the bridges. These were deleted and re-created from `fact_case_deduped` so every measure runs on the deduplicated case set. `fact_case` remains loaded but is not used in visuals.

## 4. DAX Measures

All five measures live in `fact_case_deduped`, plus `Total States`.

```dax
Total Cases = DISTINCTCOUNT(fact_case_deduped[case_id])

Total Titles Challenged = DISTINCTCOUNT(bridge_case_title[row_index])

Total Challenges (sum) = SUM(fact_case_deduped[count_titles_challenged])

YoY % Change =
VAR CurrentYear  = MAX(fact_case_deduped[year])
VAR CurrentTotal = CALCULATE([Total Challenges (sum)], fact_case_deduped[year] = CurrentYear)
VAR PriorTotal   = CALCULATE([Total Challenges (sum)], fact_case_deduped[year] = CurrentYear - 1)
RETURN DIVIDE(CurrentTotal - PriorTotal, PriorTotal)

% Needing Manual Review =
DIVIDE(
    CALCULATE(COUNTROWS(bridge_case_title), bridge_case_title[needs_manual_review] = TRUE),
    COUNTROWS(bridge_case_title)
)

Total States =
CALCULATE(
    DISTINCTCOUNT(fact_case_deduped[state]),
    fact_case_deduped[state] <> "INT",
    fact_case_deduped[state] <> ""
)
```

### Bug found and fixed: YoY % Change

The first version of `YoY % Change` showed **500.55%**. It used `MAX(year)` to find the prior year but divided the **all-years** total by the prior-year total. I cross-checked against the Phase 2 SQL YoY query (2025 vs. 2024 = +36.58%) and found the mismatch. The fixed measure computes both the current-year and prior-year totals explicitly and now returns **36.58%**, matching SQL. The card label is "YoY % Change (2025 vs 2024)".

No date table was built: `year` is a plain integer, so one would solve a problem this project does not have.

## 5. Data Quality Handling

| Issue | Handling |
|---|---|
| `state` contains `NULL` (4 corrupted cases) and the literal `"INT"` (international) | The column was **not** altered. Both values are filtered out only where they would distort the result: the Overview map, the `Total States` measure, and the Top 15 States chart. |
| Blank `age_group` and `fiction_or_nonfiction` (unclassified titles) | Blanks filtered out on the age group chart and the fiction donut (visual-level filter). |
| Blank `location` (about 3 cases) | Removed from the library type donut. |
| `isbn` stored as text | Kept as text, never used as a join key. Joins use `row_index` and `case_id`. |

**Why totals differ across pages:** the fiction donut totals 9.720K versus 10.283K on the Overview card, because 562 titles have no fiction/non-fiction value (9,720 + 562 = 10,282 distinct titles in the file; the card shows one more because it counts the blank as a value, see the note under the cards in section 6). The library type donut totals 5.369K versus 5.372K cases because of the blank-location cases. The age group chart likewise excludes blanks. These are intentional exclusions, not errors.

## 6. Pages in Detail

### Page 1: Overview
- **Cards:** Total Cases **5.372K**, Total Titles Challenged **10.283K**, Total States **51**, YoY % Change **36.58%**. 
  - Check against the files: `bridge_case_title.csv` has 10,282 distinct `row_index` values and 774 rows with no `row_index`, so the card is one higher than the data. `DISTINCTCOUNT` counts a blank as a value (`DISTINCTCOUNTNOBLANK` does not), and the 774 rows with no `row_index` add one, so 10,282 + 1 = 10,283. The card is not filtered to `fact_case_deduped`, so it also includes 6 titles that appear only in case 4564 (10,276 distinct titles without that case). The fiction donut total of 9,720 equals 10,282 minus the 562 titles with no fiction value.
- **Line chart:** `year` × `Total Challenges (sum)`, with data labels and markers. Year is set to "Don't summarize".
- **Shape Map** (map type USA: States): `state` as location, `Total Challenges (sum)` as color saturation. Shape Map was used instead of Filled Map because it keys on state abbreviations from a built-in shape file rather than depending on Bing geocoding. It required enabling **File → Options → Preview features → Shape map**. Filter: `state` is not `INT` and is not blank.

### Page 2: Reasons & Content
- **Challenges by Reason:** `bridge_case_reason[reason]` × distinct count of `case_id`, sorted descending, data labels on.
- **Titles Challenged by Age Group:** `dim_title[age_group]` × `Total Titles Challenged`, blanks removed.
- **Fiction vs Non-Fiction:** donut of `dim_title[fiction_or_nonfiction]`, blanks removed, category and percent labels, legend kept.
- **Title Explorer:** table of `dim_title[title]` and `dim_title[author]`. It is the **source for drill-through**: right-click a title → Drill through → Title Detail.

### Page 3: Who & Where
- **Challenges by Initiators:** `fact_case_deduped[initiator]` × `Total Cases`, sorted descending, data labels formatted as whole numbers.
- **Challenges by Library Type:** donut of `fact_case_deduped[location]` (Public Library, School Library, School), blank removed.
- **Top 15 States with Most Challenges:** `state` × `Total Challenges (sum)`, **Top N = 15** by value, sorted descending. `INT` and blank do not appear.

### Page 4: Title Detail (drill-through)
- Drill-through field: `dim_title[title]`. A back button is added automatically.
- Cards: Title, Summary, Maturity Rating, Age Group, Author, Fiction/Non-Fiction. Text fields use the **First** aggregation; with a title selected via drill-through, the filter narrows to one book, so "First" returns that book's value.
- Tested with a repeated title: the author changes with the book picked, so books that share a title are kept apart (162 titles are shared by 2 or more books, for example 10 books called "Abortion"). This holds when drilling from a visual that carries author, such as the Title Explorer table. 
- Tested end to end: right-clicking a title in the Title Explorer updates every card.

## 7. Key Findings (from the dashboard)

- **5,372 cases** covering **10,283 titles** (card value; 10,282 distinct `row_index` values in the file, see section 6) across **51** state codes (excluding `INT`).
- Challenges were flat at about 0.3-0.4K per year through 2020, then jumped in 2021 and peaked in **2023 (10.0K)**, dropped to 6.2K in 2024, and rose again to **8.4K in 2025 (+36.58%)**.
- **Florida (7.7K) and Texas (6.8K)** lead all states, followed by Tennessee (2.8K) and Iowa (2.1K).
- Top reasons: **Content Considered Sexually Explicit (1,902)** and **LGBTQIA+ Representation (1,889)**, far ahead of EDI Content (440) and Profanity (431).
- **Parents (1,635) and patrons (1,600)** initiate the most challenges, well ahead of pressure groups (633) and administrators (398).
- By setting: Public Library 45.8%, School Library 41.8%, School 12.4%.
- By genre: Fiction 69.8%, Non-Fiction 30.2%.
- By audience: Young Adult (4.9K) > Children (2.3K) > Adult (0.9K).

## 8. Limitations and Notes

- Challenge counts are self-reported to the ALA; reasons are assigned by the reporter, not verified.
- Title matching from cases to `dim_title` is approximate where it was not exact: 1,355 rows matched on first author only and 39 rows were fuzzy (see Phase 1). `bridge_case_title.needs_manual_review` flags 61 rows. Separately, 1,891 of 10,380 titles (18.2%) got their Google Books details by a title-and-author fuzzy match, and 303 got none.
- `YoY % Change` is based on `Total Challenges (sum)` (titles challenged per incident summed), not on case count, and compares the latest year in the current filter context to the prior year.
- "School" and "School Library" are separate source categories and were not merged; they may or may not overlap in meaning.
- The reasons chart can scroll, since there are about 20 reasons.
- Excluded blanks (above) mean category-level charts do not always sum to the Overview totals.


