"""
diagnose.py
───────────
Run this to check which scrapers are working, how many jobs
each one returns, and what errors (if any) are occurring.

Usage:
    python diagnose.py
    python diagnose.py --keyword "Software Engineer" --location "Remote"
    python diagnose.py --scraper indeed
"""

import argparse
import sys
import time
import traceback
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import yaml

# ── Colour helpers (work on Windows 10+ terminals) ───────────────
RESET  = "\033[0m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def green(s):  return f"{GREEN}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def cyan(s):   return f"{CYAN}{s}{RESET}"
def bold(s):   return f"{BOLD}{s}{RESET}"
def dim(s):    return f"{DIM}{s}{RESET}"

# Enable ANSI on Windows
import os
os.system("")


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def check_env_vars():
    """Check all environment variables and report status."""
    print(f"\n{bold('━━━ Environment Variables ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')}")

    vars_to_check = [
        ("EMAIL_USER",      "required",  "SMTP sender address"),
        ("EMAIL_PASS",      "required",  "Gmail App Password"),
        ("LINKEDIN_EMAIL",  "optional",  "LinkedIn login (more results)"),
        ("LINKEDIN_PASS",   "optional",  "LinkedIn password"),
        ("SERPAPI_KEY",     "optional",  "Google Jobs via SerpAPI"),
        ("TELEGRAM_TOKEN",  "optional",  "Telegram bot token"),
        ("TELEGRAM_CHAT_ID","optional",  "Telegram chat ID"),
    ]

    all_required_ok = True
    for var, level, desc in vars_to_check:
        val = os.environ.get(var, "")
        if val:
            masked = val[:4] + "••••••••" + val[-2:] if len(val) > 6 else "••••"
            status = green("✓ SET")
            print(f"  {status}  {var:<20} {dim(masked)}")
        else:
            if level == "required":
                status = red("✗ MISSING")
                all_required_ok = False
            else:
                status = yellow("○ not set")
            print(f"  {status}  {var:<20} {dim(desc)}")

    if not all_required_ok:
        print(f"\n  {red('▶ Add missing required vars to your .env file')}")
    return all_required_ok


def check_dependencies():
    """Check all required Python packages are installed."""
    print(f"\n{bold('━━━ Dependencies ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')}")

    packages = [
        ("requests",         "required"),
        ("bs4",              "required"),
        ("lxml",             "required"),
        ("yaml",             "required"),
        ("apscheduler",      "required"),
        ("playwright",       "required"),
        ("dotenv",           "required"),
        ("cloudscraper",     "optional"),
    ]

    all_ok = True
    for pkg, level in packages:
        try:
            __import__(pkg)
            print(f"  {green('✓')} {pkg}")
        except ImportError:
            if level == "required":
                print(f"  {red('✗')} {pkg}  {red('← pip install ' + pkg)}")
                all_ok = False
            else:
                print(f"  {yellow('○')} {pkg}  {dim('(optional)')}")

    # Special check for Playwright browsers
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        print(f"  {green('✓')} playwright chromium browser")
    except Exception as e:
        print(f"  {red('✗')} playwright chromium browser  {red('← run: playwright install chromium')}")
        all_ok = False

    return all_ok


def test_scraper(name, scraper_cls, config, keyword, location):
    """Run a single scraper and return a result dict."""
    result = {
        "name":     name,
        "status":   "unknown",
        "count":    0,
        "duration": 0,
        "error":    None,
        "sample":   [],
    }

    start = time.time()
    try:
        scraper = scraper_cls(config)
        jobs    = scraper.safe_search(keyword, location)
        scraper.cleanup()

        result["duration"] = round(time.time() - start, 1)
        result["count"]    = len(jobs)
        result["status"]   = "ok" if jobs else "empty"
        result["sample"]   = jobs[:2]   # show first 2 as proof

    except Exception as e:
        result["duration"] = round(time.time() - start, 1)
        result["status"]   = "error"
        result["error"]    = str(e)
        result["trace"]    = traceback.format_exc()

    return result


def run_scraper_tests(config, keyword, location, only=None):
    """Test all enabled scrapers (or just one if --scraper is set)."""
    from scrapers.indeed_scraper      import IndeedScraper
    from scrapers.linkedin_scraper    import LinkedInScraper
    from scrapers.simplify_scraper    import SimplifyScraper
    from scrapers.handshake_scraper   import HandshakeScraper
    from scrapers.google_jobs_scraper import GoogleJobsScraper

    registry = {
        "indeed":      IndeedScraper,
        "linkedin":    LinkedInScraper,
        "simplify":    SimplifyScraper,
        "handshake":   HandshakeScraper,
        "google_jobs": GoogleJobsScraper,
    }

    enabled = config.get("scrapers", {}).get("enabled", list(registry))
    if only:
        enabled = [only] if only in registry else []
        if not enabled:
            print(red(f"Unknown scraper '{only}'. Choose from: {list(registry.keys())}"))
            sys.exit(1)

    print(f"\n{bold('━━━ Scraper Tests ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')}")
    print(f"  {dim('Keyword:')}  {keyword}")
    print(f"  {dim('Location:')} {location}\n")

    results = []
    for name in enabled:
        cls = registry.get(name)
        if not cls:
            continue
        print(f"  {cyan('⟳')} Testing {bold(name)}...", end="", flush=True)
        result = test_scraper(name, cls, config, keyword, location)
        results.append(result)

        # Print inline result
        if result["status"] == "ok":
            print(f"\r  {green('✓')} {bold(name):<15} "
                  f"{green(str(result['count']) + ' jobs')}  "
                  f"{dim(str(result['duration']) + 's')}")
            for j in result["sample"]:
                title   = j.title[:45] if hasattr(j, 'title') else j.get('title','')[:45]
                company = j.company[:20] if hasattr(j, 'company') else j.get('company','')[:20]
                print(f"       {dim('→')} {title}  {dim('|')}  {company}")

        elif result["status"] == "empty":
            print(f"\r  {yellow('○')} {bold(name):<15} "
                  f"{yellow('0 jobs')}  "
                  f"{dim(str(result['duration']) + 's')}  "
                  f"{yellow('← try different keyword/location')}")

        else:
            print(f"\r  {red('✗')} {bold(name):<15} "
                  f"{red('ERROR')}  "
                  f"{dim(str(result['duration']) + 's')}")
            print(f"       {red(result['error'])}")

    return results


def print_summary(results, env_ok, deps_ok):
    """Print a final summary table."""
    print(f"\n{bold('━━━ Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')}")

    working = [r for r in results if r["status"] == "ok"]
    empty   = [r for r in results if r["status"] == "empty"]
    broken  = [r for r in results if r["status"] == "error"]

    total_jobs = sum(r["count"] for r in results)

    print(f"  Environment vars : {'✓ OK' if env_ok else red('✗ Missing required vars')}")
    print(f"  Dependencies     : {'✓ OK' if deps_ok else red('✗ Missing packages')}")
    print(f"  Scrapers working : {green(str(len(working)))} / {len(results)}")
    print(f"  Total jobs found : {green(str(total_jobs))}")

    if broken:
        print(f"\n  {red('Broken scrapers:')} {', '.join(r['name'] for r in broken)}")
        print(f"  {dim('Run with --verbose to see full tracebacks')}")

    if empty:
        print(f"\n  {yellow('Empty scrapers:')} {', '.join(r['name'] for r in empty)}")
        print(f"  {dim('These may work with different keywords or locations')}")

    if len(working) == len(results) and env_ok and deps_ok:
        print(f"\n  {green('🎉 Everything looks good! Run: python main.py --run-now')}")
    elif len(working) >= len(results) // 2:
        print(f"\n  {yellow('⚠  Partial – enough scrapers work to run the tool')}")
        print(f"  {dim('Run: python main.py --run-now')}")
    else:
        print(f"\n  {red('✗  Too many scrapers failing – check errors above')}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Diagnose job scraper health")
    parser.add_argument("--keyword",  default="Software Engineer", help="Test keyword")
    parser.add_argument("--location", default="Remote",            help="Test location")
    parser.add_argument("--scraper",  default=None,                help="Test one scraper only")
    parser.add_argument("--verbose",  action="store_true",         help="Show full tracebacks")
    parser.add_argument("--skip-env", action="store_true",         help="Skip env var check")
    parser.add_argument("--skip-deps",action="store_true",         help="Skip dependency check")
    args = parser.parse_args()

    print(f"\n{bold(cyan('╔══════════════════════════════════════════════════════╗'))}")
    print(f"{bold(cyan('║       Job Automation – Scraper Diagnostic Tool       ║'))}")
    print(f"{bold(cyan('╚══════════════════════════════════════════════════════╝'))}")
    print(f"  {dim(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n")

    config = load_config()

    env_ok  = check_env_vars()  if not args.skip_env  else True
    deps_ok = check_dependencies() if not args.skip_deps else True

    results = run_scraper_tests(
        config,
        keyword  = args.keyword,
        location = args.location,
        only     = args.scraper,
    )

    if args.verbose:
        for r in results:
            if r.get("trace"):
                print(f"\n{bold(red('Traceback for ' + r['name'] + ':'))}")
                print(r["trace"])

    print_summary(results, env_ok, deps_ok)


if __name__ == "__main__":
    main()