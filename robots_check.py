#!/usr/bin/env python3
"""Ask a host's robots.txt whether we may fetch some URLs, and fetch nothing else.

    python robots_check.py https://www.wikipedia.org/wiki/Web_scraping

The target site publishes no robots.txt, so running the crawler against it
never exercises a Disallow rule. This points the same parser at hosts that do
publish one, which is the only way to show the gate works on real policy
rather than on fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scraper.fetch import Fetcher

DEFAULTS = [
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://en.wikipedia.org/w/index.php?title=Web_scraping&action=edit",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://github.com/Nevvyboi/polite-scraper",
    "https://github.com/Nevvyboi/polite-scraper/search?q=robots",
]


def main(argv):
    urls = argv or DEFAULTS
    fetcher = Fetcher(cache_dir=Path("data/cache"), delay_floor=1.0)
    try:
        for url in urls:
            decision = fetcher.allowed(url)
            verdict = "allowed" if decision.allowed else "REFUSED"
            print(f"{verdict:8}  {url}\n          {decision.reason}\n")
    finally:
        fetcher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
