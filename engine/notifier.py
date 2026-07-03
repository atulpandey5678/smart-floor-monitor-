"""Email notification service for Cologic Shop Floor Tracker.

Reads SMTP config from SettingsManager and sends alert emails.
Uses Python stdlib smtplib — no extra dependencies.

Usage:
    notifier = Notifier(settings_manager)
    notifier.send_alert(alert_type='machine_red_light', machine_id='CNC-01',
                        message='RED light detected')
"""

import logging
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# Alert type → human readable label
_ALERT_LABELS = {
    "machine_red_light": "🔴 Machine RED Light",
    "static_worker": "⚠️ Static Worker (ABANDONED)",
    "camera_offline": "📷 Camera Offline",
    "EXCEPTION": "⚠️ Worker Exception",
    "ABANDONED": "⚠️ Worker Abandoned",
}


class Notifier:
    """Thread-safe SMTP email notifier.

    Reads config from SettingsManager on every send call so changes
    take effect immediately without a restart.
    """

    def __init__(self, settings_manager=None):
        self._settings = settings_manager
        self._lock = threading.Lock()

    def _get_config(self) -> dict:
        """Return current notification settings."""
        if self._settings:
            return self._settings.section("notifications")
        return {}

    def should_notify(self, alert_type: str) -> bool:
        """Return True if this alert_type is subscribed for email delivery."""
        cfg = self._get_config()
        if not cfg.get("email_enabled"):
            return False
        notify_on = cfg.get("notify_on", [])
        # Check exact match or case-insensitive match
        return (alert_type in notify_on or
                alert_type.lower() in [n.lower() for n in notify_on])

    def send_alert(self, alert_type: str, machine_id: str = "",
                   message: str = "", badge_id: str = "") -> bool:
        """Send an alert email. Returns True on success, False on failure.

        Non-blocking — fires in a daemon thread so it never stalls the CV pipeline.
        """
        if not self.should_notify(alert_type):
            return False

        # Launch in background thread so SMTP latency doesn't block the caller
        t = threading.Thread(
            target=self._send_now,
            args=(alert_type, machine_id, message, badge_id),
            daemon=True,
        )
        t.start()
        return True

    def _send_now(self, alert_type: str, machine_id: str,
                  message: str, badge_id: str) -> None:
        """Actual blocking SMTP send — runs in its own thread."""
        cfg = self._get_config()
        host = cfg.get("smtp_host", "").strip()
        port = int(cfg.get("smtp_port", 587))
        username = cfg.get("smtp_username", "").strip()
        password = cfg.get("smtp_password", "")
        recipients = cfg.get("alert_recipients", [])
        company = "Cologic"

        if not host or not recipients:
            logger.warning("Notifier: SMTP host or recipients not configured — skipping email")
            return

        label = _ALERT_LABELS.get(alert_type, alert_type)
        subject = f"[{company}] Alert: {label}"
        if machine_id:
            subject += f" — {machine_id}"

        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build plain-text and HTML bodies
        plain = (
            f"{label}\n\n"
            f"Machine: {machine_id or '—'}\n"
            f"Details: {message or '—'}\n"
            f"Badge ID: {badge_id or '—'}\n"
            f"Time: {now_str}\n\n"
            f"— {company} Shop Floor Tracker"
        )

        html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Inter,Arial,sans-serif;background:#0B1120;color:#F9FAFB;margin:0;padding:24px">
  <div style="max-width:520px;margin:0 auto;background:#1F2937;border-radius:12px;
              border:1px solid rgba(255,255,255,0.08);overflow:hidden">
    <div style="background:#EF4444;padding:18px 24px">
      <span style="font-size:20px;font-weight:700;color:#fff">{label}</span>
    </div>
    <div style="padding:24px">
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr><td style="color:#9CA3AF;padding:6px 0;width:100px">Machine</td>
            <td style="font-weight:600">{machine_id or '—'}</td></tr>
        <tr><td style="color:#9CA3AF;padding:6px 0">Details</td>
            <td>{message or '—'}</td></tr>
        <tr><td style="color:#9CA3AF;padding:6px 0">Badge ID</td>
            <td style="font-family:monospace">{badge_id or '—'}</td></tr>
        <tr><td style="color:#9CA3AF;padding:6px 0">Time</td>
            <td>{now_str}</td></tr>
      </table>
      <div style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.08);
                  font-size:12px;color:#6B7280">
        {company} Shop Floor Tracker — automated alert
      </div>
    </div>
  </div>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = username or f"noreply@cologic.app"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        with self._lock:
            try:
                with smtplib.SMTP(host, port, timeout=10) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    if username and password:
                        server.login(username, password)
                    server.sendmail(msg["From"], recipients, msg.as_string())
                logger.info(
                    "Alert email sent: type=%s machine=%s recipients=%s",
                    alert_type, machine_id, recipients,
                )
            except Exception as e:
                logger.error("Failed to send alert email: %s", e)

    def send_report(self, subject: str, body_plain: str,
                    body_html: str = "") -> bool:
        """Send a scheduled report email to alert_recipients.

        Returns True if email was dispatched (non-blocking).
        """
        cfg = self._get_config()
        if not cfg.get("email_enabled"):
            return False
        recipients = cfg.get("alert_recipients", [])
        if not recipients:
            return False

        t = threading.Thread(
            target=self._send_report_now,
            args=(subject, body_plain, body_html or body_plain, recipients, cfg),
            daemon=True,
        )
        t.start()
        return True

    def _send_report_now(self, subject: str, body_plain: str,
                         body_html: str, recipients: list, cfg: dict) -> None:
        host = cfg.get("smtp_host", "").strip()
        port = int(cfg.get("smtp_port", 587))
        username = cfg.get("smtp_username", "").strip()
        password = cfg.get("smtp_password", "")
        if not host:
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = username or "noreply@cologic.app"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body_plain, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        with self._lock:
            try:
                with smtplib.SMTP(host, port, timeout=10) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    if username and password:
                        server.login(username, password)
                    server.sendmail(msg["From"], recipients, msg.as_string())
                logger.info("Report email sent: subject='%s' recipients=%s", subject, recipients)
            except Exception as e:
                logger.error("Failed to send report email: %s", e)


# Module-level singleton (set at startup)
_notifier: Optional[Notifier] = None


def init_notifier(settings_manager) -> Notifier:
    """Initialize the global Notifier instance."""
    global _notifier
    _notifier = Notifier(settings_manager)
    return _notifier


def get_notifier() -> Optional[Notifier]:
    """Return the global Notifier, or None if not initialized."""
    return _notifier
