"""Gmail integration for HeatShield: briefing emails with signed check-in links, sent over Gmail SMTP."""
import hashlib
import hmac
import logging
import re
import smtplib
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode

from .messages import _field, safe_bedrock_message

logger = logging.getLogger("heatshield.gmail")

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465

_EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")

ACTION_LABELS = {
    "en": ("I'm OK", "Not OK"),
    "hi": ("मैं ठीक हूँ", "ठीक नहीं हूँ"),
    "te": ("నేను సరిగా ఉన్నాను", "సరిగా లేను"),
}


def parse_recipients(raw) -> list[str]:
    """Normalizes a list or comma/space separated string into unique, valid addresses."""
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    recipients = []
    for item in raw or []:
        address = str(item).strip().lower()
        if _EMAIL_RE.match(address) and address not in recipients:
            recipients.append(address)
    return recipients


def sign_action(secret: str, action: str, site_id: str, plan_id: str, email: str) -> str:
    """HMAC signature binding a check-in link to one recipient, plan, and action."""
    message = "\n".join((action, site_id, plan_id, email.lower())).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_action(secret: str, action: str, site_id: str, plan_id: str, email: str, signature: str) -> bool:
    if not secret or not signature:
        return False
    return hmac.compare_digest(sign_action(secret, action, site_id, plan_id, email), str(signature))


def build_action_url(base_url: str, secret: str, action: str, site_id: str, plan_id: str, email: str) -> str:
    query = urlencode({"a": action, "site": site_id, "plan": plan_id, "e": email, "s": sign_action(secret, action, site_id, plan_id, email)})
    return f"{base_url.rstrip('/')}/email/ack?{query}"


def build_gmail_message(sender: str, recipient: str, plan, site_name: str, language: str = "en", action_urls: dict | None = None, custom_message: str | None = None) -> EmailMessage:
    """Builds the briefing email (plain text + HTML) for a single recipient."""
    text = custom_message or safe_bedrock_message(plan, site_name, language)
    ok_label, unwell_label = ACTION_LABELS.get(language, ACTION_LABELS["en"])
    date_str = _field(plan, "date") or "today"

    message = EmailMessage()
    message["Subject"] = f"HeatShield heat safety briefing: {site_name} ({date_str})"
    message["From"] = f"HeatShield <{sender}>"
    message["To"] = recipient

    plain = text
    buttons = ""
    if action_urls:
        plain += f"\n\n{ok_label}: {action_urls['ok']}\n{unwell_label}: {action_urls['unwell']}"
        style = "display:inline-block;padding:12px 18px;margin:4px 8px 4px 0;border-radius:8px;color:#fff;font-weight:700;text-decoration:none"
        buttons = (
            f'<p><a href="{escape(action_urls["ok"])}" style="{style};background:#22A474">{escape(ok_label)}</a>'
            f'<a href="{escape(action_urls["unwell"])}" style="{style};background:#D32F2F">{escape(unwell_label)}</a></p>'
        )
    message.set_content(plain)
    body = escape(text).replace("\n", "<br>")
    message.add_alternative(
        f'<div style="font-family:Arial,sans-serif;font-size:16px;line-height:1.5;color:#182235;max-width:520px">{body}{buttons}</div>',
        subtype="html",
    )
    return message


def send_gmail_briefing(sender: str, app_password: str, recipients: list[str], plan, site_name: str, language: str = "en", base_url: str | None = None, custom_message: str | None = None) -> int:
    """Emails the briefing to each recipient through Gmail SMTP; returns how many were accepted.

    Check-in links are included only when a public base URL is known.
    If custom_message is provided, it replaces the auto-generated briefing text.
    """
    if not sender or not app_password or not recipients:
        return 0
    # Google shows app passwords in space-separated groups; the spaces are not part of the secret.
    app_password = app_password.replace(" ", "")
    site_id = str(_field(plan, "site_id") or "default")
    plan_id = str(_field(plan, "plan_id") or "plan")
    delivered = 0
    try:
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=10) as smtp:
            smtp.login(sender, app_password)
            for recipient in recipients:
                action_urls = None
                if base_url:
                    action_urls = {action: build_action_url(base_url, app_password, action, site_id, plan_id, recipient) for action in ("ok", "unwell")}
                try:
                    smtp.send_message(build_gmail_message(sender, recipient, plan, site_name, language, action_urls, custom_message))
                    delivered += 1
                except smtplib.SMTPException as e:
                    logger.warning("Gmail delivery failed for %s: %s", recipient, e)
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("Gmail SMTP session failed: %s", e)
    return delivered
