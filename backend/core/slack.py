"""Slack incoming webhook delivery for HeatShield safety briefings."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .messages import _field, _plan_parts

logger = logging.getLogger("heatshield.slack")


def build_slack_payload(plan, site_name: str, language: str = "en") -> dict:
    """Build a readable Slack Block Kit payload for an approved plan."""
    hours, blocks = _plan_parts(plan)
    if not hours:
        return {"text": f"HeatShield safety briefing for {site_name} is unavailable."}

    peak = max(hours, key=lambda item: _field(item, "forecast_heat_risk"))
    idx = hours.index(peak)
    control = blocks[idx] if idx < len(blocks) else (blocks[0] if blocks else {})
    level = str(_field(peak, "level") or "UNKNOWN").upper()
    score = _field(peak, "forecast_heat_risk")
    date_str = _field(plan, "date") or "Today"
    temp = _field(peak, "temperature_c")
    humidity = _field(peak, "relative_humidity_pct")
    rest = _field(control, "rest_minutes_per_hour")
    water = _field(control, "water_every_minutes")
    allowed_work = str(_field(control, "allowed_work") or "STANDARD").replace("_", " ")

    return {
        "text": f"HeatShield safety briefing for {site_name}: {level} heat risk ({score}/100).",
        "blocks": [
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
    if not webhook_url or not webhook_url.startswith("https://hooks.slack.com/"):
        logger.warning("Invalid Slack webhook URL")
        return False

    request = Request(
        webhook_url,
        data=json.dumps(build_slack_payload(plan, site_name, language)).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "HeatShield-Safety/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # nosec B310
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.warning("Slack webhook delivery failed: %s", exc)
        return False
