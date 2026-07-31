# Build notes

Written while building. The point of this file is the decisions that could
have gone the other way, and the three things the build got wrong first.

## Why the robots parser is hand written

`urllib.robotparser` is in the standard library and does the job. I did not
use it, for two reasons that both turned out to matter.

It discards `Crawl-delay` for every group it did not select. The interface
gives you a delay for a named agent only if that agent's own group carried
one, which means a host that sets a delay under `User-agent: *` and also has a
group for you hands you nothing. Getting the pacing wrong in the permissive
direction is exactly the failure this assignment is about.

More importantly, it answers `True` or `False` and nothing else. When a
crawler skips 40 URLs I want to know which rule did it, because "the parser
said no" is not something I can check. `Decision` carries the reason:

```
REFUSED   https://github.com/Nevvyboi/polite-scraper/search?q=robots
          https://github.com/robots.txt line 71: Disallow: /*q=
```

The cost is roughly 130 lines and the risk of getting the matching rules
wrong, which is why the matching rules have 11 tests.

## What a missing robots.txt means

RFC 9309 separates two cases that look similar and mean opposite things.

A 4xx means the host published no policy. That is permission by default. It is
not permission to go fast, so the delay floor still applies, and the run log
says so out loud rather than passing over it in silence.

A 5xx, a timeout or a connection error means the host has a policy and I could
not read it. Treating that as permission would mean an outage on the robots
endpoint quietly unlocks the whole site. The crawler closes the host instead.
401 and 403 are treated the same way: a policy exists and is being withheld.

`Robots.deny_all` is what makes that a single line at each call site rather
than a branch that could be forgotten.

## Three things that were wrong first

**A test that asserted the wrong thing.** The first version of
`test_named_group_wins_over_wildcard` expected `/private/notes` to be allowed,
on the reasoning that `Disallow: /private/` belongs to the wildcard group and
our named group should win. It failed. The named group in that fixture is
`Disallow: /` plus `Allow: /catalogue/`, so `/private/notes` is refused either
way, just by a different line. The code was right and the test was wrong. What
the test should check, and now does, is which line did the refusing.

Worth recording because the tempting move at that moment is to soften the
matching rules until the test goes green.

**Every price came back as `Â£51.77`.** The server sends `Content-Type:
text/html` with no charset. Per RFC 2616, `requests` falls back to ISO-8859-1
for `text/*`, so the UTF-8 pound sign is decoded as two Latin-1 characters. The
document declares UTF-8 in a `<meta>` tag, which the HTTP layer never looks at.

The fix is in `fetch._decode`: read the declared charset out of the first 4 KB
of the body and prefer it over the header. Two things I considered and did not
do. Hard-coding `resp.encoding = "utf-8"` works on this host and breaks on the
next one. Cleaning the mojibake downstream in `clean.py` treats the symptom and
leaves every other non-ASCII character mangled, and the descriptions on this
site contain plenty.

The regression test asserts the price is still recoverable from the mojibake
form, because a decode this dependent on a meta tag will eventually meet a page
that does not carry one.

**The description de-duplicator ate real text.** The site concatenates a
truncated preview with the full description, so the opening appears twice. An
embedding of the raw field weights the first 400 characters double, which is
the sort of defect that never surfaces as an error and quietly degrades every
retrieval built on top of it.

The first detector looked for the opening 40 characters appearing again and
cut there. `test_a_description_that_genuinely_repeats_is_left_alone` failed
immediately: a description that is a repeated line of marketing copy matches
that test and loses its opening.

The signal I was missing is that the preview is cut mid-word. `and love th`
followed by `and love that` is a truncation. `Buy now.` followed by `Buy now.`
is a description that repeats itself. The detector now requires the first copy
to end in a fragment that the second copy continues, and that distinguishes the
two cases exactly.

It also means a preview cut on a word boundary goes through untouched. I know
that and left it, because a looser rule is the one that damages real text, and
this site does not produce that case.

## Why parse and clean are separate modules

`parse.product_fields` returns the page's own strings and does no typing at
all. Everything that turns `"£51.77"` into `51.77` happens in `clean`, on
plain dictionaries.

That split buys two things. The cleaning tests run on text copied out of real
pages with no network and no HTML parser in the way, so they stay fast and
they stay honest about what the site actually serves. And a selector that rots
when the site changes shows up as a missing field at the parse stage, rather
than as a wrong number three stages downstream where it looks like a cleaning
bug.

## Why SQLite sits between the crawl and the corpus

The corpus is JSONL because that is what a retrieval step wants. Writing JSONL
directly from the crawl would have been fewer moving parts and worse:

A 1000 page crawl takes about twenty minutes. Interrupt it at book 600 and an
append-only file leaves you 600 records with no record of which URLs produced
them. `seen_urls()` off the database makes the next run skip what is already
stored, and that is the difference between a crawl you can stop and one you
have to babysit.

Run it twice against an append-only file and you get 2000 lines for 1000 books.
`ON CONFLICT(id) DO UPDATE` keyed on the site's own UPC makes the second run
update in place.

The export is regenerated from the database on every run, ordered by id, so
the committed corpus produces small diffs when the site changes rather than a
reshuffle.

## What I would do next

Per-host concurrency with a token bucket, so two hosts can be crawled at once
without either of them seeing more than one request per delay window. The
current design serialises everything globally, which is correct and slower
than it needs to be.

Content hashing on the stored body, so `updated_at` only moves when something
actually changed. Right now every `--refresh` run touches all 1000 rows even
though the 304s prove nothing was modified.
