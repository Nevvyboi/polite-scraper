"""robots.txt fetching and rule matching.

Written by hand rather than using urllib.robotparser for two reasons: the
stdlib parser drops Crawl-delay for any group it did not select, and when a
page is refused I want to report the exact line that refused it. A crawler
that cannot say why it skipped a URL is not auditable.

Matching follows RFC 9309: the longest matching rule wins, Allow beats
Disallow on a tie, `*` matches any run of characters and `$` anchors the end.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field


@dataclass
class Rule:
    allow: bool
    pattern: str
    line_no: int

    def __post_init__(self):
        self.regex = _pattern_to_regex(self.pattern)

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None

    @property
    def specificity(self) -> int:
        return len(self.pattern)

    def __str__(self) -> str:
        verb = "Allow" if self.allow else "Disallow"
        return f"line {self.line_no}: {verb}: {self.pattern}"


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class Decision:
    allowed: bool
    reason: str


def _pattern_to_regex(pattern: str) -> re.Pattern:
    anchored_end = pattern.endswith("$")
    if anchored_end:
        pattern = pattern[:-1]
    parts = [re.escape(p) for p in pattern.split("*")]
    body = ".*".join(parts)
    return re.compile(body + ("$" if anchored_end else ""))


class Robots:
    """The policy one host publishes, and the answers it gives about paths."""

    def __init__(self, groups: list[Group], agent: str, source: str):
        self.source = source
        self.agent = agent
        self._group = _select_group(groups, agent)
        self.crawl_delay = self._group.crawl_delay if self._group else None

    @classmethod
    def parse(cls, text: str, agent: str, source: str = "robots.txt") -> "Robots":
        groups: list[Group] = []
        current: Group | None = None
        starting_new_group = True

        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "user-agent":
                if not starting_new_group or current is None:
                    current = Group()
                    groups.append(current)
                    starting_new_group = True
                current.agents.append(value.lower())
                continue

            if current is None:
                # Rules before any User-agent line belong to nobody. Skip them
                # rather than guessing, and let the caller see a permissive
                # result they can check against the raw file.
                continue

            starting_new_group = False
            if key in ("allow", "disallow"):
                if key == "disallow" and value == "":
                    continue
                current.rules.append(Rule(key == "allow", value, line_no))
            elif key == "crawl-delay":
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass

        return cls(groups, agent, source)

    @classmethod
    def allow_all(cls, agent: str, source: str) -> "Robots":
        return cls([], agent, source)

    @classmethod
    def deny_all(cls, agent: str, source: str) -> "Robots":
        group = Group(agents=["*"], rules=[Rule(False, "/", 0)])
        return cls([group], agent, source)

    def check(self, url: str) -> Decision:
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        if self._group is None:
            return Decision(True, f"no group in {self.source} applies to {self.agent}")

        winner: Rule | None = None
        for rule in self._group.rules:
            if not rule.matches(path):
                continue
            if winner is None or rule.specificity > winner.specificity:
                winner = rule
            elif rule.specificity == winner.specificity and rule.allow:
                winner = rule

        if winner is None:
            return Decision(True, f"no rule in {self.source} matches {path}")
        return Decision(winner.allow, f"{self.source} {winner}")


def _select_group(groups: list[Group], agent: str) -> Group | None:
    """Pick the group with the longest User-agent token our name starts with.

    `*` is the fallback and only used when no named group matches.
    """
    agent = agent.lower()
    best: Group | None = None
    best_len = -1
    fallback: Group | None = None

    for group in groups:
        for name in group.agents:
            if name == "*":
                if fallback is None:
                    fallback = group
                continue
            if agent.startswith(name) and len(name) > best_len:
                best, best_len = group, len(name)

    return best or fallback
