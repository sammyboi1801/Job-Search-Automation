"""
scrapers/simplify_scraper.py
─────────────────────────────
Simplify Jobs Scraper (Dynamic URL & Stealth Playwright).
Constructs a query URL matching the user's specific filters (Roles, Experience, Remote)
and scrapes the React-rendered job list.
"""

import logging
import urllib.parse
import time
import random
from typing import List

from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)

class SimplifyScraper(BaseScraper):
    SOURCE_NAME = "simplify"
    BASE_URL    = "https://simplify.jobs/jobs"

    def _setup(self) -> None:
        self._playwright = None
        self._browser    = None
        self._page       = None

    def search(self, keyword: str, location: str) -> List[Job]:
        return self._playwright_search(keyword, location)

    def _playwright_search(self, keyword: str, location: str) -> List[Job]:
        jobs: List[Job] = []

        if not self._ensure_playwright():
            return jobs

        # ─────────────────────────────────────────────────────────────
        # 1. Dynamic URL Construction
        # ─────────────────────────────────────────────────────────────

        # Base params
        params = {
            "search": keyword,   # The "Role" (e.g. "Machine Learning", "Software Engineer")
            "location": location,
            "sort": "Newest"
        }

        # Analyze keyword to set "Experience" filters automatically
        # Simplify values: "Internship", "Entry Level/New Grad", "Junior", "Mid Level", "Senior"
        kw_lower = keyword.lower()
        experience = []

        if "intern" in kw_lower:
            experience.append("Internship")
        elif "new grad" in kw_lower or "graduate" in kw_lower or "entry" in kw_lower:
            experience.append("Entry Level/New Grad")
        elif "junior" in kw_lower:
            experience.append("Junior")

        # If any experience filters were detected, join them with semicolons
        if experience:
            params["experience"] = ";".join(experience)

        # Handle Remote/Hybrid preference
        # Simplify values: "Remote", "Hybrid", "In Person"
        work_arrangements = []
        is_remote = self.config.get("search", {}).get("remote_filter") or location.lower() == "remote"

        if is_remote:
            work_arrangements.extend(["Remote", "Hybrid"])
            # If location was strictly "Remote", clear the location text to search globally
            if location.lower() == "remote":
                params["location"] = ""

        if work_arrangements:
            params["workArrangement"] = ";".join(work_arrangements)

        # Construct full URL
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        logger.info("Simplify searching: %s", url)

        # ─────────────────────────────────────────────────────────────
        # 2. Playwright Execution (Stealth)
        # ─────────────────────────────────────────────────────────────
        try:
            page = self._page

            # Navigate
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Human-like delay
            time.sleep(random.uniform(3, 5))

            # Wait for results to load
            try:
                # Wait for at least one job link to appear
                page.wait_for_selector("a[href*='/jobs/']", timeout=15000)
            except:
                logger.warning("Simplify: Timeout waiting for job cards (possible 0 results or CAPTCHA).")

            # Scroll to trigger lazy loading
            for _ in range(4):
                page.keyboard.press("PageDown")
                time.sleep(random.uniform(1.0, 1.5))

            # ─────────────────────────────────────────────────────────────
            # 3. HTML Parsing
            # ─────────────────────────────────────────────────────────────
            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            # Find all anchor tags that point to a job detail page
            links = soup.find_all("a", href=lambda h: h and "/jobs/" in h)
            seen_ids = set()

            for link in links:
                try:
                    href = link['href']

                    # Extract UUID to deduplicate (e.g. /jobs/1234-5678)
                    job_uuid = href.split("?")[0]
                    if job_uuid in seen_ids:
                        continue
                    seen_ids.add(job_uuid)

                    # Title is usually the text of the link
                    title = link.get_text(" ", strip=True)
                    if not title: continue

                    # Find the Container (Card) to extract Company/Location
                    # The <a> is usually inside a div or li. We walk up to find the container.
                    container = link.find_parent("li")
                    if not container:
                         # Fallback for different layouts
                        container = link.find_parent("div", class_=lambda c: c and "border" in str(c))

                    company = "Unknown"
                    loc = location

                    if container:
                        # Extract all text from the card
                        text_parts = list(container.stripped_strings)

                        # Heuristic: Filter out the title, "Apply", "New", etc.
                        # Usually the structure is: [Title, Company, Location, Type...]
                        clean_parts = [
                            t for t in text_parts
                            if t != title and "apply" not in t.lower() and "new" != t.lower()
                        ]

                        if clean_parts:
                            # The first non-title element is usually the Company
                            company = clean_parts[0]

                            # Look for something that looks like a location (contains comma or "Remote")
                            for part in clean_parts[1:]:
                                if "," in part or "Remote" in part or "Hybrid" in part:
                                    loc = part
                                    break

                    # Normalize URL
                    if href.startswith("/"):
                        href = f"https://simplify.jobs{href}"

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc,
                        url=href,
                        source="simplify"
                    ))

                except Exception as e:
                    logger.debug("Simplify card parse error: %s", e)

        except Exception as exc:
            logger.error("Simplify Playwright failed: %s", exc)

        return jobs

    def _ensure_playwright(self) -> bool:
        if self._page:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            headless = self.config.get("scrapers", {}).get("headless", True)

            # STEALTH ARGS: Crucial for Simplify/Cloudflare
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-gpu",
                ]
            )

            ctx = self._browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-US"
            )

            # Mask the webdriver property
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            self._page = ctx.new_page()
            return True
        except Exception as exc:
            logger.error("Simplify Playwright unavailable: %s", exc)
            return False

    def cleanup(self) -> None:
        try:
            if self._browser: self._browser.close()
            if self._playwright: self._playwright.stop()
        except: pass
        super().cleanup()