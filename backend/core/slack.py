"""Slack incoming webhook delivery for HeatShield safety briefings."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .messages import DEMO_NOTICE, _field, _plan_parts, peak_index

logger = logging.getLogger("heatshield.slack")


def build_slack_payload(plan, site_name: str, language: str = "en") -> dict:
    """Build a readable Slack Block Kit payload for an approved plan."""
    hours, blocks = _plan_parts(plan)
    if not hours:
        return {"text": f"HeatShield safety briefing for {site_name} is unavailable."}

    idx = peak_index(hours)
    peak = hours[idx]
    control = blocks[idx] if idx < len(blocks) else (blocks[0] if blocks else {})
    level = str(_field(peak, "level") or "UNKNOWN").upper()
    score = _field(peak, "forecast_heat_risk")
    date_str = _field(plan, "date") or "Today"
    temp = _field(peak, "temperature_c")
    humidity = _field(peak, "relative_humidity_pct")
    rest = _field(control, "rest_minutes_per_hour")
    water = _field(control, "water_every_minutes")
    allowed_work = str(_field(control, "allowed_work") or "STANDARD").replace("_", " ")

    demo = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{DEMO_NOTICE}*"}}] if _field(plan, "weather_provider") == "DEMO" else []
    return {
        "text": f"HeatShield safety briefing for {site_name}: {level} heat risk ({score}/100).",
        "blocks": demo + [
            {"type": "header", "text": {"type": "plain_text", "text": f"HeatShield Safety Briefing — {site_name}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Date:* {date_str}\n*Peak risk:* *{level}* ({score}/100) at {_field(peak, 'time')}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Peak conditions*\n{temp}°C · {humidity}% humidity"},
                {"type": "mrkdwn", "text": f"*Hydration*\nWater every {water} minutes"},
                {"type": "mrkdwn", "text": f"*Work / rest*\n{rest} minutes rest per hour"},
                {"type": "mrkdwn", "text": f"*Heavy work rule*\n{allowed_work}"},
            ]},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Forecast guidance only. Follow site safety rules and supervisor instructions."}]},
        ],
    }


def send_slack_webhook(webhook_url: str, plan, site_name: str, language: str = "en") -> bool:
    """Send a safety briefing to a Slack incoming webhook."""
    return _post(webhook_url, build_slack_payload(plan, site_name, language))


def send_slack_text(webhook_url: str, text: str) -> bool:
    """Send a short plain message, such as a worker alert, to a Slack incoming webhook."""
    return _post(webhook_url, {"text": text})


def _post(webhook_url: str, payload: dict) -> bool:
    if not webhook_url or not webhook_url.startswith("https://hooks.slack.com/"):
        logger.warning("Invalid Slack webhook URL")
        return False

    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "HeatShield-Safety/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # nosec B310
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.warning("Slack webhook delivery failed: %s", type(exc).__name__)
        return False
