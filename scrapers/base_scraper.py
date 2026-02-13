"""
scrapers/base_scraper.py
────────────────────────
Abstract base that every concrete scraper must extend.

Contract enforced on subclasses:
  • search(keyword, location) → List[Job]

Shared utilities provided here:
  • Rotating user-agent pool
  • Rate-limited requests session (get / post)
  • Retry logic with exponential back-off
  • robots.txt compliance check
  • Relevance scoring against tag list
"""

import abc
import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Job data model
# ──────────────────────────────────────────────────────────────

@dataclass
class Job:
    title:       str
    company:     str
    location:    str    = ""
    url:         str    = ""
    date_posted: str    = ""
    source:      str    = ""
    description: str    = ""
    score:       float  = 0.0

    def to_dict(self) -> Dict:
        return {
            "title":       self.title,
            "company":     self.company,
            "location":    self.location,
            "url":         self.url,
            "date_posted": self.date_posted,
            "source":      self.source,
            "description": self.description,
            "score":       self.score,
        }


# ──────────────────────────────────────────────────────────────
# User-agent pool
# ──────────────────────────────────────────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
    "Gecko/20100101 Firefox/124.0",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def random_user_agent() -> str:
    return random.choice(_USER_AGENTS)


# ──────────────────────────────────────────────────────────────
# Base scraper
# ──────────────────────────────────────────────────────────────

class BaseScraper(abc.ABC):
    """
    Abstract base for all job-board scrapers.

    Subclasses must implement:
        search(keyword: str, location: str) -> List[Job]

    They may optionally override:
        _setup()   – called once after __init__
        cleanup()  – called when the scheduler shuts down
    """

    SOURCE_NAME: str = "unknown"   # override in every subclass

    def __init__(self, config: dict) -> None:
        self.config = config
        scraper_cfg = config.get("scrapers", {})
        self.rate_limit: float = float(scraper_cfg.get("rate_limit_seconds", 3))
        self.max_retries: int  = int(scraper_cfg.get("max_retries", 3))
        self.tags: List[str]   = config.get("search", {}).get("tags", [])
        self._session: Optional[requests.Session] = None
        self._robots_cache: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self._setup()
        logger.debug("%s scraper initialised", self.SOURCE_NAME)

    # ── override hook ────────────────────────────────────────────

    def _setup(self) -> None:
        """Optional hook called at end of __init__."""

    def cleanup(self) -> None:
        """Release resources (close Playwright, sessions, etc.)."""
        if self._session:
            self._session.close()

    # ── abstract method every scraper must implement ─────────────

    @abc.abstractmethod
    def search(self, keyword: str, location: str) -> List[Job]:
        """
        Return a list of Job objects matching keyword + location.
        Never raise – log errors and return [].
        """

    # ── HTTP helpers ─────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        """Lazy-init a session with retry middleware."""
        if self._session is None:
            session = requests.Session()
            retry = Retry(
                total=self.max_retries,
                backoff_factor=1.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._session = session
        # Rotate user-agent every call
        self._session.headers.update({
            "User-Agent": random_user_agent(),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        return self._session

    def _get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """
        GET with built-in rate limiting, random jitter, and error handling.
        Returns None on failure instead of raising.
        """
        jitter = random.uniform(0.5, 1.5)
        time.sleep(self.rate_limit * jitter)
        try:
            resp = self._get_session().get(url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("%s GET failed [%s]: %s", self.SOURCE_NAME, url, exc)
            return None

    def _post(self, url: str, **kwargs) -> Optional[requests.Response]:
        """POST with the same safety wrapper."""
        jitter = random.uniform(0.5, 1.5)
        time.sleep(self.rate_limit * jitter)
        try:
            resp = self._get_session().post(url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("%s POST failed [%s]: %s", self.SOURCE_NAME, url, exc)
            return None

    # ── robots.txt compliance ─────────────────────────────────────

    def _can_fetch(self, url: str) -> bool:
        """Check robots.txt before fetching a URL."""
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            try:
                rp.read()
            except Exception:
                # If we can't read robots.txt assume allowed
                self._robots_cache[base] = None
                return True
            self._robots_cache[base] = rp
        rp = self._robots_cache.get(base)
        if rp is None:
            return True
        ua = random_user_agent().split("(")[0].strip()
        return rp.can_fetch(ua, url)

    # ── relevance scoring ─────────────────────────────────────────

    def _score(self, job: Job, keyword: str) -> float:
        """
        Simple keyword-match relevance score 0–100.
        Weights: title match > description match > tag matches.
        """
        haystack_title = (job.title + " " + job.company).lower()
        haystack_body  = job.description.lower()
        kw_lower       = keyword.lower()

        score = 0.0

        # Exact keyword in title = strong signal
        if kw_lower in haystack_title:
            score += 40.0

        # Partial word matches in title
        for word in kw_lower.split():
            if word in haystack_title:
                score += 5.0

        # Tag matches
        for tag in self.tags:
            tag_lower = tag.lower()
            if tag_lower in haystack_title:
                score += 10.0
            elif tag_lower in haystack_body:
                score += 3.0

        return min(score, 100.0)

    # ── safe wrapper ──────────────────────────────────────────────

    def safe_search(self, keyword: str, location: str) -> List[Job]:
        """
        Call search() with a try/except so one broken scraper
        never kills the whole run.
        """
        try:
            jobs = self.search(keyword, location)
            # Attach score and source
            for j in jobs:
                j.source = self.SOURCE_NAME
                j.score  = self._score(j, keyword)
            return jobs
        except Exception as exc:
            logger.error(
                "%s.safe_search crashed (kw=%s, loc=%s): %s",
                self.SOURCE_NAME, keyword, location, exc, exc_info=True
            )
            return []
