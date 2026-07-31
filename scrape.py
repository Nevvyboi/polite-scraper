#!/usr/bin/env python3
"""Run the pipeline: discover, fetch, parse, clean, store, export.

    python scrape.py --pages 3
    python scrape.py --pages 50 --out out/corpus.jsonl

Every stage is separable and every stage reports. A run that fetched nothing
because robots.txt refused it should look different from a run that fetched
everything and parsed nothing, and both should be obvious from the summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from scraper import parse
from scraper.clean import clean_record
from scraper.fetch import Fetcher, RobotsRefusal
from scraper.store import Store

ROOT = Path(__file__).resolve().parent
START_URL = "https://books.toscrape.com/catalogue/page-1.html"


def log(message: str):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def discover(fetcher: Fetcher, start: str, max_pages: int, limit: int | None) -> list[str]:
    """Walk the listing pages and collect product URLs, in order."""
    found: list[str] = []
    seen: set[str] = set()
    url = start
    pages = 0

    while url and pages < max_pages:
        try:
            response = fetcher.get(url)
        except RobotsRefusal as refusal:
            log(f"listing refused: {refusal.reason}")
            break

        if response.status != 200:
            log(f"listing {url} returned {response.status}, stopping discovery")
            break

        links, next_url = parse.catalogue_links(response.text, url)
        fresh = [u for u in links if u not in seen]
        seen.update(fresh)
        found.extend(fresh)
        pages += 1
        log(f"listing page {pages}: {len(fresh)} products ({len(found)} so far)"
            + (" [cached]" if response.from_cache else ""))

        if limit and len(found) >= limit:
            found = found[:limit]
            break
        url = next_url

    return found


def scrape(args) -> int:
    fetcher = Fetcher(
        cache_dir=ROOT / args.cache,
        delay_floor=args.delay,
        log=log,
    )
    store = Store(ROOT / args.db)
    started = time.monotonic()

    counts = {"new": 0, "updated": 0, "skipped": 0, "refused": 0, "unparsable": 0, "failed": 0}

    try:
        log(f"identifying as: {fetcher.session.headers['User-Agent']}")
        urls = discover(fetcher, args.start, args.pages, args.limit)
        log(f"discovered {len(urls)} product pages")

        already = store.seen_urls() if not args.refresh else set()
        if already:
            log(f"{len(already)} already stored, use --refresh to fetch them again")

        for index, url in enumerate(urls, start=1):
            if url in already:
                counts["skipped"] += 1
                continue

            try:
                response = fetcher.get(url)
            except RobotsRefusal as refusal:
                counts["refused"] += 1
                log(f"refused: {refusal.reason}")
                continue

            if response.status != 200:
                counts["failed"] += 1
                log(f"{url} returned {response.status}")
                continue

            try:
                raw = parse.product_fields(response.text, url)
            except parse.MissingField as missing:
                counts["unparsable"] += 1
                log(f"cannot parse {url}: {missing}")
                continue

            record = clean_record(raw)
            counts[store.upsert(record)] += 1

            if index % 20 == 0 or index == len(urls):
                log(f"{index}/{len(urls)} processed")

        written = store.export_jsonl(ROOT / args.out)
        elapsed = time.monotonic() - started

        report = {
            "records": counts,
            "http": fetcher.stats.as_dict(),
            "corpus": {"file": args.out, "lines": written},
            "database": store.summary(),
            "wall_clock_seconds": round(elapsed, 1),
        }
        print("\n" + json.dumps(report, indent=2))

        Path(ROOT / args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(ROOT / args.report).write_text(json.dumps(report, indent=2) + "\n")
        log(f"report written to {args.report}")

        return 0 if written else 1
    finally:
        fetcher.close()
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="A scraper that behaves itself.")
    parser.add_argument("--start", default=START_URL, help="first listing page")
    parser.add_argument("--pages", type=int, default=2, help="listing pages to walk")
    parser.add_argument("--limit", type=int, help="stop after this many product URLs")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="minimum seconds between requests when robots.txt sets no Crawl-delay")
    parser.add_argument("--db", default="data/books.sqlite")
    parser.add_argument("--cache", default="data/cache")
    parser.add_argument("--out", default="out/corpus.jsonl")
    parser.add_argument("--report", default="out/run-report.json")
    parser.add_argument("--refresh", action="store_true", help="re-fetch pages already stored")
    return scrape(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
