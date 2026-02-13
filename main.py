"""
main.py
───────
CLI entry point for the Job Search Automation tool.

Usage:
  python main.py                          # start the scheduler (runs every X hours)
  python main.py --run-now                # run once immediately then exit
  python main.py --dry-run                # run once, no email/telegram sent
  python main.py --test-email             # send a test email to verify credentials
  python main.py --add-keyword "ML Eng"   # persist a new keyword into the DB
  python main.py --remove-keyword "..."   # remove a keyword from the DB
  python main.py --list-config            # print current effective config
  python main.py --list-jobs [N]          # show last N unseen jobs (default 20)
  python main.py --export-csv             # export all DB jobs to CSV immediately
"""

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
load_dotenv()


# ──────────────────────────────────────────────────────────────────
# Logging setup (must happen before importing scrapers)
# ──────────────────────────────────────────────────────────────────

def _setup_logging(config: dict) -> None:
    log_cfg   = config.get("logging", {})
    level_str = log_cfg.get("level", "INFO").upper()
    level     = getattr(logging, level_str, logging.INFO)
    log_file  = log_cfg.get("log_file", "logs/job_scraper.log")
    max_bytes = int(log_cfg.get("max_bytes", 5_242_880))
    backups   = int(log_cfg.get("backup_count", 3))

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s – %(message)s"
    ))
    root.addHandler(fh)

    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "playwright", "asyncio", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Config loader
# ──────────────────────────────────────────────────────────────────

def _load_config(path: str = "config.yaml") -> dict:
    if not Path(path).exists():
        sys.exit(f"[ERROR] config.yaml not found at '{path}'. "
                 "Copy config.yaml to the project root and edit it.")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ──────────────────────────────────────────────────────────────────
# CLI argument parser
# ──────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="job-automation",
        description="Automated multi-platform job search & alert engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Run the scraper once and exit (sends email).",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the scraper once but do NOT send email/Telegram.",
    )
    group.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email to verify SMTP credentials.",
    )
    group.add_argument(
        "--add-keyword",
        metavar="KEYWORD",
        help="Add a keyword to the persistent search list.",
    )
    group.add_argument(
        "--remove-keyword",
        metavar="KEYWORD",
        help="Remove a keyword from the persistent search list.",
    )
    group.add_argument(
        "--list-config",
        action="store_true",
        help="Print the current configuration and persistent keywords.",
    )
    group.add_argument(
        "--list-jobs",
        nargs="?",
        const=20,
        metavar="N",
        type=int,
        help="List the N most recent unnotified jobs in the DB (default 20).",
    )
    group.add_argument(
        "--export-csv",
        action="store_true",
        help="Export all unseen DB jobs to CSV and exit.",
    )
    p.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to config.yaml (default: config.yaml)",
    )
    return p


# ──────────────────────────────────────────────────────────────────
# Sub-command handlers
# ──────────────────────────────────────────────────────────────────

def _handle_test_email(config: dict) -> None:
    from notifier.email_notifier import EmailNotifier
    notifier = EmailNotifier(config)
    ok = notifier.test()
    if ok:
        print("✅  Test email sent successfully.")
    else:
        print("❌  Test email failed – check logs and credentials.")
    sys.exit(0 if ok else 1)


def _handle_add_keyword(config: dict, keyword: str) -> None:
    from storage.database import Database
    db = Database(config.get("storage", {}).get("db_path", "storage/jobs.db"))
    db.add_keyword(keyword)
    print(f"✅  Keyword added: '{keyword}'")
    sys.exit(0)


def _handle_remove_keyword(config: dict, keyword: str) -> None:
    from storage.database import Database
    db = Database(config.get("storage", {}).get("db_path", "storage/jobs.db"))
    db.remove_keyword(keyword)
    print(f"✅  Keyword removed: '{keyword}'")
    sys.exit(0)


def _handle_list_config(config: dict) -> None:
    from storage.database import Database
    db   = Database(config.get("storage", {}).get("db_path", "storage/jobs.db"))
    kws  = db.list_keywords()

    print("\n┌─ YAML Configuration ────────────────────────────────────")
    print(f"│  Keywords    : {config.get('search',{}).get('keywords', [])}")
    print(f"│  + DB extras : {kws}")
    print(f"│  Locations   : {config.get('search',{}).get('locations', [])}")
    print(f"│  Tags        : {config.get('search',{}).get('tags', [])}")
    print(f"│  Exp level   : {config.get('search',{}).get('experience_level','any')}")
    print(f"│  Remote only : {config.get('search',{}).get('remote_filter', False)}")
    print(f"│  Interval    : {config.get('scheduler',{}).get('interval_hours',3)}h")
    print(f"│  Scrapers    : {config.get('scrapers', {}).get('enabled', [])}")
    print(f"│  Email to    : {config.get('email',{}).get('recipients', [])}")
    print(f"│  Telegram    : {config.get('telegram',{}).get('enabled', False)}")
    print(f"│  DB path     : {config.get('storage', {}).get('db_path', 'storage/jobs.db')}")
    print("└──────────────────────────────────────────────────────────\n")
    sys.exit(0)


def _handle_list_jobs(config: dict, n: int) -> None:
    from storage.database import Database
    db   = Database(config.get("storage", {}).get("db_path", "storage/jobs.db"))
    jobs = db.get_unnotified_jobs()[:n]
    if not jobs:
        print("No unseen jobs in the database yet. Run the scraper first.")
        sys.exit(0)

    print(f"\n{'─'*70}")
    print(f"{'TITLE':<40} {'COMPANY':<20} {'SOURCE':<12} SCORE")
    print(f"{'─'*70}")
    for j in jobs:
        print(
            f"{j['title'][:38]:<40} "
            f"{j['company'][:18]:<20} "
            f"{j['source']:<12} "
            f"{j.get('score', 0):>5.0f}/100"
        )
        print(f"  {j['url']}")
    print(f"{'─'*70}")
    print(f"Total: {len(jobs)} unseen jobs\n")
    sys.exit(0)


def _handle_export_csv(config: dict) -> None:
    from scheduler import JobScheduler
    js   = JobScheduler(config)
    jobs = js.db.get_unnotified_jobs()
    js._export_csv(jobs)
    print(f"✅  Exported {len(jobs)} jobs to CSV.")
    sys.exit(0)


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    config = _load_config(args.config)
    _setup_logging(config)

    # ── Quick commands (no scheduler needed) ──────────────────────

    if args.test_email:
        _handle_test_email(config)

    if args.add_keyword:
        _handle_add_keyword(config, args.add_keyword)

    if args.remove_keyword:
        _handle_remove_keyword(config, args.remove_keyword)

    if args.list_config:
        _handle_list_config(config)

    if args.list_jobs is not None:
        _handle_list_jobs(config, args.list_jobs)

    if args.export_csv:
        _handle_export_csv(config)

    # ── Scheduler / run commands ──────────────────────────────────

    from scheduler import JobScheduler
    job_scheduler = JobScheduler(config)

    if args.run_now:
        logger.info("Running once (--run-now)…")
        new = job_scheduler.run_once(dry_run=False)
        job_scheduler.cleanup()
        print(f"\n✅  Done. {new} new job(s) found and notified.")
        sys.exit(0)

    if args.dry_run:
        logger.info("Running once in dry-run mode (no notifications)…")
        new = job_scheduler.run_once(dry_run=True)
        job_scheduler.cleanup()
        print(f"\n✅  Dry run done. {new} new job(s) found (not notified).")
        sys.exit(0)

    # Default: start the recurring scheduler
    print(
        f"\n🚀  Job Automation Tool started.\n"
        f"    Interval : every {config.get('scheduler',{}).get('interval_hours',3)}h\n"
        f"    Scrapers : {list(config.get('scrapers',{}).get('enabled',[]))}\n"
        f"    Press Ctrl+C to stop.\n"
    )
    job_scheduler.start_scheduler()


if __name__ == "__main__":
    main()
