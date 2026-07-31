"""Cleaning tests.

The strings below are copied out of real pages on the target site rather than
invented, so a change in the site's markup shows up here as a failure instead
of as a quietly wrong record.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.clean import (
    category_from_breadcrumbs,
    clean_description,
    clean_record,
    corpus_text,
    parse_availability,
    parse_price,
)

# Observed on /catalogue/a-light-in-the-attic_1000/: the template renders a
# truncated preview and then the full text, and closes with "...more".
DUPLICATED = (
    "It's hard to imagine a world without A Light in the Attic. This now-classic "
    "collection of poetry and drawings from Shel Silverstein celebrates its 20th "
    "anniversary with this special edition. Silverstein's humorous and creative verse "
    "can amuse the dowdiest of readers. Lemon-faced adults and fidgety kids sit still "
    "and read these rhythmic words and laugh and smile and love th "
    "It's hard to imagine a world without A Light in the Attic. This now-classic "
    "collection of poetry and drawings from Shel Silverstein celebrates its 20th "
    "anniversary with this special edition. Silverstein's humorous and creative verse "
    "can amuse the dowdiest of readers. Lemon-faced adults and fidgety kids sit still "
    "and read these rhythmic words and laugh and smile and love that Silverstein. ...more"
)


def test_price_keeps_the_number_and_the_currency():
    assert parse_price("£51.77") == (51.77, "GBP")
    assert parse_price("£0.00") == (0.0, "GBP")
    assert parse_price("£1,299.50") == (1299.5, "GBP")


def test_price_survives_the_mojibake_a_naive_decode_produces():
    # What the raw bytes look like if the ISO-8859-1 fallback wins. The number
    # is still recoverable, and it must not be read as part of the amount.
    assert parse_price("Â£51.77")[0] == 51.77


def test_price_of_nothing_is_none_not_zero():
    assert parse_price(None) == (None, None)
    assert parse_price("") == (None, None)
    assert parse_price("Free") == (None, None)


def test_availability_splits_the_flag_from_the_count():
    assert parse_availability("In stock (22 available)") == (True, 22)
    assert parse_availability("In stock") == (True, None)


def test_out_of_stock_is_false_not_truthy():
    assert parse_availability("Out of stock") == (False, 0)


def test_duplicated_opening_is_removed_once():
    cleaned = clean_description(DUPLICATED)
    assert cleaned.startswith("It's hard to imagine")
    assert cleaned.count("It's hard to imagine a world without") == 1
    assert cleaned.endswith("love that Silverstein.")
    assert len(cleaned) < len(DUPLICATED) / 1.5


def test_a_description_that_genuinely_repeats_is_left_alone():
    text = "Buy now. " * 12
    assert clean_description(text) == text.strip()


def test_short_descriptions_are_untouched_apart_from_whitespace():
    assert clean_description("  A   short  note. ") == "A short note."


def test_category_is_the_crumb_before_the_title():
    crumbs = ["Home", "Books", "Poetry", "A Light in the Attic"]
    assert category_from_breadcrumbs(crumbs) == "Poetry"


def test_category_is_none_when_the_trail_does_not_carry_one():
    assert category_from_breadcrumbs(["Home", "Books", "A Light in the Attic"]) is None
    assert category_from_breadcrumbs([]) is None


def test_record_is_typed_end_to_end():
    raw = {
        "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "title": "A Light in the Attic",
        "price": "£51.77",
        "availability": "In stock (22 available)",
        "rating_word": "Three",
        "description": DUPLICATED,
        "breadcrumbs": ["Home", "Books", "Poetry", "A Light in the Attic"],
        "image_url": "https://books.toscrape.com/media/x.jpg",
        "upc": "a897fe39b1053632",
        "price_excl_tax": "£51.77",
        "price_incl_tax": "£51.77",
        "tax": "£0.00",
        "review_count": "0",
    }
    record = clean_record(raw)
    assert record["id"] == "a897fe39b1053632"
    assert record["price"] == 51.77 and record["currency"] == "GBP"
    assert record["rating"] == 3
    assert record["in_stock"] is True and record["stock_count"] == 22
    assert record["tax"] == 0.0
    assert record["review_count"] == 0
    assert record["category"] == "Poetry"


def test_corpus_text_names_the_book_it_describes():
    record = {
        "title": "A Light in the Attic",
        "category": "Poetry",
        "description": "Poems and drawings.",
    }
    text = corpus_text(record)
    assert text.startswith("A Light in the Attic")
    assert "Category: Poetry" in text
    assert text.endswith("Poems and drawings.")


def test_missing_fields_do_not_crash_the_record():
    record = clean_record({"url": "https://x.test/book", "title": "Untitled"})
    assert record["id"] == "https://x.test/book"
    assert record["price"] is None
    assert record["rating"] is None
    assert record["in_stock"] is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"pass  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}  {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
