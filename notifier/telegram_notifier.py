"""
notifier/telegram_notifier.py
──────────────────────────────
Bonus: Push new job summaries to a Telegram chat.

Setup:
  1. Create a bot via @BotFather → get TELEGRAM_TOKEN
  2. Start a chat with the bot (or use a group)
  3. Get your chat ID: https://api.telegram.org/bot<TOKEN>/getUpdates
  4. Export env vars:
       export TELEGRAM_TOKEN="123456:ABCDEF..."
       export TELEGRAM_CHAT_ID="-100123456789"
"""

import logging
import os
from typing import Any, Dict, List

from scrapers.base_scraper import BaseScraper  # reuse rate-limited _get/_post

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    def __init__(self, config: dict) -> None:
        self.config   = config
        self.enabled  = config.get("telegram", {}).get("enabled", False)
        self.token    = os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id  = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._base    = _BASE.format(token=self.token)

        if self.enabled and (not self.token or not self.chat_id):
            logger.warning(
                "Telegram enabled but TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set."
            )

    def send(self, jobs: List[Dict[str, Any]]) -> bool:
        if not self.enabled:
            return True
        if not self.token or not self.chat_id:
            return False
        if not jobs:
            return True

        import requests

        # Send a brief message per job (batch up to 10 to avoid spam)
        for job in jobs[:10]:
            text = (
                f"🚀 *{_esc(job.get('title',''))}*\n"
                f"🏢 {_esc(job.get('company',''))} · {_esc(job.get('location',''))}\n"
                f"📅 {_esc(job.get('date_posted',''))}\n"
                f"⭐ Relevance: {job.get('score',0):.0f}/100\n"
                f"🔗 [Apply Here]({job.get('url','#')})"
            )
            try:
                resp = requests.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id":    self.chat_id,
                        "text":       text,
                        "parse_mode": "MarkdownV2",
                        "disable_web_page_preview": False,
                    },
                    timeout=10,
                )
                if not resp.ok:
                    logger.warning("Telegram send failed: %s", resp.text)
            except Exception as exc:
                logger.error("Telegram error: %s", exc)

        if len(jobs) > 10:
            try:
                requests.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text":    f"_…and {len(jobs) - 10} more jobs\\. Check your email for the full digest\\._",
                        "parse_mode": "MarkdownV2",
                    },
                    timeout=10,
                )
            except Exception:
                pass

        logger.info("Telegram: sent %d job notifications", min(len(jobs), 10))
        return True


def _esc(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))
