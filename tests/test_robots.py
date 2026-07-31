import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.robots import Robots

AGENT = "flyrank-polite-scraper"

POLICY = """
# a policy with enough shape to be worth testing against

User-agent: *
Disallow: /private/
Disallow: /*.json$
Crawl-delay: 10

User-agent: flyrank-polite-scraper
Disallow: /
Allow: /catalogue/
Disallow: /catalogue/secret/
Crawl-delay: 2

User-agent: badbot
Disallow: /
"""


def robots(text=POLICY, agent=AGENT):
    return Robots.parse(text, agent, source="test policy")


def test_named_group_wins_over_wildcard():
    # Our group disallows everything, so /private/ is refused either way. What
    # this checks is which line did the refusing: the wildcard group must not
    # be consulted at all once a named group matches.
    r = robots()
    assert r.crawl_delay == 2
    decision = r.check("https://x.test/private/notes")
    assert decision.allowed is False
    assert "Disallow: /\n" not in decision.reason
    assert decision.reason.endswith("Disallow: /")


def test_wildcard_used_when_no_named_group_matches():
    r = robots(agent="some-other-bot")
    assert r.crawl_delay == 10
    assert r.check("https://x.test/private/notes").allowed is False


def test_longest_match_wins():
    r = robots()
    assert r.check("https://x.test/").allowed is False
    assert r.check("https://x.test/catalogue/page-1.html").allowed is True
    assert r.check("https://x.test/catalogue/secret/x.html").allowed is False


def test_allow_wins_a_tie():
    r = robots("User-agent: *\nDisallow: /a\nAllow: /a\n")
    assert r.check("https://x.test/a").allowed is True


def test_end_anchor_and_wildcard():
    r = robots(agent="some-other-bot")
    assert r.check("https://x.test/data/file.json").allowed is False
    assert r.check("https://x.test/data/file.json.html").allowed is True


def test_empty_disallow_means_allow_everything():
    r = robots("User-agent: *\nDisallow:\n")
    assert r.check("https://x.test/anything").allowed is True


def test_query_string_is_part_of_the_path():
    r = robots("User-agent: *\nDisallow: /*?sort=\n")
    assert r.check("https://x.test/list?sort=price").allowed is False
    assert r.check("https://x.test/list").allowed is True


def test_consecutive_agent_lines_share_one_group():
    r = robots("User-agent: alpha\nUser-agent: beta\nDisallow: /x\n", agent="beta")
    assert r.check("https://x.test/x").allowed is False


def test_comments_are_stripped():
    r = robots("User-agent: *\nDisallow: /x # keep out\n")
    assert r.check("https://x.test/x").allowed is False


def test_refusal_names_the_line_that_refused():
    r = robots()
    decision = r.check("https://x.test/catalogue/secret/x.html")
    assert decision.allowed is False
    assert "Disallow: /catalogue/secret/" in decision.reason


def test_deny_all_is_used_when_the_file_cannot_be_read():
    r = Robots.deny_all(AGENT, "unreachable")
    assert r.check("https://x.test/").allowed is False


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
