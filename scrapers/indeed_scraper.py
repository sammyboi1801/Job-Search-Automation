"""
scrapers/indeed_scraper.py
──────────────────────────
Uses Indeed's RSS feed as the primary method — far more reliable
than scraping HTML since RSS is a lightweight XML endpoint that
Indeed doesn't aggressively block.

Strategy (in order):
  1. RSS feed  (/rss endpoint)  ← almost never 403'd
  2. Playwright headless        ← if RSS returns 0 results
  3. Log warning and return []  ← if both fail
"""

import logging
import urllib.parse
from typing import List

from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)


class IndeedScraper(BaseScraper):
    SOURCE_NAME = "indeed"
    RSS_URL     = "https://www.indeed.com/rss"
    BASE_URL    = "https://www.indeed.com/jobs"

    def _setup(self) -> None:
        self._playwright = None
        self._browser    = None
        self._page       = None

    # ── Public entry ─────────────────────────────────────────────

    def search(self, keyword: str, location: str) -> List[Job]:
        # Indeed's /rss endpoint was removed (returns 404) — use Playwright directly
        jobs = self._playwright_search(keyword, location)
        logger.info(
            "Indeed returned %d jobs for '%s' / '%s'", len(jobs), keyword, location
        )
        return jobs

    # ── Playwright search (primary) ─────────────────────────────

    def _playwright_search(self, keyword: str, location: str) -> List[Job]:
        """Full browser render via Playwright — used only when RSS fails."""
        jobs: List[Job] = []

        if not self._ensure_playwright():
            return jobs

        import time, random
        from bs4 import BeautifulSoup

        params = {"q": keyword, "l": location, "sort": "date"}
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        logger.info("Indeed Playwright searching: %s", url)

        try:
            page = self._page
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            for _ in range(2):
                page.evaluate("window.scrollBy(0, 600)")
                time.sleep(random.uniform(1, 1.5))

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            cards = (
                soup.find_all("div", class_="job_seen_beacon")
                or soup.find_all("li", attrs={"class": lambda c: c and "eu4oa1w0" in c})
                or soup.find_all("div", attrs={"data-testid": "slider_item"})
            )

            logger.debug("Indeed Playwright: found %d cards", len(cards))

            for card in cards[:25]:
                try:
                    title_el = (
                        card.find("h2", class_="jobTitle")
                        or card.find("a", attrs={"data-testid": "job-title"})
                        or card.find("span", attrs={"title": True})
                    )
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue

                    company_el = (
                        card.find("span", class_="companyName")
                        or card.find("span", attrs={"data-testid": "company-name"})
                    )
                    company = company_el.get_text(strip=True) if company_el else "Unknown"

                    loc_el = (
                        card.find("div", class_="companyLocation")
                        or card.find("div", attrs={"data-testid": "text-location"})
                    )
                    loc = loc_el.get_text(strip=True) if loc_el else location

                    link_el = card.find("a", href=True)
                    href = link_el["href"] if link_el else ""
                    if href.startswith("/"):
                        href = f"https://www.indeed.com{href}"

                    date_el = card.find("span", class_="date")
                    date_str = date_el.get_text(strip=True) if date_el else ""

                    jobs.append(Job(
                        title=title, company=company, location=loc,
                        url=href, date_posted=date_str,
                    ))
                except Exception as exc:
                    logger.debug("Indeed Playwright card error: %s", exc)

        except Exception as exc:
            logger.error("Indeed Playwright search failed: %s", exc)

        return jobs

    def _ensure_playwright(self) -> bool:
        if self._page:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            headless = self.config.get("scrapers", {}).get("headless", True)
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self._page = ctx.new_page()
            return True
        except Exception as exc:
            logger.error("Indeed: Playwright unavailable: %s", exc)
            return False

    def cleanup(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        super().cleanup()


# ── Helpers ───────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Remove HTML tags from a string using the stdlib only."""
    import re
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"&nbsp;",  " ", clean)
    clean = re.sub(r"&amp;",   "&", clean)
    clean = re.sub(r"&lt;",    "<", clean)
    clean = re.sub(r"&gt;",    ">", clean)
    clean = re.sub(r"&quot;",  '"', clean)
    clean = re.sub(r"\s+",     " ", clean)
    return clean.strip()


def _clean_title(title: str) -> str:
    """
    Indeed RSS titles sometimes look like:
      'Software Engineer - Google - Mountain View, CA'
    We just want 'Software Engineer'.
    """
    import re
    parts = re.split(r"\s[-–]\s", title)
    return parts[0].strip() if parts else title.strip()


def _extract_location(title: str, description: str) -> str:
    """Try to pull a 'City, ST' pattern from title or description."""
    import re
    pattern = r"[A-Z][a-zA-Z\s]+,\s[A-Z]{2}"
    for text in (title, description):
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return ""