"""
scheduler.py
────────────
Orchestrates a full scrape → dedup → notify cycle and repeats
it on a configurable interval using APScheduler.

Flow per run:
  1. For each enabled scraper:
       For each keyword × location pair:
         Collect jobs → filter duplicates → score → save
  2. Fetch all unseen (un-notified) jobs from DB
  3. Send email (+ Telegram if enabled)
  4. Mark jobs as notified
  5. (Optionally) export to CSV
"""

import csv
import logging
import os
from datetime import datetime
from typing import Dict, List, Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from scrapers.base_scraper import Job
from scrapers.indeed_scraper import IndeedScraper
from scrapers.linkedin_scraper import LinkedInScraper
from scrapers.simplify_scraper import SimplifyScraper
from scrapers.handshake_scraper import HandshakeScraper
from scrapers.google_jobs_scraper import GoogleJobsScraper
from notifier.email_notifier import EmailNotifier
from notifier.telegram_notifier import TelegramNotifier
from storage.database import Database

logger = logging.getLogger(__name__)

# Map config name → scraper class
SCRAPER_REGISTRY = {
    "indeed":      IndeedScraper,
    "linkedin":    LinkedInScraper,
    "simplify":    SimplifyScraper,
    "handshake":   HandshakeScraper,
    "google_jobs": GoogleJobsScraper,
}


class JobScheduler:
    """
    Manages the APScheduler loop and one-shot execution.
    Instantiated once in main.py.
    """

    def __init__(self, config: dict) -> None:
        self.config        = config
        self.db            = Database(config.get("storage", {}).get("db_path", "storage/jobs.db"))
        self.email         = EmailNotifier(config)
        self.telegram      = TelegramNotifier(config)
        self._scrapers     = self._init_scrapers()
        self._apscheduler  = None

    # ──────────────────────────────────────────────────────────────
    # Scraper initialisation
    # ──────────────────────────────────────────────────────────────

    def _init_scrapers(self):
        enabled = self.config.get("scrapers", {}).get("enabled", list(SCRAPER_REGISTRY))
        scrapers = {}
        for name in enabled:
            cls = SCRAPER_REGISTRY.get(name.lower())
            if cls is None:
                logger.warning("Unknown scraper '%s' in config – skipped", name)
                continue
            try:
                scrapers[name] = cls(self.config)
                logger.info("Loaded scraper: %s", name)
            except Exception as exc:
                logger.error("Failed to load scraper '%s': %s", name, exc)
        return scrapers

    # ──────────────────────────────────────────────────────────────
    # Core run logic
    # ──────────────────────────────────────────────────────────────

    def run_once(self, dry_run: bool = False) -> int:
        """
        Execute a full scrape cycle.

        Args:
            dry_run: If True, save jobs to DB but do NOT send notifications.

        Returns:
            Number of new jobs found.
        """
        run_id = self.db.start_run()
        logger.info("═" * 60)
        logger.info("Run #%d started at %s", run_id, datetime.utcnow().isoformat())

        search_cfg   = self.config.get("search", {})
        keywords     = self._effective_keywords()
        locations    = search_cfg.get("locations", ["Remote"])
        new_count    = 0

        for scraper_name, scraper in self._scrapers.items():
            for keyword in keywords:
                for location in locations:
                    logger.info(
                        "Scraping %s | keyword='%s' | location='%s'",
                        scraper_name, keyword, location,
                    )
                    jobs: List[Job] = scraper.safe_search(keyword, location)

                    for job in jobs:
                        if self.db.is_new(job.url, job.title, job.company):
                            self.db.save_job(job.to_dict())
                            new_count += 1

        logger.info("New jobs this run: %d", new_count)

        # Notify
        unnotified = self.db.get_unnotified_jobs()
        if unnotified:
            ok = self.email.send(unnotified, dry_run=dry_run)
            self.telegram.send(unnotified)
            if ok and not dry_run:
                self.db.mark_notified(unnotified)
        else:
            self.email.send([], dry_run=dry_run)   # sends "no new jobs" if configured

        # CSV export
        if self.config.get("export", {}).get("csv_enabled"):
            self._export_csv(unnotified)

        self.db.finish_run(run_id, new_count, "ok")
        logger.info("Run #%d finished. Total jobs in DB: %d", run_id, self.db.total_jobs())
        return new_count

    # ──────────────────────────────────────────────────────────────
    # Keyword resolution (config + DB overrides)
    # ──────────────────────────────────────────────────────────────

    def _effective_keywords(self) -> List[str]:
        """Merge YAML keywords with any keywords added via CLI into DB."""
        yaml_kw = self.config.get("search", {}).get("keywords", [])
        db_kw   = self.db.list_keywords()
        merged  = list({k.strip() for k in yaml_kw + db_kw if k.strip()})
        return merged

    # ──────────────────────────────────────────────────────────────
    # Scheduling
    # ──────────────────────────────────────────────────────────────

    def start_scheduler(self) -> None:
        """Block the main thread and run every X hours."""
        hours = float(self.config.get("scheduler", {}).get("interval_hours", 3))
        logger.info("Starting scheduler – running every %.1f hours", hours)

        scheduler = BlockingScheduler(timezone="UTC")
        scheduler.add_job(
            self.run_once,
            trigger=IntervalTrigger(hours=hours),
            next_run_time=datetime.utcnow(),   # fire immediately on start
            id="job_search",
            max_instances=1,
            misfire_grace_time=600,
        )
        try:
            scheduler.start()
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        """Gracefully shut down all scrapers."""
        for scraper in self._scrapers.values():
            try:
                scraper.cleanup()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────
    # CSV export (bonus)
    # ──────────────────────────────────────────────────────────────

    def _export_csv(self, jobs: List[Dict[str, Any]]) -> None:
        path = self.config.get("export", {}).get("csv_path", "exports/jobs_export.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Append mode so historical data accumulates
        file_exists = os.path.isfile(path)
        fieldnames  = [
            "title", "company", "location", "url",
            "date_posted", "source", "description", "score", "discovered",
        ]
        try:
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                if not file_exists:
                    writer.writeheader()
                writer.writerows(jobs)
            logger.info("CSV export appended %d rows to %s", len(jobs), path)
        except Exception as exc:
            logger.error("CSV export failed: %s", exc)
