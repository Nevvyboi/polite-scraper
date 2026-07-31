"""HTML to raw strings. No typing, no normalising, no judgement.

Keeping extraction separate from cleaning means the cleaning tests can run on
captured strings without a network or a parser, and a selector that rots shows
up as a missing field rather than as a wrong number three stages later.
"""

from __future__ import annotations

import urllib.parse

from bs4 import BeautifulSoup

RATING_WORDS = ("One", "Two", "Three", "Four", "Five")


class MissingField(Exception):
    pass


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def catalogue_links(html: str, page_url: str) -> tuple[list[str], str | None]:
    """Product links on a listing page, plus the next listing page."""
    doc = soup(html)
    products = [
        urllib.parse.urljoin(page_url, a["href"])
        for a in doc.select("article.product_pod h3 a[href]")
    ]
    nxt = doc.select_one("li.next a[href]")
    return products, urllib.parse.urljoin(page_url, nxt["href"]) if nxt else None


def product_fields(html: str, url: str) -> dict:
    """Every field we want off a product page, exactly as the page states it."""
    doc = soup(html)
    main = doc.select_one("div.product_main")
    if main is None:
        raise MissingField("div.product_main is absent, the page is not a product")

    title = main.select_one("h1")
    if title is None:
        raise MissingField("h1 title")

    table = {
        row.th.get_text(strip=True): row.td.get_text(strip=True)
        for row in doc.select("table.table-striped tr")
        if row.th and row.td
    }

    image = doc.select_one("#product_gallery img[src]")
    breadcrumbs = [li.get_text(strip=True) for li in doc.select("ul.breadcrumb li")]

    return {
        "url": url,
        "title": title.get_text(strip=True),
        "price": _text(main.select_one("p.price_color")),
        "availability": _text(main.select_one("p.availability")),
        "rating_word": _rating_word(main.select_one("p.star-rating")),
        "description": _description(doc),
        "breadcrumbs": breadcrumbs,
        "image_url": urllib.parse.urljoin(url, image["src"]) if image else None,
        "upc": table.get("UPC"),
        "price_excl_tax": table.get("Price (excl. tax)"),
        "price_incl_tax": table.get("Price (incl. tax)"),
        "tax": table.get("Tax"),
        "review_count": table.get("Number of reviews"),
    }


def _text(node) -> str | None:
    return node.get_text(" ", strip=True) if node else None


def _rating_word(node) -> str | None:
    if node is None:
        return None
    for word in RATING_WORDS:
        if word in node.get("class", []):
            return word
    return None


def _description(doc) -> str | None:
    """The paragraph after the description header.

    There is no class or id on the paragraph itself, only on the header div
    above it, so this walks forward rather than selecting directly.
    """
    header = doc.select_one("#product_description")
    if header is None:
        return None
    paragraph = header.find_next_sibling("p")
    return paragraph.get_text(" ", strip=True) if paragraph else None
