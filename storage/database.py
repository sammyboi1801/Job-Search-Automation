"""
storage/database.py
───────────────────
SQLite-backed persistence layer.

Responsibilities:
  • Store every discovered job so we never re-alert on it.
  • Track keyword/location config mutations (add/remove commands).
  • Expose helpers used by scrapers and the notifier.
"""

import sqlite3
import hashlib
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


def _canonical_id(url: str, title: str, company: str) -> str:
    """Stable fingerprint for a job listing regardless of source URL variations."""
    raw = f"{url.strip().lower()}::{title.strip().lower()}::{company.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


class Database:
    """Thread-safe SQLite wrapper for job deduplication and config storage."""

    def __init__(self, db_path: str = "storage/jobs.db") -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_schema()
        logger.info("Database ready at %s", db_path)

    # ──────────────────────────────────────────────────────────────
    # Context manager helpers
    # ──────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Yield a connection with row_factory set; auto-commit on exit."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # safe concurrent reads
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ──────────────────────────────────────────────────────────────
    # Schema
    # ──────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id          TEXT PRIMARY KEY,      -- sha256 fingerprint
                    title       TEXT NOT NULL,
                    company     TEXT NOT NULL,
                    location    TEXT,
                    url         TEXT NOT NULL,
                    source      TEXT,                  -- which scraper found it
                    description TEXT,
                    date_posted TEXT,
                    score       REAL DEFAULT 0,        -- relevance score
                    discovered  TEXT NOT NULL,         -- ISO timestamp
                    notified    INTEGER DEFAULT 0      -- 1 once emailed
                );

                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT PRIMARY KEY,
                    added   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at  TEXT NOT NULL,
                    finished_at TEXT,
                    new_jobs    INTEGER DEFAULT 0,
                    status      TEXT DEFAULT 'running'
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs (notified);
                CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs (source);
            """)

    # ──────────────────────────────────────────────────────────────
    # Job CRUD
    # ──────────────────────────────────────────────────────────────

    def is_new(self, url: str, title: str, company: str) -> bool:
        """Return True if this job has NOT been seen before."""
        job_id = _canonical_id(url, title, company)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return row is None

    def save_job(self, job: Dict[str, Any]) -> bool:
        """
        Persist a new job. Returns True if inserted, False if duplicate.
        The caller is responsible for calling is_new() first if they want
        to filter before bulk inserts, but this is safe to call blindly.
        """
        job_id = _canonical_id(
            job.get("url", ""), job.get("title", ""), job.get("company", "")
        )
        now = datetime.utcnow().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (id, title, company, location, url, source,
                        description, date_posted, score, discovered, notified)
                       VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
                    (
                        job_id,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("location", ""),
                        job.get("url", ""),
                        job.get("source", ""),
                        job.get("description", ""),
                        job.get("date_posted", ""),
                        job.get("score", 0.0),
                        now,
                    ),
                )
            return True
        except Exception as exc:
            logger.error("save_job failed: %s", exc)
            return False

    def mark_notified(self, jobs: List[Dict[str, Any]]) -> None:
        """Bulk-mark a list of jobs as emailed."""
        ids = [
            _canonical_id(j.get("url", ""), j.get("title", ""), j.get("company", ""))
            for j in jobs
        ]
        with self._conn() as conn:
            conn.executemany(
                "UPDATE jobs SET notified = 1 WHERE id = ?",
                [(i,) for i in ids],
            )

    def get_unnotified_jobs(self) -> List[Dict]:
        """Fetch all jobs that have been saved but not yet emailed."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE notified = 0 ORDER BY discovered DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def total_jobs(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # ──────────────────────────────────────────────────────────────
    # Keyword management (CLI commands)
    # ──────────────────────────────────────────────────────────────

    def add_keyword(self, keyword: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO keywords (keyword, added) VALUES (?,?)",
                    (keyword.strip(), datetime.utcnow().isoformat()),
                )
            logger.info("Keyword added: %s", keyword)
            return True
        except Exception as exc:
            logger.error("add_keyword failed: %s", exc)
            return False

    def remove_keyword(self, keyword: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM keywords WHERE keyword = ?", (keyword.strip(),)
                )
            logger.info("Keyword removed: %s", keyword)
            return True
        except Exception as exc:
            logger.error("remove_keyword failed: %s", exc)
            return False

    def list_keywords(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT keyword FROM keywords ORDER BY added"
            ).fetchall()
        return [r[0] for r in rows]

    # ──────────────────────────────────────────────────────────────
    # Run logging
    # ──────────────────────────────────────────────────────────────

    def start_run(self) -> int:
        """Insert a run record and return its rowid."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO run_log (started_at) VALUES (?)",
                (datetime.utcnow().isoformat(),),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, new_jobs: int, status: str = "ok") -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE run_log
                   SET finished_at=?, new_jobs=?, status=?
                   WHERE id=?""",
                (datetime.utcnow().isoformat(), new_jobs, status, run_id),
            )
