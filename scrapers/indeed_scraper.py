"""
scrapers/indeed_scraper.py
──────────────────────────
Indeed Scraper (Stealth Playwright).
Robust version with Cloudflare detection and aggressive popup closing.
"""

import logging
import urllib.parse
import time
import random
from typing import List

from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)

class IndeedScraper(BaseScraper):
    SOURCE_NAME = "indeed"
    BASE_URL    = "https://www.indeed.com/jobs"

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

        params = {"q": keyword, "l": location, "sort": "date"}
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        logger.info("Indeed searching: %s", url)

        try:
            page = self._page

            # 1. Navigate with extended timeout
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                logger.warning("Indeed navigation timeout (might still be loaded): %s", e)

            # 2. Check for Cloudflare/Captcha
            title = page.title()
            if "Just a moment" in title or "Verify" in title or "Cloudflare" in title:
                logger.warning("Indeed hit Cloudflare challenge. Waiting for clearance...")
                time.sleep(10) # Give it time to auto-solve if possible

            # 3. Random human delay
            time.sleep(random.uniform(3, 6))

            # 4. Handle Popups (Google Sign-in, Cookies)
            self._close_popups(page)

            # 5. Scroll to trigger lazy loading
            for _ in range(3):
                page.keyboard.press("PageDown")
                time.sleep(random.uniform(0.8, 1.5))

            # 6. Extract HTML
            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            # 7. Parse Job Cards (Multi-selector strategy)
            cards = (
                soup.find_all("div", class_="job_seen_beacon")
                or soup.find_all("td", class_="resultContent")
                or soup.find_all("div", class_="slider_item")
                or soup.find_all("div", class_=lambda c: c and "card" in c and "outline" in c)
            )

            # Verification log
            if not cards:
                # Debug: check if we are on the wrong page
                if "did not match any jobs" in html:
                    logger.info("Indeed: No results found for query.")
                else:
                    logger.warning("Indeed: Page loaded but no cards found. Possible blocker.")

            logger.info("Indeed found %d raw cards", len(cards))

            for card in cards[:20]:
                try:
                    # TITLE
                    title_el = (
                        card.find("h2", class_="jobTitle")
                        or card.find("a", attrs={"data-testid": "job-title"})
                        or card.find("span", attrs={"title": True})
                    )

                    if not title_el: continue

                    title = title_el.get_text(strip=True)
                    # Sometimes title is in a span inside the h2
                    if not title:
                        sp = title_el.find("span")
                        if sp: title = sp.get("title", "")

                    if not title: continue

                    # COMPANY
                    company_el = (
                        card.find("span", attrs={"data-testid": "company-name"})
                        or card.find("span", class_="companyName")
                        or card.find("a", attrs={"data-testid": "company-name"})
                    )
                    company = company_el.get_text(strip=True) if company_el else "Unknown"

                    # LOCATION
                    loc_el = (
                        card.find("div", attrs={"data-testid": "text-location"})
                        or card.find("div", class_="companyLocation")
                    )
                    loc = loc_el.get_text(strip=True) if loc_el else location

                    # LINK
                    link_el = card.find("a", href=True)
                    # If the card is a 'td', the link might be in a sibling or parent
                    if not link_el:
                         link_el = card.find_parent("a", href=True)

                    href = link_el['href'] if link_el else ""
                    if href and not href.startswith("http"):
                        href = f"https://www.indeed.com{href}"

                    # DATE POSTED
                    date_el = card.find("span", class_="date")
                    date_posted = date_el.get_text(strip=True) if date_el else ""

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc,
                        url=href,
                        date_posted=date_posted,
                        source="indeed"
                    ))
                except Exception as e:
                    logger.debug("Indeed card parse failed: %s", e)

        except Exception as exc:
            logger.error("Indeed search failed: %s", exc)

        return jobs

    def _close_popups(self, page):
        """Attempts to close the Google Sign-in iframe and cookie banners."""
        try:
            # Click "X" on Google Sign in
            # It's usually in an iframe, but sometimes a div on top
            page.mouse.click(10, 10) # Click empty space to dismiss focus

            selectors = [
                "button[aria-label='close']",
                "div[aria-label='Close']",
                ".popover-x-button-close",
                "button[id^='google-auth-dismiss']",
                "#onetrust-accept-btn-handler" # Cookie banner
            ]
            for sel in selectors:
                if page.is_visible(sel):
                    page.click(sel)
                    time.sleep(0.5)
        except:
            pass

    def _ensure_playwright(self) -> bool:
        if self._page: return True
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            headless = self.config.get("scrapers", {}).get("headless", True)

            # Stealth Args
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
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-US",
                device_scale_factor=1,
            )

            # Hide webdriver property
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            self._page = ctx.new_page()
            return True
        except Exception as exc:
            logger.error("Indeed Playwright unavailable: %s", exc)
            return False

    def cleanup(self) -> None:
        try:
            if self._browser: self._browser.close()
            if self._playwright: self._playwright.stop()
        except: pass
        super().cleanup()