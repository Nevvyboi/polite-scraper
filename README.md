# polite-scraper

A crawler for [books.toscrape.com](https://books.toscrape.com) that produces a
retrieval corpus, written so that the operator of the site would have no
reason to complain about it.

**FlyRank AI Internship, Backend AI Engineering, Week 5: The polite scraper.**

The pipeline is the assignment: fetch, parse, extract, clean, structure. The
part worth reading is everything wrapped around it.

## Run it

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python scrape.py --pages 2 --limit 10
```

```bash
./.venv/bin/python scrape.py --pages 50
```

The second command walks all 50 listing pages and all 1000 books. Measured:
**27 minutes, 1051 requests, 21.3 MB, 1000 records, no failures**, and 1043 of
those 1623 seconds spent waiting on purpose.

Once the pages are cached, changing a cleaning rule costs the site nothing:

```bash
./.venv/bin/python scrape.py --pages 50 --offline
```

That rebuilds the same 1000 record corpus from disk in **2.9 seconds with zero
requests**. It is the flag I reached for most while building.

Tests need no network:

```bash
./.venv/bin/python tests/test_robots.py && ./.venv/bin/python tests/test_clean.py
```

## What makes it polite

Politeness is not a comment in the source. Each of these is a behaviour you
can observe in the run report.

**It asks first.** `robots.txt` is fetched once per host and consulted before
every URL. A refusal carries the line that refused it, so a skipped page is
explainable rather than mysterious:

```
REFUSED   https://en.wikipedia.org/wiki/Special:Random
          https://en.wikipedia.org/robots.txt line 156: Disallow: /wiki/Special:
```

An unreachable or 5xx `robots.txt` closes the host rather than opening it. A
404 means no policy was published, which is permission, not an invitation, so
the delay floor still applies.

**It says who it is.** Every request carries a name, a project URL and an
address:

```
User-Agent: flyrank-polite-scraper/1.0 (+https://github.com/Nevvyboi/polite-scraper; nevintom2018@gmail.com)
```

A site owner who wants this stopped can find out who to tell in one log line.

**It waits.** Requests are serialised and spaced by the host's `Crawl-delay`,
or a one second floor when none is published. The report prints the smallest
gap actually measured between two requests, so the claim is checkable rather
than asserted.

**It does not ask twice.** Responses are cached on disk with their `ETag` and
`Last-Modified`, and re-runs send `If-None-Match`. The measured difference:

| | first run | second run |
|---|---|---|
| downloaded | 112.4 KB | 0.1 KB |
| 304 Not Modified | 0 | 6 of 7 |

**It backs off.** 429 and 5xx are retried with exponential backoff and jitter,
and `Retry-After` wins over the backoff when the server sends one.

**It stays in bounds.** One host, listing pages only, no query strings, a page
cap on every run, and the crawl stops at the first listing page that fails
rather than guessing at the next URL.

**It can be rebuilt without asking again.** `--offline` reparses and recleans
everything from the disk cache and sends nothing at all. Cleaning rules change
far more often than pages do, and there is no reason for the site to pay for
that.

## What the cleaning stage is for

Extraction and cleaning are separate modules on purpose. Parsing returns the
page's own strings and nothing else, so the cleaning tests run on captured
text with no network and no parser involved.

Three fields on this site produce a plausible wrong answer rather than an
error if you skip the cleaning:

| Raw | Problem | Cleaned |
|---|---|---|
| `Â£51.77` | The server sends `text/html` with no charset, so `requests` falls back to ISO-8859-1 and every pound sign becomes two characters | decoded from the document's own `<meta charset>`, `price: 51.77`, `currency: "GBP"` |
| `In stock (22 available)` | A truthiness check on this string is also true for `Out of stock` | `in_stock: true`, `stock_count: 22` |
| description | The template concatenates a truncated preview with the full text, so the opening appears twice and would be embedded twice | the duplicate opening is removed, on 895 of the 998 books that have one |

The description case is the one worth explaining. The preview is usually cut
mid-word, so the two copies are not identical and a plain "does this substring
repeat" check does not find it. The detector's main rule is that the first
copy must end in a fragment the second copy continues: `and love th` followed
by `and love that` is a truncation, `Buy now.` followed by `Buy now.` is a
description that repeats itself.

Roughly a third of the previews land exactly on a space, which leaves no
fragment to match on. Those are only removed when the repeated opening is over
100 characters, because an exact repeat on its own is far too weak a signal to
act on. Measured across the corpus, every duplicate found was between 368 and
376 characters long.

## Output

`out/corpus.jsonl`, one document per line, shaped for retrieval:

```json
{
  "id": "a897fe39b1053632",
  "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
  "text": "A Light in the Attic\n\nCategory: Poetry\n\nIt's hard to imagine a world without...",
  "metadata": {"title": "A Light in the Attic", "category": "Poetry", "price": 51.77,
               "currency": "GBP", "rating": 3, "in_stock": true, "stock_count": 22,
               "review_count": 0, "upc": "a897fe39b1053632", "updated_at": "..."}
}
```

`text` leads with the title and category because a description on its own
rarely names the book it describes, and a chunk that cannot be matched on its
own title is close to useless once it is embedded.

`data/books.sqlite` is the working store. It is keyed on the site's UPC, so an
interrupted crawl resumes and a repeated crawl updates in place. Running twice
leaves 1000 rows, not 2000.

`out/run-report.json` is written after every run. The full crawl:

```json
{
  "records": {"new": 1000, "updated": 0, "skipped": 0, "refused": 0, "unparsable": 0, "failed": 0},
  "http": {"network_requests": 1051, "served_from_cache": 0, "not_modified_304": 6,
           "refused_by_robots": 0, "retries": 0, "failed": 0, "kb_downloaded": 21294.3,
           "seconds_spent_waiting": 1043.3, "smallest_gap_between_requests": 1.235},
  "corpus": {"file": "out/corpus.jsonl", "lines": 1000},
  "database": {"books": 1000, "categories": 50, "mean_price": 35.07,
               "missing_description": 2, "in_stock": 1000},
  "wall_clock_seconds": 1623.4
}
```

1000 books across 50 categories, nothing refused, nothing retried, nothing
unparsable. The two missing descriptions are missing from the pages
themselves, not lost in parsing. The full log is in
[out/full-run.log](out/full-run.log).

A run that fetched nothing because robots refused it looks different from a
run that fetched everything and parsed nothing. Both are visible without
reading the log.

## robots_check

The target site returns 404 for `robots.txt`, so crawling it never exercises a
`Disallow` rule and proves nothing about the gate. `robots_check.py` points the
same parser at hosts that do publish a policy and prints the verdict for each
URL with the line number behind it. It fetches `robots.txt` and nothing else.

```bash
./.venv/bin/python robots_check.py https://github.com/Nevvyboi/polite-scraper/search?q=robots
```

Output from a real run is in [docs/robots-check.txt](docs/robots-check.txt).

## Layout

```
scrape.py            the pipeline, one stage per function
robots_check.py      ask a host's policy about some URLs, fetch nothing else
scraper/robots.py    robots.txt parsing and rule matching
scraper/fetch.py     identification, pacing, caching, conditional GET, backoff
scraper/parse.py     HTML to raw strings
scraper/clean.py     raw strings to typed records
scraper/store.py     SQLite upsert and JSONL export
tests/               26 tests, no network
docs/NOTES.md        decisions, and what the build got wrong first
```

## Limits

Named rather than discovered later.

- The 100 character floor on exact repeats is tuned to one site. A description
  that legitimately opens with the same long paragraph twice would lose one
  copy. I have not found one, and 103 descriptions in the corpus were left
  alone, so the rule is not simply firing on everything.
- A cached page whose server sent no `ETag` and no `Last-Modified` is served
  from cache indefinitely, because there is no cheap way to revalidate it. Use
  `--refresh` to force those.
- `robots.txt` path matching does not normalise percent-encoding before
  comparing, so a rule written as `/private/` and a URL written as
  `/%70rivate/` would not match. Every rule this crawler has met is written
  plainly.
- One request at a time by design. Concurrency and politeness are not
  incompatible, but they need a per-host budget rather than a global delay, and
  that is more machinery than one host needs.

## Next

The corpus is the input to Week 6. The fields that matter for retrieval are
already typed, so filtering by price or category before ranking needs no
further parsing.
