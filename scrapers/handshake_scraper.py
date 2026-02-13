"""
scrapers/handshake_scraper.py
──────────────────────────────
Handshake Scraper.
Switched to Cloudscraper to bypass Cloudflare protection which
was blocking Playwright/Requests.
"""

import logging
import urllib.parse
from typing import List

import cloudscraper
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)

class HandshakeScraper(BaseScraper):
    SOURCE_NAME = "handshake"
    SEARCH_URL = "https://joinhandshake.com/jobs/"

    def _setup(self) -> None:
        # Initialize cloudscraper to handle Cloudflare challenges
        self._scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

    def search(self, keyword: str, location: str) -> List[Job]:
        jobs = []

        params = {
            "query": keyword,
            "location": location
        }

        try:
            logger.info("Handshake (Cloudscraper) searching: %s", self.SEARCH_URL)
            resp = self._scraper.get(self.SEARCH_URL, params=params, timeout=15)

            if resp.status_code != 200:
                logger.warning("Handshake returned status %s", resp.status_code)
                return jobs

            soup = BeautifulSoup(resp.text, "lxml")
            jobs = self._parse_soup(soup, location)

            logger.info("Handshake returned %d jobs", len(jobs))

        except Exception as exc:
            logger.error("Handshake Cloudscraper failed: %s", exc)

        return jobs

    def _parse_soup(self, soup: BeautifulSoup, location: str) -> List[Job]:
        jobs = []
        seen_urls = set()

        # Handshake's structure changes often. We look for any link containing /jobs/ or /postings/
        # and try to deduce the card content from its parent.
        links = soup.find_all("a", href=lambda h: h and ("/jobs/" in h or "/postings/" in h))

        for link in links[:30]:
            try:
                href = link['href']
                if href in seen_urls: continue
                if len(href) < 15: continue # Skip short/nav links

                # Find the container (card)
                card = link.find_parent("div", class_=lambda c: c and "card" in str(c).lower())
                if not card:
                    card = link.parent # Fallback

                full_text = card.get_text(" ", strip=True)

                # Basic Title Extraction
                # Try finding a heading first
                title_tag = card.find(["h2", "h3", "h4", "strong"])
                if title_tag:
                    title = title_tag.get_text(strip=True)
                else:
                    # Fallback to link text
                    title = link.get_text(strip=True)

                if len(title) < 3: continue

                # Basic Company Extraction
                # Handshake often puts company name in a specific 'employer' class or just after title
                company = "Unknown"
                # Simple heuristic: remove title from full text, take the next chunk
                remaining = full_text.replace(title, "").strip()
                if remaining:
                    company = remaining.split(" ")[0:3] # Take first 3 words
                    company = " ".join(company).strip("| ,")

                if href.startswith("/"):
                    href = f"https://joinhandshake.com{href}"

                seen_urls.add(href)

                jobs.append(Job(
                    title=title,
                    company=company or "Handshake Employer",
                    location=location,
                    url=href
                ))

            except Exception:
                continue

        return jobs