"""
config.py - settings shared by several scripts, so each list lives in ONE place.

Scripts 2 and 3 do:   from config import ...
(this works because the scripts are run as `python scripts/xx.py`, which puts the
scripts folder on Python's import path)
"""

import re

# type_format values for cases about books (case_scope = "book")
BOOK_TYPE_FORMATS = ["Book", "Graphic Novel", "Textbook"]

# type_format values for cases that contain no books at all (case_scope = "non_book").
# A title is never linked as a "book" because of a case of one of these types.
NON_BOOK_TYPE_FORMATS = [
    "Film", "Music", "Magazine", "Game", "Newspaper", "Database", "Pamphlet",
    "Student publication", "Meeting Room", "Local Resource",
]

# type_format values for cases that can hold books AND other items (case_scope = "mixed").
# A title in one of these cases is only called a non-book when it is clearly not a book.
MIXED_TYPE_FORMATS = [
    "Display", "Hate Crime", "Program", "Reading List", "Online resource", "Play",
    "Social Media", "Exhibit", "Performance",
]

# A dim_title title that carries a film / screenplay / musical / TV marker in
# parentheses or brackets, e.g. "The Help (Screenplay)" or "Show [DVD]". Such an
# entry is an adaptation, not a book. The pattern deliberately does NOT match
# "[series]" or "(Series)": those are real book series.
NON_BOOK_TITLE_MARKER = re.compile(
    r"\((film|screenplay|musical|movie|tv[^)]*|disney|documentary|sound recording|video)\)"
    r"|\[(pbs series|animated short|sound recording|video|film|tv series|dvd)\]",
    re.IGNORECASE,
)
