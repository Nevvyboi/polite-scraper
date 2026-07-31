"""The HTTP layer, and every constraint that makes it welcome on a server.

Five things happen here that would not happen in a naive scraper:

  1. robots.txt is fetched once per host and consulted before every request.
  2. Requests are spaced by at least the host's Crawl-delay, or a floor if it
     publishes none.
  3. Responses are cached on disk with their validators, so a second run sends
     If-None-Match and usually gets a 304 that costs the host nothing.
  4. 429 and 5xx back off, honouring Retry-After when the server sends one.
  5. The User-Agent says who is asking and where to complain.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import requests

AGENT_TOKEN = "flyrank-polite-scraper"
CONTACT = "https://github.com/Nevvyboi/polite-scraper"
USER_AGENT = f"{AGENT_TOKEN}/1.0 (+{CONTACT}; nevintom2018@gmail.com)"

META_CHARSET = re.compile(
    rb"""<meta[^>]+charset=["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE
)

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class Response:
    url: str
    status: int
    text: str
    from_cache: bool = False


@dataclass
class Stats:
    requested: int = 0
    served_from_cache: int = 0
    not_modified: int = 0
    refused_by_robots: int = 0
    retried: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    slept: float = 0.0
    intervals: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        gaps = self.intervals
        return {
            "network_requests": self.requested,
            "served_from_cache": self.served_from_cache,
            "not_modified_304": self.not_modified,
            "refused_by_robots": self.refused_by_robots,
            "retries": self.retried,
            "failed": self.failed,
            "kb_downloaded": round(self.bytes_downloaded / 1024, 1),
            "seconds_spent_waiting": round(self.slept, 1),
            "smallest_gap_between_requests": round(min(gaps), 3) if gaps else None,
        }


class RobotsRefusal(Exception):
    def __init__(self, url: str, reason: str):
        super().__init__(f"{url} refused by {reason}")
        self.url = url
        self.reason = reason


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        delay_floor: float = 1.0,
        timeout: float = 20.0,
        max_attempts: int = 4,
        log=print,
    ):
        from .robots import Robots  # local import keeps the module graph flat

        self._Robots = Robots
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_floor = delay_floor
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.log = log
        self.stats = Stats()

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "From": "nevintom2018@gmail.com",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, deflate",
            }
        )

        self._robots: dict[str, object] = {}
        self._last_request_at: float | None = None

    def close(self):
        self.session.close()

    # robots

    def robots_for(self, url: str):
        host = _origin(url)
        if host in self._robots:
            return self._robots[host]

        policy_url = host + "/robots.txt"
        self._wait_turn(0.0)
        try:
            resp = self.session.get(policy_url, timeout=self.timeout)
        except requests.RequestException as exc:
            self.log(f"robots: {policy_url} unreachable ({exc}), treating host as closed")
            policy = self._Robots.deny_all(AGENT_TOKEN, policy_url)
            self._robots[host] = policy
            return policy

        self._mark_request(len(resp.content))

        if resp.status_code in (401, 403):
            self.log(f"robots: {policy_url} returned {resp.status_code}, treating host as closed")
            policy = self._Robots.deny_all(AGENT_TOKEN, policy_url)
        elif 400 <= resp.status_code < 500:
            self.log(f"robots: no policy published ({resp.status_code}), using the {self.delay_floor}s floor")
            policy = self._Robots.allow_all(AGENT_TOKEN, policy_url)
        elif resp.status_code >= 500:
            self.log(f"robots: {policy_url} returned {resp.status_code}, treating host as closed")
            policy = self._Robots.deny_all(AGENT_TOKEN, policy_url)
        else:
            policy = self._Robots.parse(resp.text, AGENT_TOKEN, policy_url)
            delay = policy.crawl_delay
            self.log(
                f"robots: policy read, crawl-delay "
                + (f"{delay}s" if delay else f"unset (using the {self.delay_floor}s floor)")
            )

        self._robots[host] = policy
        return policy

    def allowed(self, url: str):
        return self.robots_for(url).check(url)

    # requests

    def get(self, url: str) -> Response:
        decision = self.allowed(url)
        if not decision.allowed:
            self.stats.refused_by_robots += 1
            raise RobotsRefusal(url, decision.reason)

        cached = self._read_cache(url)
        if cached and not cached.get("validators"):
            self.stats.served_from_cache += 1
            return Response(url, cached["status"], cached["text"], from_cache=True)

        headers = {}
        if cached:
            validators = cached["validators"]
            if validators.get("etag"):
                headers["If-None-Match"] = validators["etag"]
            if validators.get("last_modified"):
                headers["If-Modified-Since"] = validators["last_modified"]

        delay = self._delay_for(url)
        last_error = None

        for attempt in range(1, self.max_attempts + 1):
            self._wait_turn(delay)
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                self.stats.retried += 1
                self._backoff(attempt, f"{type(exc).__name__} on {url}")
                continue

            self._mark_request(len(resp.content))

            if resp.status_code == 304 and cached:
                self.stats.not_modified += 1
                return Response(url, 200, cached["text"], from_cache=True)

            if resp.status_code in RETRY_STATUSES and attempt < self.max_attempts:
                self.stats.retried += 1
                self._backoff(attempt, f"HTTP {resp.status_code} on {url}", resp.headers.get("Retry-After"))
                continue

            if resp.status_code != 200:
                self.stats.failed += 1
                return Response(url, resp.status_code, "")

            text = _decode(resp)
            self._write_cache(url, resp, text)
            return Response(url, 200, text)

        self.stats.failed += 1
        self.log(f"giving up on {url} after {self.max_attempts} attempts ({last_error})")
        return Response(url, 0, "")

    # pacing

    def _delay_for(self, url: str) -> float:
        published = self.robots_for(url).crawl_delay
        return max(published or 0.0, self.delay_floor)

    def _wait_turn(self, delay: float):
        if self._last_request_at is None:
            return
        gap = time.monotonic() - self._last_request_at
        if gap < delay:
            pause = delay - gap
            self.stats.slept += pause
            time.sleep(pause)

    def _mark_request(self, size: int):
        now = time.monotonic()
        if self._last_request_at is not None:
            self.stats.intervals.append(now - self._last_request_at)
        self._last_request_at = now
        self.stats.requested += 1
        self.stats.bytes_downloaded += size

    def _backoff(self, attempt: int, why: str, retry_after: str | None = None):
        wait = None
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = None
        if wait is None:
            wait = (2**attempt) + random.uniform(0, 1)
        self.log(f"{why}, waiting {wait:.1f}s before attempt {attempt + 1}")
        self.stats.slept += wait
        time.sleep(wait)

    # cache

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _read_cache(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, url: str, resp: requests.Response, text: str):
        validators = {}
        if resp.headers.get("ETag"):
            validators["etag"] = resp.headers["ETag"]
        if resp.headers.get("Last-Modified"):
            validators["last_modified"] = resp.headers["Last-Modified"]

        self._cache_path(url).write_text(
            json.dumps(
                {
                    "url": url,
                    "status": resp.status_code,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "validators": validators,
                    "text": text,
                }
            )
        )


def _decode(resp: requests.Response) -> str:
    """Decode using the document's own declared charset.

    Servers that send `Content-Type: text/html` with no charset make requests
    fall back to ISO-8859-1, which turns every pound sign on the target site
    into a mojibake pair. The document says UTF-8 in a meta tag; believe it
    over the header.
    """
    match = META_CHARSET.search(resp.content[:4096])
    if match:
        declared = match.group(1).decode("ascii", "ignore")
        try:
            return resp.content.decode(declared)
        except (LookupError, UnicodeDecodeError):
            pass
    if resp.encoding and resp.encoding.lower() != "iso-8859-1":
        return resp.text
    return resp.content.decode("utf-8", "replace")


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
