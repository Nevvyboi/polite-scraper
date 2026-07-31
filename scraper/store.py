"""Where records land.

SQLite is the working store because the run needs to be resumable and
idempotent: a crawl interrupted at book 300 should carry on rather than start
again, and running twice should leave one row per book, not two. UPC is the
site's own identifier and makes a natural primary key.

JSONL is the export, because that is what next week's retrieval step wants to
read line by line.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .clean import corpus_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id             TEXT PRIMARY KEY,
    upc            TEXT,
    url            TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    category       TEXT,
    description    TEXT,
    price          REAL,
    currency       TEXT,
    price_excl_tax REAL,
    price_incl_tax REAL,
    tax            REAL,
    rating         INTEGER,
    in_stock       INTEGER,
    stock_count    INTEGER,
    review_count   INTEGER,
    image_url      TEXT,
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS books_category ON books (category);
CREATE INDEX IF NOT EXISTS books_price ON books (price);
"""

COLUMNS = [
    "id", "upc", "url", "title", "category", "description", "price", "currency",
    "price_excl_tax", "price_incl_tax", "tax", "rating", "in_stock",
    "stock_count", "review_count", "image_url",
]


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def seen_urls(self) -> set[str]:
        return {row["url"] for row in self.db.execute("SELECT url FROM books")}

    def upsert(self, record: dict) -> str:
        """Insert or update one book. Returns "new" or "updated"."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        values = [record.get(c) for c in COLUMNS]
        placeholders = ", ".join("?" for _ in COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id")

        existing = self.db.execute(
            "SELECT 1 FROM books WHERE id = ?", (record["id"],)
        ).fetchone()

        self.db.execute(
            f"""INSERT INTO books ({", ".join(COLUMNS)}, first_seen_at, updated_at)
                VALUES ({placeholders}, ?, ?)
                ON CONFLICT(id) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
            values + [now, now],
        )
        self.db.commit()
        return "updated" if existing else "new"

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM books").fetchone()[0]

    def summary(self) -> dict:
        row = self.db.execute(
            """SELECT COUNT(*) AS books,
                      COUNT(DISTINCT category) AS categories,
                      ROUND(AVG(price), 2) AS mean_price,
                      SUM(CASE WHEN description IS NULL THEN 1 ELSE 0 END) AS missing_description,
                      SUM(CASE WHEN in_stock = 1 THEN 1 ELSE 0 END) AS in_stock
               FROM books"""
        ).fetchone()
        return dict(row)

    def export_jsonl(self, path: Path) -> int:
        """Write the corpus one record per line, ordered so diffs stay small."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in self.db.execute("SELECT * FROM books ORDER BY id"):
                record = dict(row)
                record["in_stock"] = bool(record["in_stock"]) if record["in_stock"] is not None else None
                document = {
                    "id": record["id"],
                    "url": record["url"],
                    "text": corpus_text(record),
                    "metadata": {
                        k: record[k]
                        for k in (
                            "title", "category", "price", "currency", "rating",
                            "in_stock", "stock_count", "review_count", "upc",
                            "image_url", "updated_at",
                        )
                    },
                }
                handle.write(json.dumps(document, ensure_ascii=False) + "\n")
                written += 1
        return written
