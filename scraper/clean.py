"""Raw page strings to typed records.

Three problems on this site are worth naming, because each one silently
produces a plausible wrong answer rather than an error:

  * Prices arrive as "£51.77". Kept as a string they sort lexically, so £9.99
    lands above £51.77 in any downstream ranking.
  * Availability arrives as "In stock (22 available)". A truthiness check on
    that string is true for "Out of stock" as well.
  * Descriptions arrive with a truncated preview glued to the front of the
    full text, so an embedding of the raw field weights the opening twice.
"""

from __future__ import annotations

import html
import re

RATING_VALUES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

PRICE = re.compile(r"([£$€])?\s*([0-9][0-9,]*\.?[0-9]*)")
STOCK_COUNT = re.compile(r"\(\s*(\d+)\s+available\s*\)", re.IGNORECASE)
WHITESPACE = re.compile(r"\s+")

CURRENCIES = {"£": "GBP", "$": "USD", "€": "EUR"}

# Shortest repeated opening we will remove when there is no truncated word to
# confirm it. Anything shorter is more likely to be a description that repeats
# itself on purpose than a template artefact.
MIN_DUPLICATE = 100


def clean_record(raw: dict) -> dict:
    amount, currency = parse_price(raw.get("price"))
    in_stock, count = parse_availability(raw.get("availability"))
    description = clean_description(raw.get("description"))
    title = collapse(raw.get("title"))
    category = category_from_breadcrumbs(raw.get("breadcrumbs") or [])

    return {
        "id": raw.get("upc") or raw["url"],
        "upc": raw.get("upc"),
        "url": raw["url"],
        "title": title,
        "category": category,
        "description": description,
        "price": amount,
        "currency": currency,
        "price_excl_tax": parse_price(raw.get("price_excl_tax"))[0],
        "price_incl_tax": parse_price(raw.get("price_incl_tax"))[0],
        "tax": parse_price(raw.get("tax"))[0],
        "rating": RATING_VALUES.get(raw.get("rating_word") or ""),
        "in_stock": in_stock,
        "stock_count": count,
        "review_count": to_int(raw.get("review_count")),
        "image_url": raw.get("image_url"),
    }


def collapse(text: str | None) -> str | None:
    if text is None:
        return None
    return WHITESPACE.sub(" ", html.unescape(text)).strip() or None


def parse_price(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    match = PRICE.search(text)
    if not match:
        return None, None
    symbol, digits = match.groups()
    try:
        amount = float(digits.replace(",", ""))
    except ValueError:
        return None, None
    return amount, CURRENCIES.get(symbol or "")


def parse_availability(text: str | None) -> tuple[bool | None, int | None]:
    if not text:
        return None, None
    normalised = collapse(text).lower()
    count_match = STOCK_COUNT.search(normalised)
    count = int(count_match.group(1)) if count_match else None

    if "out of stock" in normalised or "unavailable" in normalised:
        return False, count or 0
    if "in stock" in normalised or "available" in normalised:
        return True, count
    return None, count


def clean_description(text: str | None) -> str | None:
    """Undo the two things the template does to the description.

    The page shows a truncated preview and then the full text, concatenated,
    and ends the whole thing with "...more". Both are presentation, and both
    survive into the raw string.
    """
    text = collapse(text)
    if not text:
        return None

    text = re.sub(r"\s*\.\.\.\s*more\s*$", "", text)
    text = _strip_repeated_opening(text)
    return text.strip() or None


def _strip_repeated_opening(text: str) -> str:
    """Drop a leading copy of the opening if the full text follows it.

    The tell is that the preview is cut mid-word. So it is not enough for the
    opening to appear twice: the first copy has to end in a fragment that the
    second copy continues. "and love th" followed by "and love that" is a
    truncation. "Buy now." followed by "Buy now." is a description that simply
    repeats itself, and gets left alone.

    Roughly a third of the previews on this site happen to land exactly on a
    word boundary, and those leave no fragment to match on. For those the
    duplicate is an exact prefix of what follows, which on its own is far too
    weak a signal, so it is only accepted above MIN_DUPLICATE characters. The
    truncation is fixed length: measured across 1000 books, every duplicate
    found was between 368 and 376 characters.
    """
    head = text[:40]
    if len(head) < 40:
        return text

    start = text.find(head, 1)
    if start == -1 or start > len(text) // 2 + 1:
        return text

    duplicate = text[:start].rstrip()
    remainder = text[start:]
    if len(remainder) <= len(duplicate):
        return text

    body, _, fragment = duplicate.rpartition(" ")
    if not body or not fragment or not remainder.startswith(body):
        return text

    continues = remainder[len(body):].lstrip().split(" ", 1)[0]
    if continues.startswith(fragment) and len(continues) > len(fragment):
        return remainder

    if len(duplicate) >= MIN_DUPLICATE and remainder.startswith(duplicate):
        return remainder
    return text


def category_from_breadcrumbs(crumbs: list[str]) -> str | None:
    """The last crumb before the product's own title.

    The trail is Home, Books, <category>, <title>. Anything shorter means the
    page did not carry a category, which is worth recording as None rather
    than guessing at "Books".
    """
    trail = [collapse(c) for c in crumbs]
    trail = [c for c in trail if c and c.lower() not in ("home", "books")]
    if len(trail) < 2:
        return None
    return trail[-2]


def to_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.search(r"-?\d+", text)
    return int(digits.group()) if digits else None


def corpus_text(record: dict) -> str:
    """The passage a retriever would embed.

    Title and category are prepended because a description alone rarely names
    the book, and a chunk that cannot be matched on its own title is close to
    useless in retrieval.
    """
    parts = [record["title"]]
    if record.get("category"):
        parts.append(f"Category: {record['category']}")
    if record.get("description"):
        parts.append(record["description"])
    return "\n\n".join(p for p in parts if p)
