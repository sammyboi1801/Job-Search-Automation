"""
scrapers/linkedin_scraper.py
────────────────────────────
LinkedIn job search via Playwright (headless Chromium).
Runs anonymously — public job listings don't require login.
Cookie-based login is supported optionally via LINKEDIN_COOKIE env var.
"""

import logging
import os
import random
import time
import urllib.parse
from typing import List

from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)


class LinkedInScraper(BaseScraper):
    SOURCE_NAME = "linkedin"
    SEARCH_URL  = "https://www.linkedin.com/jobs/search/"

    def _setup(self) -> None:
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._page       = None

    def _ensure_browser(self) -> bool:
        if self._page is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            headless = self.config.get("scrapers", {}).get("headless", True)
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self._page = self._context.new_page()

            # Optional: inject saved cookie for logged-in access
            cookie = os.environ.get("LINKEDIN_COOKIE", "")
            if cookie:
                self._context.add_cookies([{
                    "name": "li_at", "value": cookie,
                    "domain": ".linkedin.com", "path": "/",
                }])
                logger.info("LinkedIn: using cookie auth")
            else:
                logger.info("LinkedIn: anonymous mode")

            return True
        except Exception as exc:
            logger.error("LinkedIn: failed to start Playwright: %s", exc)
            return False

    def search(self, keyword: str, location: str) -> List[Job]:
        jobs: List[Job] = []
        if not self._ensure_browser():
            return jobs

        params = {
            "keywords": keyword,
            "location": location,
            "sortBy":   "DD",
            "f_TPR":    "r86400",
        }
        if self.config.get("search", {}).get("remote_filter"):
            params["f_WT"] = "2"

        exp = self.config.get("search", {}).get("experience_level", "")
        exp_map = {"entry": "1,2", "mid": "3", "senior": "4,5", "lead": "5,6"}
        if exp.lower() in exp_map:
            params["f_E"] = exp_map[exp.lower()]

        url = f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}"
        logger.info("LinkedIn searching: %s", url)

        try:
            page = self._page

            # Navigate with generous timeout
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(3, 5))

            # Dismiss any modals/popups
            for selector in [
                "button[aria-label='Dismiss']",
                ".modal__dismiss",
                "button.contextual-sign-in-modal__modal-dismiss",
                "[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
            ]:
                try:
                    page.click(selector, timeout=2000)
                    time.sleep(0.5)
                except Exception:
                    pass

            # Scroll to load job cards
            for _ in range(4):
                page.evaluate("window.scrollBy(0, 700)")
                time.sleep(random.uniform(0.8, 1.5))

            # Try multiple card selectors (LinkedIn A/B tests layouts)
            cards = (
                page.query_selector_all("li.jobs-search-results__list-item")
                or page.query_selector_all("div.base-card")
                or page.query_selector_all("li.ember-view[data-occludable-job-id]")
                or page.query_selector_all("[data-job-id]")
            )

            logger.debug("LinkedIn found %d raw cards", len(cards))

            for card in cards[:20]:
                try:
                    # Title — try multiple selectors
                    title = ""
                    for sel in [
                        "h3.base-search-card__title",
                        "h3.job-search-card__title",
                        ".job-search-card__title",
                        "a[data-tracking-control-name]",
                    ]:
                        el = card.query_selector(sel)
                        if el:
                            title = el.inner_text().strip()
                            break

                    if not title:
                        continue

                    # Company
                    company = ""
                    for sel in ["h4.base-search-card__subtitle", "a.hidden-nested-link", ".job-search-card__subtitle"]:
                        el = card.query_selector(sel)
                        if el:
                            company = el.inner_text().strip()
                            break

                    # Location
                    loc = location
                    loc_el = card.query_selector("span.job-search-card__location")
                    if loc_el:
                        loc = loc_el.inner_text().strip()

                    # Date
                    date_el = card.query_selector("time")
                    date_str = date_el.get_attribute("datetime") if date_el else ""

                    # URL
                    href = ""
                    for sel in ["a.base-card__full-link", "a[href*='/jobs/view/']", "a"]:
                        el = card.query_selector(sel)
                        if el:
                            href = el.get_attribute("href") or ""
                            if href:
                                break

                    if href:
                        href = href.split("?")[0]

                    jobs.append(Job(
                        title=title,
                        company=company or "Unknown",
                        location=loc,
                        url=href,
                        date_posted=date_str,
                    ))
                except Exception as exc:
                    logger.debug("LinkedIn card parse error: %s", exc)

        except Exception as exc:
            logger.error("LinkedIn search failed: %s", exc)

        logger.info("LinkedIn returned %d jobs for '%s' / '%s'", len(jobs), keyword, location)
        return jobs

    def cleanup(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        super().cleanup()