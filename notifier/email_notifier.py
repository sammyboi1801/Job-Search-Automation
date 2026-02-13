"""
notifier/email_notifier.py
───────────────────────────
Sends a richly-formatted HTML email summarising newly-found jobs.

Credentials are read from environment variables:
  EMAIL_USER  –  your "from" address (also used for SMTP auth)
  EMAIL_PASS  –  app password (use Gmail App Password, not your real password)

Config overrides accepted in config.yaml under the "email" key:
  smtp_host, smtp_port, recipients
"""

import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends HTML job digest emails via SMTP (TLS)."""

    def __init__(self, config: dict) -> None:
        email_cfg         = config.get("email", {})
        self.sender       = os.environ.get("EMAIL_USER", email_cfg.get("sender", ""))
        self.password     = os.environ.get("EMAIL_PASS", "")
        self.recipients   = email_cfg.get("recipients", [])
        self.smtp_host    = email_cfg.get("smtp_host", "smtp.gmail.com")
        self.smtp_port    = int(email_cfg.get("smtp_port", 587))
        self.send_empty   = config.get("scheduler", {}).get("send_empty_email", False)

        if not self.sender:
            logger.warning("EMAIL_USER env var not set – emails will not send.")
        if not self.password:
            logger.warning("EMAIL_PASS env var not set – emails will not send.")

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def send(self, jobs: List[Dict[str, Any]], dry_run: bool = False) -> bool:
        """
        Send a digest email for the provided job list.

        Args:
            jobs:    List of job dicts (title, company, location, url, …)
            dry_run: If True, build the email but do NOT actually send it.

        Returns:
            True if email was sent (or dry_run), False on error.
        """
        if not jobs and not self.send_empty:
            logger.info("No new jobs – skipping email.")
            return True

        subject  = self._subject(len(jobs))
        html     = self._build_html(jobs)
        plain    = self._build_plain(jobs)

        if dry_run:
            logger.info("DRY RUN – email not sent. Subject: %s", subject)
            logger.debug("Plain body:\n%s", plain)
            return True

        if not self.sender or not self.password:
            logger.error("Email credentials missing – cannot send.")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = self.sender
            msg["To"]      = ", ".join(self.recipients)

            msg.attach(MIMEText(plain, "plain"))
            msg.attach(MIMEText(html,  "html"))

            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())

            logger.info(
                "Email sent to %s — %d jobs in digest", self.recipients, len(jobs)
            )
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP auth failed. For Gmail, use an App Password: "
                "https://support.google.com/accounts/answer/185833"
            )
            return False
        except Exception as exc:
            logger.error("Failed to send email: %s", exc, exc_info=True)
            return False

    def test(self) -> bool:
        """Send a test email with a single fake job."""
        fake = [{
            "title":       "Test Job – Email Is Working 🎉",
            "company":     "Anthropic",
            "location":    "Remote",
            "url":         "https://example.com",
            "date_posted": datetime.utcnow().strftime("%Y-%m-%d"),
            "source":      "test",
            "description": "This is a test notification from your Job Automation tool.",
            "score":       100.0,
        }]
        return self.send(fake)

    # ──────────────────────────────────────────────────────────────
    # Email builders
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _subject(count: int) -> str:
        ts = datetime.utcnow().strftime("%b %d, %Y %H:%M UTC")
        if count == 0:
            return f"🔎 Job Alert — No New Jobs Found ({ts})"
        return f"🚀 Job Alert — {count} New Job{'s' if count != 1 else ''} Found ({ts})"

    @staticmethod
    def _group_by_source(jobs: List[Dict]) -> Dict[str, List[Dict]]:
        groups: Dict[str, List[Dict]] = {}
        for j in jobs:
            src = j.get("source", "unknown").capitalize()
            groups.setdefault(src, []).append(j)
        # Sort within each group by score desc
        for src in groups:
            groups[src].sort(key=lambda x: x.get("score", 0), reverse=True)
        return groups

    def _build_html(self, jobs: List[Dict]) -> str:
        groups = self._group_by_source(jobs)
        ts     = datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")

        sections = ""
        for source, source_jobs in groups.items():
            cards = ""
            for j in source_jobs:
                score_bar = "▓" * int(j.get("score", 0) / 10) + "░" * (10 - int(j.get("score", 0) / 10))
                cards += f"""
                <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;
                            padding:16px 20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                      <a href="{j.get('url','#')}"
                         style="font-size:15px;font-weight:600;color:#1d4ed8;text-decoration:none;">
                        {j.get('title','')}
                      </a>
                      <div style="color:#374151;font-size:13px;margin-top:4px;">
                        <strong>{j.get('company','')}</strong>
                        &nbsp;·&nbsp;{j.get('location','')}
                        {'&nbsp;·&nbsp;<span style="color:#6b7280">' + j.get('date_posted','') + '</span>' if j.get('date_posted') else ''}
                      </div>
                      {('<div style="color:#4b5563;font-size:12px;margin-top:6px;line-height:1.5;">'
                         + j.get('description','')[:200] + '…</div>') if j.get('description') else ''}
                    </div>
                    <div style="text-align:right;min-width:90px;margin-left:12px;">
                      <span style="font-size:11px;color:#6b7280;">Relevance</span><br>
                      <span style="font-family:monospace;font-size:11px;color:#10b981;">{score_bar}</span><br>
                      <span style="font-size:13px;font-weight:700;color:#10b981;">{j.get('score',0):.0f}/100</span>
                    </div>
                  </div>
                  <div style="margin-top:10px;">
                    <a href="{j.get('url','#')}"
                       style="display:inline-block;padding:6px 14px;background:#1d4ed8;
                              color:#fff;border-radius:6px;font-size:12px;text-decoration:none;">
                      Apply →
                    </a>
                  </div>
                </div>
                """
            sections += f"""
            <div style="margin-bottom:32px;">
              <h2 style="font-size:17px;font-weight:700;color:#111827;margin:0 0 12px;
                         padding-bottom:6px;border-bottom:2px solid #e5e7eb;">
                {source} <span style="font-weight:400;color:#6b7280;">({len(source_jobs)} job{'s' if len(source_jobs)!=1 else ''})</span>
              </h2>
              {cards}
            </div>
            """

        body = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
          <div style="max-width:660px;margin:32px auto;padding:0 16px;">

            <!-- Header -->
            <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);border-radius:12px;
                        padding:28px 32px;color:#fff;margin-bottom:28px;">
              <div style="font-size:24px;font-weight:800;margin-bottom:4px;">🚀 Job Alert</div>
              <div style="font-size:14px;opacity:.85;">
                {len(jobs)} new listing{'s' if len(jobs)!=1 else ''} found &nbsp;·&nbsp; {ts}
              </div>
            </div>

            <!-- Sections by source -->
            {sections if jobs else '<p style="color:#6b7280;text-align:center;">No new jobs found this run.</p>'}

            <!-- Footer -->
            <div style="text-align:center;color:#9ca3af;font-size:11px;margin-top:32px;padding-bottom:24px;">
              Sent by Job Automation Tool · 
              <a href="https://github.com" style="color:#9ca3af;">Manage alerts</a>
            </div>
          </div>
        </body>
        </html>
        """
        return body

    @staticmethod
    def _build_plain(jobs: List[Dict]) -> str:
        lines = [
            f"Job Alert – {len(jobs)} New Jobs",
            "=" * 50,
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
        ]
        groups: Dict[str, List] = {}
        for j in jobs:
            src = j.get("source", "unknown").capitalize()
            groups.setdefault(src, []).append(j)

        for src, src_jobs in groups.items():
            lines.append(f"\n── {src} ({len(src_jobs)} jobs) ──")
            for j in src_jobs:
                lines.append(f"\n  Title:   {j.get('title','')}")
                lines.append(f"  Company: {j.get('company','')} · {j.get('location','')}")
                if j.get("date_posted"):
                    lines.append(f"  Posted:  {j.get('date_posted')}")
                lines.append(f"  Score:   {j.get('score', 0):.0f}/100")
                lines.append(f"  Link:    {j.get('url','')}")

        if not jobs:
            lines.append("No new jobs found this run.")

        return "\n".join(lines)
