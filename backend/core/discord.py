"""Discord integration for HeatShield: rich safety embeds, action components, and webhook delivery."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .messages import _field, _plan_parts

logger = logging.getLogger("heatshield.discord")

# Color mapping by Heat Risk level
RISK_COLORS = {
    "LOW": 0x22A474,       # Green
    "MODERATE": 0xFFBE55,  # Amber/Yellow
    "HIGH": 0xF4774E,      # Orange
    "EXTREME": 0xD32F2F,   # Red
}


def build_discord_embed(plan, site_name: str, language: str = "en") -> dict:
    """Constructs a Discord Embed representing the HeatShield daily plan."""
    hours, blocks = _plan_parts(plan)
    peak = max(hours, key=lambda item: _field(item, "forecast_heat_risk"))
    level = str(_field(peak, "level"))
    score = _field(peak, "forecast_heat_risk")
    time_str = _field(peak, "time")
    temp = _field(peak, "temperature_c")
    humidity = _field(peak, "relative_humidity_pct")
    
    idx = hours.index(peak)
    control = blocks[idx] if idx < len(blocks) else blocks[0]
    rest = _field(control, "rest_minutes_per_hour")
    water = _field(control, "water_every_minutes")
    allowed_work = _field(control, "allowed_work") or "STANDARD"
    date_str = _field(plan, "date") or "Today"

    color = RISK_COLORS.get(level.upper(), 0x3B82F6)

    disclaimer = "Forecast guidance only. Follow site safety rules & supervisor instructions."
    if language == "hi":
        disclaimer = "यह पूर्वानुमान आधारित मार्गदर्शन है; अपने सुपरवाइज़र और सुरक्षा नियमों का पालन करें।"
    elif language == "te":
        disclaimer = "ఇది వాతావరణ అంచనా ఆధారిత మార్గదర్శకం; సూపర్‌వైజర్ మరియు భద్రతా నియమాలను పాటించండి।"

    fields = [
        {"name": "🔥 Peak Heat Risk", "value": f"**{level}** ({score}/100) at {time_str}", "inline": True},
        {"name": "🌡️ Peak Conditions", "value": f"{temp}°C · {humidity}% Humidity", "inline": True},
        {"name": "💧 Hydration Goal", "value": f"Drink water every **{water} mins**", "inline": True},
        {"name": "⏱️ Work / Rest Cycle", "value": f"**{rest} mins rest** each hour", "inline": True},
        {"name": "🛡️ Heavy Work Rule", "value": f"`{allowed_work.replace('_', ' ')}`", "inline": True},
        {"name": "👷 Status", "value": "Supervisor Approved", "inline": True},
    ]

    return {
        "title": f"☀️ HeatShield Safety Briefing — {site_name}",
        "description": f"Mandatory heat safety advisory for **{date_str}**.",
        "color": color,
        "fields": fields,
        "footer": {"text": disclaimer},
    }


def build_discord_components(plan_id: str) -> list[dict]:
    """Builds interactive Discord action buttons for worker check-ins."""
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 3,  # SUCCESS (Green)
                    "label": "Acknowledge (OK)",
                    "custom_id": f"ok:{plan_id}",
                    "emoji": {"name": "✅"},
                },
                {
                    "type": 2,  # Button
                    "style": 4,  # DANGER (Red)
                    "label": "I Feel Unwell",
                    "custom_id": f"unwell:{plan_id}",
                    "emoji": {"name": "🚨"},
                },
            ],
        }
    ]


def build_discord_payload(plan, site_name: str, language: str = "en", include_components: bool = True) -> dict:
    """Builds the complete Discord message payload."""
    plan_id = _field(plan, "plan_id") or "plan"
    embed = build_discord_embed(plan, site_name, language)
    payload = {
        "content": f"📢 **Heat Safety Briefing for {site_name}**",
        "embeds": [embed],
    }
    if include_components:
        payload["components"] = build_discord_components(plan_id)
    return payload


def send_discord_webhook(webhook_url: str, plan, site_name: str, language: str = "en") -> bool:
    """Dispatches a safety briefing to a Discord Channel Webhook.
    
    Falls back gracefully to embed-only if custom interactive components
    require application bot permissions.
    """
    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Invalid or non-HTTPS Discord webhook URL: %s", webhook_url)
        return False

    headers = {"Content-Type": "application/json", "User-Agent": "HeatShield-Safety/1.0"}

    # Attempt 1: Full payload with interactive action buttons
    payload_with_buttons = build_discord_payload(plan, site_name, language, include_components=True)
    try:
        req = Request(
            webhook_url,
            data=json.dumps(payload_with_buttons).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=5) as response:  # nosec B310
            if response.status in (200, 204):
                return True
    except HTTPError as e:
        # Standard Discord webhooks without a registered application return 400 for component buttons
        if e.code == 400:
            logger.debug("Discord webhook rejected interactive components; falling back to embed-only")
        else:
            logger.warning("Discord webhook HTTP error: %s", e)
            return False
    except (URLError, TimeoutError, OSError) as e:
        logger.warning("Discord webhook network failure: %s", e)
        return False

    # Attempt 2: Fallback to rich embed without components
    payload_embed_only = build_discord_payload(plan, site_name, language, include_components=False)
    try:
        req = Request(
            webhook_url,
            data=json.dumps(payload_embed_only).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=5) as response:  # nosec B310
            return response.status in (200, 204)
    except Exception as e:  # noqa: BLE001
        logger.warning("Discord webhook fallback failed: %s", e)
        return False
