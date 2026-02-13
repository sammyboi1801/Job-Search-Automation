# 🚀 Job Search Automation CLI Tool

A production-ready, terminal-based job alert engine. It scrapes multiple job boards, deduplicates listings, scores them for relevance, and emails you only the **new** ones — on a configurable schedule.

---

## 📁 Project Structure

```
job_automation/
├── main.py                    ← CLI entry point
├── config.yaml                ← All user configuration
├── requirements.txt
├── scheduler.py               ← Orchestrates runs + APScheduler loop
├── scrapers/
│   ├── base_scraper.py        ← Abstract base + Job model + rate limiting
│   ├── indeed_scraper.py
│   ├── linkedin_scraper.py    ← Uses Playwright (headless Chromium)
│   ├── simplify_scraper.py
│   ├── handshake_scraper.py
│   └── google_jobs_scraper.py ← SerpAPI or HTML fallback
├── notifier/
│   ├── email_notifier.py      ← HTML email digest via SMTP
│   └── telegram_notifier.py   ← Optional Telegram push
├── storage/
│   └── database.py            ← SQLite (dedup + keyword management)
├── logs/                      ← Auto-created. Rotating log files.
└── exports/                   ← Auto-created. CSV exports.
```

---

## ⚡ Quick Start

### 1. Clone & install dependencies

```bash
git clone https://github.com/yourname/job-automation
cd job-automation
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install Playwright browser

```bash
playwright install chromium
```

### 3. Configure

Edit `config.yaml`:

```yaml
search:
  keywords:
    - "Software Engineer"
    - "Machine Learning Engineer"
  locations:
    - "San Francisco, CA"
    - "Remote"

email:
  recipients:
    - "you@gmail.com"

scrapers:
  enabled:
    - indeed
    - linkedin
    - simplify
```

### 4. Set environment variables

```bash
# Required for email
export EMAIL_USER="youremail@gmail.com"
export EMAIL_PASS="your-gmail-app-password"   # NOT your real password

# Optional: LinkedIn login (more results)
export LINKEDIN_EMAIL="you@email.com"
export LINKEDIN_PASS="yourpassword"

# Optional: SerpAPI for Google Jobs
export SERPAPI_KEY="your-key-here"

# Optional: Telegram
export TELEGRAM_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="-100123456789"
```

> **Gmail App Password**: Go to [Google Account → Security → 2-Step Verification → App passwords](https://myaccount.google.com/apppasswords). Generate a password for "Mail".

### 5. Test email

```bash
python main.py --test-email
```

### 6. Run once to verify everything works

```bash
python main.py --dry-run      # no email sent
python main.py --run-now      # sends email with new jobs
```

### 7. Start the scheduler (runs every 3 hours by default)

```bash
python main.py
```

---

## 🖥️ CLI Reference

| Command | Description |
|---|---|
| `python main.py` | Start the scheduler (default: every 3h) |
| `python main.py --run-now` | Run once immediately, send email |
| `python main.py --dry-run` | Run once, save to DB but skip notifications |
| `python main.py --test-email` | Send a test email |
| `python main.py --add-keyword "ML Engineer"` | Add keyword to DB |
| `python main.py --remove-keyword "Data Analyst"` | Remove keyword from DB |
| `python main.py --list-config` | Print all active settings |
| `python main.py --list-jobs 30` | Show 30 most recent unseen jobs |
| `python main.py --export-csv` | Export all unseen jobs to CSV |

---

## ⚙️ Configuration Reference (`config.yaml`)

```yaml
search:
  keywords: [...]         # Job titles / roles to search
  tags: [...]             # Used for relevance scoring (AI, ML, Python…)
  locations: [...]        # List of cities or "Remote"
  experience_level: ""    # entry | mid | senior | lead (leave blank = any)
  remote_filter: false    # true = remote listings only

scheduler:
  interval_hours: 3       # Run frequency
  send_empty_email: false # Send email even when no new jobs

scrapers:
  enabled:                # Which scrapers to run
    - indeed
    - linkedin
    - simplify
    - handshake
    - google_jobs
  rate_limit_seconds: 3   # Delay between requests
  max_retries: 3
  headless: true          # Playwright headless mode

email:
  recipients: ["you@email.com"]
  smtp_host: "smtp.gmail.com"
  smtp_port: 587

telegram:
  enabled: false          # Set to true + env vars to enable

export:
  csv_enabled: true
  csv_path: "exports/jobs_export.csv"
```

---

## 🔐 Anti-Bot & Ethical Scraping

| Platform | Method | Notes |
|---|---|---|
| Indeed | `cloudscraper` + rotating UA | Respects rate limits |
| LinkedIn | Playwright headless Chromium | Random delays, UA spoofing |
| Simplify | requests + JSON API | Lightweight, official-ish endpoint |
| Handshake | requests JSON API → Playwright fallback | |
| Google Jobs | SerpAPI (preferred) or HTML fallback | |

- All scrapers implement **exponential back-off** on failures
- `robots.txt` is checked before fetching
- One site failing **never** crashes others

---

## 🔄 Running as a Background Process

### macOS / Linux — `nohup`

```bash
nohup python main.py > logs/stdout.log 2>&1 &
echo $! > .pid
# Stop: kill $(cat .pid)
```

### macOS / Linux — `screen`

```bash
screen -S job-bot
source .venv/bin/activate
python main.py
# Detach: Ctrl+A then D
# Reattach: screen -r job-bot
```

### Linux — `systemd` service

Create `/etc/systemd/system/job-automation.service`:

```ini
[Unit]
Description=Job Search Automation
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/job_automation
ExecStart=/home/ubuntu/job_automation/.venv/bin/python main.py
Environment="EMAIL_USER=you@gmail.com"
Environment="EMAIL_PASS=yourapppassword"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable job-automation
sudo systemctl start job-automation
sudo systemctl status job-automation
```

### Windows — Task Scheduler

1. Open Task Scheduler → Create Basic Task
2. Set trigger: **Daily**, repeat every 3 hours
3. Action: Start program → `python` with argument `C:\path\to\job_automation\main.py --run-now`

---

## ☁️ AWS EC2 Deployment (Bonus)

```bash
# 1. Spin up a t3.micro (free tier) running Ubuntu 22.04
# 2. SSH in and install dependencies
sudo apt update && sudo apt install -y python3-pip python3-venv git
git clone https://github.com/yourname/job-automation
cd job-automation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium   # install system deps

# 3. Set env vars (use a .env file or AWS Secrets Manager)
cp .env.example .env
nano .env  # fill in EMAIL_USER, EMAIL_PASS, etc.
source .env

# 4. Run as systemd service (see above) OR use tmux
tmux new -s jobs
python main.py
# Ctrl+B, D to detach
```

---

## 🧩 Adding a New Job Board

1. Create `scrapers/mynewsite_scraper.py`
2. Inherit from `BaseScraper`, set `SOURCE_NAME = "mynewsite"`
3. Implement `search(self, keyword: str, location: str) -> List[Job]`
4. Register it in `scheduler.py`:
   ```python
   from scrapers.mynewsite_scraper import MyNewSiteScraper
   SCRAPER_REGISTRY["mynewsite"] = MyNewSiteScraper
   ```
5. Add `"mynewsite"` to `scrapers.enabled` in `config.yaml`

That's it. The dedup, scoring, notification and scheduling all happen automatically.

**Minimal scraper template:**

```python
from scrapers.base_scraper import BaseScraper, Job
from typing import List

class MyNewSiteScraper(BaseScraper):
    SOURCE_NAME = "mynewsite"

    def search(self, keyword: str, location: str) -> List[Job]:
        jobs = []
        resp = self._get(f"https://mynewsite.com/jobs?q={keyword}&l={location}")
        if not resp:
            return jobs
        # parse resp.text / resp.json() ...
        jobs.append(Job(title="...", company="...", url="..."))
        return jobs
```

---

## 🧪 Testing & Debug

```bash
# Dry run (no email, but logs everything)
python main.py --dry-run

# Verbose logging
# In config.yaml: logging.level: DEBUG

# Show queued jobs in DB
python main.py --list-jobs 50

# Tail logs
tail -f logs/job_scraper.log
```

---

## 🏆 Feature Summary

| Feature | Status |
|---|---|
| Multi-platform scraping (5 sites) | ✅ |
| Keyword + location × scraper matrix | ✅ |
| SQLite deduplication | ✅ |
| Relevance scoring 0–100 | ✅ |
| HTML email digest | ✅ |
| Telegram notifications | ✅ |
| CSV export | ✅ |
| APScheduler every X hours | ✅ |
| Playwright anti-bot for LinkedIn | ✅ |
| Rotating user agents | ✅ |
| robots.txt compliance | ✅ |
| Graceful failure isolation | ✅ |
| CLI keyword management | ✅ |
| systemd / background process docs | ✅ |
| AWS EC2 deployment guide | ✅ |
