"""
scrapers/google_jobs_scraper.py
────────────────────────────────
Pulls jobs from Google's "Jobs" feature (the panel that appears
in search results when you search "software engineer jobs near me").

Two modes:
  1. SerpAPI (preferred) – set SERPAPI_KEY env var.
     Provides clean, reliable structured data.
  2. HTML scraping fallback – parses Google search results.
     Works without API key but fragile and subject to CAPTCHAs.
"""

import logging
import os
import urllib.parse
from typing import List

from scrapers.base_scraper import BaseScraper, Job

logger = logging.getLogger(__name__)


class GoogleJobsScraper(BaseScraper):
    SOURCE_NAME  = "google_jobs"
    SERPAPI_URL  = "https://serpapi.com/search.json"
    GOOGLE_URL   = "https://www.google.com/search"

    def search(self, keyword: str, location: str) -> List[Job]:
        api_key = os.environ.get("SERPAPI_KEY", "")
        if api_key:
            jobs = self._serpapi_search(keyword, location, api_key)
        else:
            logger.info(
                "SERPAPI_KEY not set – using HTML scrape for Google Jobs "
                "(set SERPAPI_KEY for more reliable results)"
            )
            jobs = self._html_search(keyword, location)

        logger.info(
            "Google Jobs returned %d jobs for '%s' / '%s'",
            len(jobs), keyword, location,
        )
        return jobs

    # ── SerpAPI ──────────────────────────────────────────────────────

    def _serpapi_search(self, keyword: str, location: str, api_key: str) -> List[Job]:
        jobs: List[Job] = []
        params = {
            "engine":    "google_jobs",
            "q":         f"{keyword} {location}",
            "hl":        "en",
            "api_key":   api_key,
        }
        if self.config.get("search", {}).get("remote_filter"):
            params["ltype"] = "1"   # remote listings

        resp = self._get(
            f"{self.SERPAPI_URL}?{urllib.parse.urlencode(params)}"
        )
        if not resp:
            return jobs

        try:
            data = resp.json()
        except ValueError:
            logger.warning("Google Jobs SerpAPI: non-JSON response")
            return jobs

        error = data.get("error")
        if error:
            logger.warning("SerpAPI error: %s", error)
            return jobs

        for item in data.get("jobs_results", []):
            try:
                title   = item.get("title",    "")
                company = item.get("company_name", "Unknown")
                loc     = item.get("location",  location)
                date    = (item.get("detected_extensions") or {}).get("posted_at", "")
                desc    = item.get("description", "")

                # Build URL from sharing link or Google redirect
                share   = (item.get("related_links") or [{}])[0].get("link", "")
                href    = share or item.get("apply_options", [{}])[0].get("link", "")

                if not title:
                    continue
                jobs.append(
                    Job(
                        title=title, company=company, location=loc,
                        url=href, date_posted=str(date),
                        description=str(desc)[:300],
                    )
                )
            except Exception as exc:
                logger.debug("SerpAPI parse error: %s", exc)

        return jobs

    # ── HTML fallback ────────────────────────────────────────────────

    def _html_search(self, keyword: str, location: str) -> List[Job]:
        """
        Scrape Google search for 'keyword jobs location'.
        The htidocid results panel contains job cards.
        Note: Google CAPTCHAs are common without a proxy.
        """
        from bs4 import BeautifulSoup
        jobs: List[Job] = []

        query  = f"{keyword} jobs {location}"
        params = {"q": query, "ibp": "htl;jobs", "hl": "en"}
        url    = f"{self.GOOGLE_URL}?{urllib.parse.urlencode(params)}"

        resp = self._get(
            url,
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        )
        if not resp:
            return jobs

        if "captcha" in resp.url.lower() or resp.status_code != 200:
            logger.warning(
                "Google Jobs: CAPTCHA or non-200 response (%s). "
                "Consider setting SERPAPI_KEY.", resp.status_code
            )
            return jobs

        soup = BeautifulSoup(resp.text, "lxml")

        # Google Jobs cards in the HTLP/jobs search result panel
        cards = (
            soup.find_all("div", class_="iFjolb")   # common class for job card
            or soup.find_all("g-card")
            or soup.find_all("div", attrs={"jscontroller": True, "data-is-hoisted": True})
        )

        for card in cards[:20]:
            try:
                title_el   = (card.find(class_="BjJfJf")
                               or card.find("div", class_="tJ9zfc"))
                company_el = (card.find(class_="vNEEBe")
                               or card.find("div", class_="nJlQNd"))
                loc_el     = (card.find(class_="Qk80Jf")
                               or card.find("div", class_="ShLHV"))
                link_el    = card.find("a", href=True)
                date_el    = card.find("span", class_="LL4CDc")

                title   = title_el.get_text(strip=True)   if title_el   else ""
                company = company_el.get_text(strip=True)  if company_el else "Unknown"
                loc     = loc_el.get_text(strip=True)     if loc_el     else location
                href    = link_el["href"]                  if link_el   else ""
                date_s  = date_el.get_text(strip=True)    if date_el   else ""

                if not title:
                    continue
                if href.startswith("/"):
                    href = f"https://www.google.com{href}"

                jobs.append(Job(
                    title=title, company=company, location=loc,
                    url=href, date_posted=date_s,
                ))
            except Exception as exc:
                logger.debug("Google Jobs HTML parse error: %s", exc)

        return jobs
