"""HeatShield API: site setup, weather-based plans, approval, Telegram, Discord, Gmail and worker check-ins."""
import base64
import hmac
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

logger = logging.getLogger("heatshield")

try:
    import boto3
except ImportError:
    boto3 = None

from core import waterlogging
from core.auth import get_caller_identity, is_authorized
from core.config import open_access, secret
from core.discord import send_discord_webhook, verify_discord_signature
from core.forecast import WeatherUnavailable, get_weather
from core.gmail import parse_recipients, send_gmail_briefing, verify_action
from core.messages import safe_bedrock_message
from core.models import SiteProfile
from core.planner import generate_plan
from core.slack import send_slack_webhook

SITES, PLANS, WORKERS, ACKS, ALERTS = {}, {}, {}, {}, {}
WATERLOGGING_REPORTS = {}

SITE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
WORKLOADS = {"LIGHT", "MODERATE", "HEAVY"}
ACCLIMATIZATIONS = {"ACCLIMATIZED", "NEW_OR_UNKNOWN"}
CLOTHING = {"NORMAL", "HEAVY_OR_PPE"}
LANGUAGES = {"en", "hi", "te"}
DISCORD_WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
DISCORD_INVITE_HOSTS = {"discord.gg", "discord.com", "www.discord.com", "discordapp.com"}
DEFAULT_TELEGRAM_BOT = "HeatShieldBot"
DEFAULT_DISCORD_INVITE = "https://discord.gg/heatshield"
# A draft built from a live forecast is rebuilt once it is this old, so the dashboard follows forecast updates.
PLAN_REFRESH_SECONDS = 3600
RISK_INPUT_FIELDS = ("latitude", "longitude", "timezone", "workStartHour", "workEndHour", "workload", "acclimatization", "clothing")


class BadRequest(ValueError):
    """The caller sent something the API cannot accept; reported as HTTP 400."""


# ── Storage ────────────────────────────────────────────────────────────

def _table():
    if not boto3 or not os.getenv("TABLE_NAME"): return None
    try: return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    except Exception as e:  # noqa: BLE001
        logger.debug("DynamoDB initialization bypassed: %s", e)
        return None

def _to_dynamo(value):
    """DynamoDB rejects Python floats; numbers are stored as Decimal."""
    if isinstance(value, float): return Decimal(str(value))
    if isinstance(value, dict): return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_to_dynamo(v) for v in value]
    return value

def _from_dynamo(value):
    if isinstance(value, Decimal): return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict): return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)): return [_from_dynamo(v) for v in value]
    return value

def _save_record(pk, sk, item):
    table = _table()
    if table:
        try:
            table.put_item(Item=_to_dynamo({"pk": pk, "sk": sk, **item}))
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to save record %s / %s to DynamoDB: %s", pk, sk, e)

def _query_site_items(table, site_id):
    from boto3.dynamodb.conditions import Key
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"SITE#{site_id}")}
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        if not page.get("LastEvaluatedKey"): return [_from_dynamo(item) for item in items]
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# ── Requests and responses ─────────────────────────────────────────────

def _headers(event):
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}

def _raw_body(event):
    raw = event.get("body")
    if raw is None: return ""
    if isinstance(raw, (dict, list)): return json.dumps(raw)
    if event.get("isBase64Encoded"):
        try: return base64.b64decode(raw).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            raise BadRequest("request body is not valid base64-encoded UTF-8") from e
    return str(raw)

def _body(event):
    text = _raw_body(event).strip()
    if not text: return {}
    try: parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise BadRequest("request body must be valid JSON") from e
    if not isinstance(parsed, dict): raise BadRequest("request body must be a JSON object")
    return parsed

def _json(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type, authorization, x-requested-with, x-telegram-bot-api-secret-token, x-discord-secret-token, x-signature-ed25519, x-signature-timestamp, x-api-key, x-clerk-auth-token",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"
        },
        "body": json.dumps(body)
    }


def _html(status, title, inner):
    page = f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title></head><body style="font-family:Arial,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;color:#182235"><h2>{escape(title)}</h2>{inner}</body></html>'
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": page}


def _authorized(event):
    return is_authorized(event)

def _telegram_secret_valid(event):
    expected = secret("TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET_PARAMETER")
    # With no secret configured the webhook is only usable in open-access mode; otherwise anyone could forge check-ins.
    if not expected: return open_access()
    return hmac.compare_digest(_headers(event).get("x-telegram-bot-api-secret-token", "").encode(), expected.encode())

def _discord_request_valid(event):
    headers = _headers(event)
    public_key = secret("DISCORD_PUBLIC_KEY", "DISCORD_PUBLIC_KEY_PARAMETER")
    if public_key:
        return verify_discord_signature(public_key, headers.get("x-signature-ed25519"), headers.get("x-signature-timestamp"), _raw_body(event))
    shared = secret("DISCORD_WEBHOOK_SECRET", "DISCORD_WEBHOOK_SECRET_PARAMETER")
    if shared:
        return hmac.compare_digest(headers.get("x-discord-secret-token", "").encode(), shared.encode())
    return open_access()

def _gmail_app_password():
    value = secret("GMAIL_APP_PASSWORD", "GMAIL_APP_PASSWORD_PARAMETER")
    return value.replace(" ", "") if value else None

def _public_base_url(event):
    configured = os.getenv("PUBLIC_API_URL")
    if configured: return configured.rstrip("/")
    domain = event.get("requestContext", {}).get("domainName")
    if domain: return f"https://{domain}"
    host = _headers(event).get("host")
    return f"http://{host}" if host else None


# ── Sites ──────────────────────────────────────────────────────────────

def _first(raw, *names):
    for name in names:
        value = raw.get(name)
        if value is not None and value != "": return value
    return None

def _number(raw, names, default, kind, low, high, label):
    value = _first(raw, *names)
    if value is None: return default
    try: number = kind(value)
    except (TypeError, ValueError) as e:
        raise BadRequest(f"{label} must be a number") from e
    if not low <= number <= high: raise BadRequest(f"{label} must be between {low} and {high}")
    return number

def _choice(raw, names, default, allowed, label):
    value = _first(raw, *names)
    if value is None: return default
    if str(value) not in allowed: raise BadRequest(f"{label} must be one of {', '.join(sorted(allowed))}")
    return str(value)

def _https_link(value, hosts, label, path_prefix="/"):
    text = str(value).strip()
    parsed = urlparse(text)
    valid = parsed.scheme == "https" and (parsed.hostname or "").lower() in hosts and len(text) <= 300 and not any(c.isspace() for c in text)
    if not valid or not parsed.path.startswith(path_prefix) or not parsed.path.strip("/"):
        raise BadRequest(f"{label} must be an https:// link on {' or '.join(sorted(hosts))}")
    return text

def _site(raw):
    """Builds a validated SiteProfile from API input or a stored record; raises BadRequest on bad values."""
    site_id = str(_first(raw, "siteId", "site_id") or uuid.uuid4().hex[:8])
    if not SITE_ID_RE.match(site_id): raise BadRequest("siteId may only contain letters, digits, '-' and '_' (max 64 characters)")
    name = " ".join(str(_first(raw, "name") or "Hyderabad Construction Site").split())[:120]
    timezone = str(_first(raw, "timezone") or "Asia/Kolkata")
    try: ZoneInfo(timezone)
    except Exception as e:  # noqa: BLE001
        raise BadRequest("timezone must be an IANA timezone name such as Asia/Kolkata") from e
    start = _number(raw, ("workStartHour", "work_start_hour"), 6, int, 0, 23, "workStartHour")
    end = _number(raw, ("workEndHour", "work_end_hour"), 18, int, 0, 23, "workEndHour")
    if start > end: raise BadRequest("workStartHour must not be later than workEndHour")
    site = SiteProfile(
        site_id, name,
        _number(raw, ("latitude",), 17.385, float, -90, 90, "latitude"),
        _number(raw, ("longitude",), 78.487, float, -180, 180, "longitude"),
        timezone, start, end,
        _choice(raw, ("workload",), "MODERATE", WORKLOADS, "workload"),
        _choice(raw, ("acclimatization",), "ACCLIMATIZED", ACCLIMATIZATIONS, "acclimatization"),
        _choice(raw, ("clothing",), "NORMAL", CLOTHING, "clothing"),
        _choice(raw, ("defaultLanguage", "default_language"), "en", LANGUAGES, "defaultLanguage"),
    )
    webhook = _first(raw, "discordWebhookUrl", "discord_webhook_url")
    if webhook: site.discord_webhook_url = _https_link(webhook, DISCORD_WEBHOOK_HOSTS, "discordWebhookUrl", "/api/webhooks/")
    site.email_recipients = parse_recipients(_first(raw, "emailRecipients", "email_recipients"))
    telegram_bot = str(_first(raw, "telegramBotUsername", "telegram_bot_username") or "").strip().lstrip("@")
    if telegram_bot:
        if not TELEGRAM_USERNAME_RE.match(telegram_bot): raise BadRequest("telegramBotUsername may only contain letters, digits and '_' (3-32 characters)")
        site.telegram_bot_username = telegram_bot
    invite = _first(raw, "discordInviteUrl", "discord_invite_url")
    if invite: site.discord_invite_url = _https_link(invite, DISCORD_INVITE_HOSTS, "discordInviteUrl")
    return site

def _site_record(site):
    """The full site as stored: one camelCase shape for both the API and DynamoDB."""
    record = {"siteId": site.site_id, "name": site.name, "latitude": site.latitude, "longitude": site.longitude, "timezone": site.timezone, "workStartHour": site.work_start_hour, "workEndHour": site.work_end_hour, "workload": site.workload, "acclimatization": site.acclimatization, "clothing": site.clothing, "defaultLanguage": site.default_language}
    if site.discord_webhook_url: record["discordWebhookUrl"] = site.discord_webhook_url
    if site.email_recipients: record["emailRecipients"] = list(site.email_recipients)
    if site.telegram_bot_username: record["telegramBotUsername"] = site.telegram_bot_username
    if site.discord_invite_url: record["discordInviteUrl"] = site.discord_invite_url
    return record

def _site_dict(site, private=False):
    """The site as returned by the API. The Discord webhook URL and email recipients are only
    included for the supervisor who just wrote them; public reads get a flag and a count."""
    d = _site_record(site)
    webhook, recipients = d.pop("discordWebhookUrl", None), d.pop("emailRecipients", None)
    d["hasDiscordWebhook"] = bool(webhook)
    d["hasSlackWebhook"] = bool(secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_PARAMETER"))
    d["emailRecipientCount"] = len(recipients or [])
    if private:
        if webhook: d["discordWebhookUrl"] = webhook
        if recipients: d["emailRecipients"] = recipients
    d["telegramBotUsername"] = site.telegram_bot_username or os.getenv("TELEGRAM_BOT_USERNAME", DEFAULT_TELEGRAM_BOT)
    d["discordInviteUrl"] = site.discord_invite_url or os.getenv("DISCORD_INVITE_URL", DEFAULT_DISCORD_INVITE)
    return d

def _save_site(site):
    SITES[site.site_id] = site
    _save_record(f"SITE#{site.site_id}", "META", _site_record(site))

def _site_from_item(item):
    try: return _site(_from_dynamo(item))
    except BadRequest as e:
        logger.error("Stored site %s is invalid and was skipped: %s", item.get("pk"), e)
        return None

def _load_site(site_id):
    if site_id in SITES: return SITES[site_id]
    table = _table()
    if table:
        try:
            item = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": "META"}).get("Item")
            site = _site_from_item(item) if item else None
            if site: SITES[site_id] = site; return site
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load site %s from DynamoDB: %s", site_id, e)
    return None

def _demo_site():
    site = _load_site("demo")
    if not site: site = _site({"siteId": "demo"}); _save_site(site)
    return site

def _all_sites():
    """Every known site; reads the table so a fresh container sees sites created elsewhere."""
    table = _table()
    if table:
        try:
            kwargs = {"FilterExpression": "sk = :meta", "ExpressionAttributeValues": {":meta": "META"}}
            while True:
                page = table.scan(**kwargs)
                for item in page.get("Items", []):
                    site = _site_from_item(item)
                    if site: SITES[site.site_id] = site
                if not page.get("LastEvaluatedKey"): break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to scan sites from DynamoDB: %s", e)
    return list(SITES.values())

def _hydrate_site_state(site_id):
    table = _table()
    if not table: return
    try:
        for item in _query_site_items(table, site_id):
            sk = item.get("sk", "")
            record = {k: v for k, v in item.items() if k not in ("pk", "sk")}
            if sk.startswith("WORKER#"): WORKERS.setdefault(site_id, {})[item.get("chatId", sk[7:])] = record
            elif sk.startswith("ACK#"): ACKS.setdefault(site_id, {})[item.get("chatId", sk.split("#")[-1])] = record
            elif sk.startswith("ALERT#"): ALERTS.setdefault(site_id, {})[item.get("alertId", sk[6:])] = record
            elif sk.startswith("PLAN#"): PLANS[(site_id, sk[5:])] = item.get("plan", record)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to hydrate state for site %s from DynamoDB: %s", site_id, e)

def _invalidate_plans(site_id):
    """Drop cached plans for a site so the next fetch regenerates them —
    needed whenever a site's location (or any risk input) changes, since a
    cached plan was computed from weather at the *old* coordinates."""
    for key in [k for k in PLANS if k[0] == site_id]:
        del PLANS[key]
    table = _table()
    if table:
        try:
            for item in _query_site_items(table, site_id):
                if item.get("sk", "").startswith("PLAN#"):
                    table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to invalidate cached plans for site %s: %s", site_id, e)


# ── Plans ──────────────────────────────────────────────────────────────

def _zone(site):
    try: return ZoneInfo(site.timezone)
    except Exception:  # noqa: BLE001
        return UTC

def _today(site):
    """Today's date where the site is, which is the date its workday plan belongs to."""
    return datetime.now(_zone(site)).strftime("%Y-%m-%d")

def _generate(site, mode="LIVE", replay_date=None):
    forecast = get_weather(site, mode, replay_date)
    resolved = forecast.get("timezone")
    if resolved and resolved != site.timezone:
        try:
            ZoneInfo(resolved); site.timezone = resolved; _save_site(site)
        except Exception:  # noqa: BLE001
            logger.warning("Ignoring unknown forecast timezone %r for site %s", resolved, site.site_id)
    # Canned demo hours carry a fixed date, so a demo plan is dated today; a live plan takes the forecast's own date.
    date = _today(site) if forecast["provider"] == "DEMO" else None
    plan = generate_plan(site, forecast["hours"], mode, replay_date, date, forecast["provider"]).to_dict()
    if not plan["hours"]:
        raise WeatherUnavailable("The forecast has no hours inside this site's work hours")
    PLANS[(site.site_id, plan["date"])] = plan
    _save_record(f"SITE#{site.site_id}", f"PLAN#{plan['date']}", {"plan": plan})
    return plan

def _stored_plan(site_id, date):
    key = (site_id, date)
    if key not in PLANS:
        table = _table()
        if table:
            try:
                saved = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": f"PLAN#{date}"}).get("Item")
                if saved: PLANS[key] = _from_dynamo(saved.get("plan", saved))
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to load plan for %s on %s from DynamoDB: %s", site_id, date, e)
    return PLANS.get(key)

def _stale(plan):
    if plan.get("status") != "DRAFT" or plan.get("mode") != "LIVE" or plan.get("weather_provider") == "DEMO": return False
    try: age = (datetime.now(UTC) - datetime.fromisoformat(plan["generated_at"])).total_seconds()
    except (KeyError, TypeError, ValueError): return True
    return age > PLAN_REFRESH_SECONDS

def _plan(site):
    """Today's plan for a site. Approved and sent plans are frozen; an old live draft is rebuilt."""
    plan = _stored_plan(site.site_id, _today(site))
    if plan and not _stale(plan): return plan
    try: return _generate(site)
    except WeatherUnavailable:
        if plan: return plan
        raise

def _peak_index(plan):
    hours = plan["hours"]
    return max(range(len(hours)), key=lambda i: hours[i]["forecast_heat_risk"])

def _controls(plan):
    """The rest/water/work rule for the hottest hour — the strictest one of the day."""
    index = _peak_index(plan)
    block = plan["blocks"][index]
    return {"time": plan["hours"][index]["time"], "level": block["level"], "restMinutesPerHour": block["rest_minutes_per_hour"], "waterEveryMinutes": block["water_every_minutes"], "allowedWork": block["allowed_work"]}

def _plan_view(plan):
    peak = plan["hours"][_peak_index(plan)]
    controls = _controls(plan)
    return {"title": "HeatShield work plan", "level": peak["level"].lower(), "message": f"Hottest at {peak['time']} ({peak['forecast_heat_risk']}/100). Plan your day carefully.", "checklist": ["Heavy work only in the safe hours — follow the work blocks below.", f"Rest breaks at the hottest hour: {controls['restMinutesPerHour']} min per hour.", f"Water breaks at the hottest hour: every {controls['waterEveryMinutes']} min."], "generated_by": "weather plan"}

def _hour_view(h):
    return {"time": h["time"], "temperature": h["temperature_c"], "humidity": h["relative_humidity_pct"], "apparentTemperature": h.get("apparent_temperature_c"), "windSpeed": h.get("wind_speed_kmh"), "radiation": h.get("direct_normal_irradiance_wm2"), "risk": h["forecast_heat_risk"], "level": h["level"]}

def _current_index(site, plan):
    """The latest forecast hour that has started at the site; the first hour before the workday begins."""
    if plan["date"] != _today(site): return 0
    now_hour = datetime.now(_zone(site)).hour
    started = [i for i, h in enumerate(plan["hours"]) if int(h["time"][:2]) <= now_hour]
    return started[-1] if started else 0

def _dashboard(site):
    _hydrate_site_state(site.site_id)
    plan = _plan(site)
    hours = [_hour_view(h) for h in plan["hours"]]
    peak = hours[_peak_index(plan)]
    plan_view = {**_plan_view(plan), "peak": peak}
    source = "Demo data" if plan.get("weather_provider") == "DEMO" else "Open-Meteo"
    return {"site": _site_dict(site), "forecast": {"location": site.name, "hourly": hours, "source": source}, "current": hours[_current_index(site, plan)], "peak": peak, "level": peak["level"].lower(), "controls": _controls(plan), "plan": plan_view, "canonicalPlan": plan, "workers": list(WORKERS.get(site.site_id, {}).values()), "acknowledgements": list(ACKS.get(site.site_id, {}).values()), "alerts": list(ALERTS.get(site.site_id, {}).values()), "updated_at": datetime.now(UTC).isoformat()}


# ── Worker check-ins ───────────────────────────────────────────────────

def _site_for_plan(plan_id):
    site_id = next((key[0] for key in PLANS if PLANS[key].get("plan_id") == plan_id), None)
    if not site_id and plan_id.count("-") >= 3:
        site_id = plan_id.rsplit("-", 3)[0]
    return site_id if site_id and SITE_ID_RE.match(site_id) else "default"

def _check_in(site_id, user_id, action, channel, name=None):
    """Records an OK acknowledgement or a Not OK alert; returns the resulting status, or None for an unknown action."""
    now = datetime.now(UTC)
    record = {"chatId": user_id, "channel": channel, "at": now.isoformat()}
    if name: record["name"] = name
    if action == "ok":
        ACKS.setdefault(site_id, {})[user_id] = {**record, "status": "ACKNOWLEDGED"}
        _save_record(f"SITE#{site_id}", f"ACK#{now.date()}#{user_id}", ACKS[site_id][user_id])
        return "ACKNOWLEDGED"
    if action == "unwell":
        ALERTS.setdefault(site_id, {})[user_id] = {**record, "alertId": user_id, "status": "OPEN"}
        _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", ALERTS[site_id][user_id])
        return "OPEN"
    return None

def _join(site_id, user_id, name, channel, language):
    WORKERS.setdefault(site_id, {})[user_id] = {"chatId": user_id, "name": name, "language": language, "status": "JOINED", "channel": channel, "joinedAt": datetime.now(UTC).isoformat()}
    _save_record(f"SITE#{site_id}", f"WORKER#{user_id}", WORKERS[site_id][user_id])

def _send_telegram(chat_id, text, plan_id):
    token = secret("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN_PARAMETER")
    if not token: return False
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [[{"text": "I'm OK", "callback_data": f"ok:{plan_id}"}, {"text": "Not OK", "callback_data": f"unwell:{plan_id}"}]]}}
    try:
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5): return True  # nosec B310
    except Exception as e:  # noqa: BLE001
        logger.warning("Telegram sendMessage failed for chat %s: %s", chat_id, type(e).__name__)
        return False


# ── Routing ────────────────────────────────────────────────────────────

def handler(event, context):
    try:
        return _route(event)
    except BadRequest as e:
        return _json(400, {"error": str(e)})
    except WeatherUnavailable as e:
        logger.warning("Weather unavailable: %s", e)
        return _json(503, {"error": "The weather forecast is unavailable right now. No plan was created; try again shortly."})
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error")
        return _json(500, {"error": "internal error"})

def _route(event):
    path = event.get("rawPath", event.get("path", "/")) or "/"
    method = str(event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET"))).upper()
    protected = (
        method in {"POST", "PUT", "DELETE"} and (path.endswith(("/sites", "/plans/generate", "/waterlogging/reports")) or "/plans/" in path or "/alerts/" in path or "/sites/" in path)
    ) or (method == "GET" and path.endswith("/sites"))
    if protected and not _authorized(event): return _json(401, {"error": "supervisor authorization required"})
    if path.endswith("/health"): return _json(200, {"ok": True, "service": "heatshield-api"})
    if path.endswith(("/auth/me", "/auth/profile")) and method == "GET":
        return _json(200, {
            "ok": True,
            "supervisor": get_caller_identity(event),
            "openAccess": open_access(),
            "clerkConfigured": bool(os.getenv("CLERK_JWT_KEY") or secret("CLERK_SECRET_KEY", "CLERK_SECRET_KEY_PARAMETER")),
        })
    if path.endswith("/dashboard") and method == "GET":
        return _json(200, _dashboard(_demo_site()))
    if path.endswith("/email/ack") and method in {"GET", "POST"}:
        return _email_ack(event, method)
    if "/waterlogging" in path and method == "GET":
        return _waterlogging_read(event, path)

    body = _body(event)
    if path.endswith("/plan") and method == "POST":
        requested = body.get("siteId")
        sites = [_load_site(str(requested))] if requested else (_all_sites() or [_demo_site()])
        plans, failed = [], []
        for site in [s for s in sites if s]:
            try: plans.append(_plan(site))
            except WeatherUnavailable: failed.append(site.site_id)
        if failed and not plans: raise WeatherUnavailable(f"no forecast for {', '.join(failed)}")
        return _json(200, {"scheduled": bool(body.get("scheduled")), "plans": plans, "failedSites": failed, "message": "Draft plans staged; supervisor approval is required before sending."})
    if path.endswith("/waterlogging/reports") and method == "POST":
        return _waterlogging_report(event, body)
    if path.endswith("/telegram/webhook") and method == "POST":
        if not _telegram_secret_valid(event): return _json(403, {"error": "invalid Telegram webhook secret"})
        return _telegram_update(body)
    if path.endswith(("/discord/webhook", "/discord/interactions")) and method == "POST":
        if not _discord_request_valid(event): return _json(401, {"error": "invalid request signature"})
        return _discord_interaction(body)
    if path.endswith("/sites") and method == "POST":
        site = _site(body); _save_site(site)
        return _json(201, {"site": _site_dict(site, private=True)})
    if path.endswith("/sites") and method == "GET":
        return _json(200, {"sites": [_site_dict(s) for s in _all_sites()]})
    if "/sites/" in path:
        return _site_route(event, path, method, body)
    if "/alerts/" in path and method == "POST":
        alert_id = path.strip("/").split("/")[-1]
        hint = str(body.get("siteId") or "")
        if SITE_ID_RE.match(hint):
            _hydrate_site_state(hint)
            groups = [(hint, ALERTS.get(hint, {}))]
        else:
            groups = list(ALERTS.items())
        for site_id, group in groups:
            if alert_id in group:
                alert = group[alert_id]
                alert["status"] = "RESOLVED"
                alert["resolvedAt"] = datetime.now(UTC).isoformat()
                alert["resolvedBy"] = get_caller_identity(event).get("name") or "supervisor"
                _save_record(f"SITE#{site_id}", f"ALERT#{alert_id}", alert)
                return _json(200, {"alert": alert})
        return _json(404, {"error": "alert not found"})
    return _json(404, {"error": "route not found"})

# ── Waterlogging watch ─────────────────────────────────────────────────

class _WaterloggingStore:
    """Lets the waterlogging module keep each cell's rainfall record in DynamoDB between cold starts."""
    @staticmethod
    def load(pk, sk):
        table = _table()
        if not table: return None
        try:
            item = table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
            return _from_dynamo(item) if item else None
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load %s / %s from DynamoDB: %s", pk, sk, e)
            return None

    @staticmethod
    def save(pk, sk, item):
        _save_record(pk, sk, item)

def _coordinates(source):
    try: lat, lon = float(source.get("lat")), float(source.get("lon"))
    except (TypeError, ValueError) as e:
        raise BadRequest("lat and lon are required numbers") from e
    if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise BadRequest("lat must be between -90 and 90, lon between -180 and 180")
    return lat, lon

def _waterlogging_reports():
    """Supervisor reports from the last two days, including ones saved by other containers."""
    cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    table = _table()
    if table:
        try:
            from boto3.dynamodb.conditions import Key
            page = table.query(KeyConditionExpression=Key("pk").eq("WLREPORT") & Key("sk").gte(cutoff))
            for item in page.get("Items", []):
                report = {k: v for k, v in _from_dynamo(item).items() if k not in ("pk", "sk")}
                WATERLOGGING_REPORTS[report["id"]] = report
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load waterlogging reports from DynamoDB: %s", e)
    waterlogging._mark("supervisor-reports", True)
    return sorted((r for r in WATERLOGGING_REPORTS.values() if r["at"] >= cutoff), key=lambda r: r["at"], reverse=True)

def _waterlogging_read(event, path):
    if path.endswith("/waterlogging/sources"):
        return _json(200, {"sources": waterlogging.source_report(), "config": waterlogging.config()})
    query = event.get("queryStringParameters") or {}
    site_id = str(query.get("siteId") or "")
    if site_id and "lat" not in query:
        site = _load_site(site_id) if SITE_ID_RE.match(site_id) else None
        if not site: return _json(404, {"error": "site not found"})
        lat, lon = site.latitude, site.longitude
    else:
        lat, lon = _coordinates(query)
    reports = _waterlogging_reports()
    focus = waterlogging.cell_for(lat, lon)
    if path.endswith("/waterlogging/point"):
        cells = [focus]
    elif path.endswith("/waterlogging"):
        cells = waterlogging.neighbourhood(lat, lon)
    else:
        return _json(404, {"error": "route not found"})
    result = waterlogging.assess_cells(cells, reports, _WaterloggingStore)
    return _json(200, {**result, "centre": {"lat": lat, "lon": lon}, "focusCellId": focus["id"], "reports": reports})

def _waterlogging_report(event, body):
    lat, lon = _coordinates(body)
    caller = get_caller_identity(event)
    now = datetime.now(UTC).isoformat()
    report = {
        "id": uuid.uuid4().hex[:12], "at": now, "lat": lat, "lon": lon,
        "cellId": waterlogging.cell_for(lat, lon)["id"],
        "note": " ".join(str(body.get("note") or "").split())[:280],
        "reportedBy": str(caller.get("name") or "supervisor")[:120],
        # Only a signed-in supervisor counts as verified; open-access (demo) reports say so.
        "verified": bool(caller.get("authenticated")),
        "source": "supervisor-reports",
    }
    WATERLOGGING_REPORTS[report["id"]] = report
    _save_record("WLREPORT", f"{now}#{report['id']}", report)
    return _json(201, {"report": report})

def _telegram_update(body):
    message = body.get("message") if isinstance(body.get("message"), dict) else {}
    text = str(message.get("text") or "")
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if text.startswith("/start") and chat_id:
        parts = text.split(maxsplit=1)
        candidate = parts[1].strip() if len(parts) > 1 else "default"
        site_id = candidate if SITE_ID_RE.match(candidate) else "default"
        _join(site_id, chat_id, str((message.get("from") or {}).get("first_name") or "Worker"), "telegram", "te")
        return _json(200, {"ok": True, "joined": site_id})
    callback = body.get("callback_query") if isinstance(body.get("callback_query"), dict) else {}
    data = str(callback.get("data") or "")
    if ":" in data:
        action, plan_id = data.split(":", 1)
        _check_in(_site_for_plan(plan_id), str((callback.get("from") or {}).get("id", "unknown")), action, "telegram")
    return _json(200, {"ok": True})

def _discord_interaction(body):
    if body.get("type") == 1:
        return _json(200, {"type": 1})
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    user = (body.get("member") or {}).get("user") or body.get("user") or {}
    user_id = str(user.get("id") or body.get("userId") or body.get("user_id") or "discord_user")
    username = user.get("username") or user.get("global_name") or body.get("name") or f"Worker_{user_id[-4:]}"
    custom_id = str(data.get("custom_id") or body.get("custom_id") or "")
    if ":" in custom_id:
        action, plan_id = custom_id.split(":", 1)
        status = _check_in(_site_for_plan(plan_id), user_id, action, "discord", username)
        if status == "ACKNOWLEDGED":
            return _json(200, {"type": 4, "data": {"content": "✅ **HeatShield Plan Acknowledged!** Stay hydrated and follow your rest intervals.", "flags": 64}})
        if status == "OPEN":
            return _json(200, {"type": 4, "data": {"content": "🚨 **Alert Recorded!** Your supervisor has been notified. Move immediately to a shaded rest area, drink cool water, and seek medical attention if symptoms persist.", "flags": 64}})
    content = str(body.get("content") or "")
    if data.get("name") in ("join", "start") or body.get("action") == "join" or content.startswith("/join"):
        options = data.get("options")
        candidate = "default"
        if options and isinstance(options, list) and isinstance(options[0], dict):
            candidate = str(options[0].get("value", "default"))
        elif body.get("siteId"):
            candidate = str(body.get("siteId"))
        elif len(content.split()) > 1:
            candidate = content.split()[1]
        target_site = candidate if SITE_ID_RE.match(candidate) else "default"
        _join(target_site, user_id, username, "discord", "en")
        return _json(200, {"type": 4, "data": {"content": f"👷 **Joined site {target_site} via Discord!** You will receive daily heat safety briefings.", "flags": 64}, "ok": True, "joined": target_site})
    plan_id = str(body.get("plan_id") or body.get("planId") or "")
    if body.get("action") and plan_id:
        status = _check_in(_site_for_plan(plan_id), user_id, str(body.get("action")), "discord", username)
        if status: return _json(200, {"ok": True, "status": status})
    return _json(200, {"ok": True})

def _email_ack(event, method):
    query = event.get("queryStringParameters") or {}
    action, site_id, plan_id, email = query.get("a", ""), query.get("site", ""), query.get("plan", ""), str(query.get("e", "")).lower()
    if action not in {"ok", "unwell"} or not verify_action(_gmail_app_password(), action, site_id, plan_id, email, query.get("s", "")):
        return _html(403, "HeatShield", "<p>This check-in link is not valid.</p>")
    if method == "GET":
        # Mail scanners prefetch links, so the GET only shows a confirm button; the POST records the check-in.
        label = "I'm OK" if action == "ok" else "Not OK"
        color = "#22A474" if action == "ok" else "#D32F2F"
        return _html(200, "HeatShield check-in", f'<form method="post"><button type="submit" style="padding:14px 22px;border:0;border-radius:8px;background:{color};color:#fff;font-size:18px;font-weight:700">{label}</button></form>')
    status = _check_in(site_id, email, action, "email", email.split("@", 1)[0])
    if status == "ACKNOWLEDGED":
        return _html(200, "Plan acknowledged", "<p>Stay hydrated and follow your rest intervals.</p>")
    return _html(200, "Alert recorded", "<p>Your supervisor has been notified. Move to a shaded rest area, drink cool water, and seek medical attention if symptoms persist.</p>")

def _site_route(event, path, method, body):
    parts = path.strip("/").split("/")
    index = parts.index("sites")
    site_id = parts[index + 1] if len(parts) > index + 1 else ""
    rest = parts[index + 2:]
    if not SITE_ID_RE.match(site_id): return _json(404, {"error": "site not found"})
    site = _load_site(site_id)

    if method == "PUT" and not rest:
        before = _site_record(site) if site else {}
        updated = _site({**before, **body, "siteId": site_id})
        _save_site(updated)
        after = _site_record(updated)
        # A plan depends on where the site is and how its crew works; channel settings leave it untouched.
        if any(before.get(field) != after.get(field) for field in RISK_INPUT_FIELDS): _invalidate_plans(site_id)
        return _json(200, {"site": _site_dict(updated, private=True)})

    if not site:
        if site_id != "demo": return _json(404, {"error": "site not found"})
        site = _demo_site()
    _hydrate_site_state(site_id)

    if rest == ["status"] and method == "GET":
        return _json(200, _dashboard(site))
    if rest == ["join"] and method == "GET":
        site_dict = _site_dict(site)
        tg_url = f"https://t.me/{site_dict['telegramBotUsername']}?start={site_id}"
        discord_invite = site_dict["discordInviteUrl"]
        dc_url = f"{discord_invite}?site={site_id}" if "?" not in discord_invite else f"{discord_invite}&site={site_id}"
        return _json(200, {"siteId": site_id, "joinCode": site_id, "joinUrl": tg_url, "telegramJoinUrl": tg_url, "discordJoinUrl": dc_url})
    if rest == ["plans", "generate"] and method == "POST":
        replaying = body.get("mode") == "REPLAY"
        replay_date = str(body.get("replayDate") or "") if replaying else None
        if replaying and not DATE_RE.match(replay_date): raise BadRequest("replayDate must be YYYY-MM-DD")
        plan = _generate(site, "REPLAY" if replaying else "LIVE", replay_date)
        return _json(200, {"plan": plan, "planView": _plan_view(plan), "controls": _controls(plan)})
    if rest[:1] == ["plans"]:
        date = rest[1] if len(rest) > 1 and DATE_RE.match(rest[1]) else _today(site)
        action = rest[-1] if len(rest) > 1 and rest[-1] in ("approve", "send") else None
        plan = _stored_plan(site_id, date)
        if not plan and date == _today(site): plan = _plan(site)
        if not plan: return _json(404, {"error": f"no plan for {date}"})
        if action == "approve" and method == "POST":
            caller = get_caller_identity(event)
            # A verified identity always wins; a name typed by the client is only trusted in open-access mode.
            approver = caller.get("name") if caller.get("authenticated") else (body.get("approvedBy") or caller.get("name"))
            plan["status"] = "APPROVED"
            plan["approvedBy"] = str(approver or "supervisor")[:120]
            plan["approvedAt"] = datetime.now(UTC).isoformat()
            _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan})
            return _json(200, {"plan": plan})
        if action == "send" and method == "POST":
            if plan.get("mode") == "REPLAY": return _json(409, {"error": "A plan built from past weather cannot be sent to workers. Create a plan from today's weather first."})
            if plan["status"] != "APPROVED": return _json(409, {"error": "Plan must be approved before sending to workers"})
            result = _send_plan(event, site, plan, date, body)
            if not result: return _json(409, {"error": "The plan was not delivered to anyone: no worker has joined yet, or no message channel is working. It is still approved; try again once a channel is set up."})
            return _json(200, result)
        return _json(200, {"plan": plan})
    return _json(404, {"error": "route not found"})

def _send_plan(event, site, plan, date, body):
    site_id = site.site_id
    workers = list(WORKERS.get(site_id, {}).values())
    tg_workers = [w for w in workers if w.get("channel") not in ("discord", "email")]
    def deliver(worker):
        return _send_telegram(worker["chatId"], safe_bedrock_message(plan, site.name, worker.get("language", site.default_language)), plan["plan_id"])
    # Sent in parallel so a large crew still fits inside the 30-second API limit.
    with ThreadPoolExecutor(max_workers=8) as pool:
        tg_delivered = sum(pool.map(deliver, tg_workers))
    discord_url = site.discord_webhook_url or secret("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_PARAMETER")
    discord_broadcast = 1 if (discord_url and send_discord_webhook(discord_url, plan, site.name, site.default_language)) else 0
    discord_workers = [w for w in workers if w.get("channel") == "discord"]
    discord_count = len(discord_workers) if discord_broadcast else 0
    slack_url = secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_PARAMETER")
    slack_broadcast = 1 if (slack_url and send_slack_webhook(slack_url, plan, site.name, site.default_language)) else 0
    custom_email_msg = str(body.get("customEmailMessage") or "")[:4000] or None
    email_delivered = send_gmail_briefing(os.getenv("GMAIL_ADDRESS"), _gmail_app_password(), site.email_recipients or parse_recipients(os.getenv("GMAIL_RECIPIENTS")), plan, site.name, site.default_language, _public_base_url(event), custom_email_msg)
    delivered = tg_delivered + discord_count + (discord_broadcast if not discord_workers else 0) + slack_broadcast + email_delivered
    channels = {"telegram": tg_delivered, "discord": discord_count + discord_broadcast, "slack": slack_broadcast, "email": email_delivered}
    # A plan that reached nobody stays APPROVED so it is never shown as sent.
    if delivered == 0: return None
    plan["status"] = "SENT"
    _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan})
    return {"plan": plan, "delivered": delivered, "channels": channels}


if __name__ == "__main__":
    from server import run
    run(int(os.environ.get("PORT", "3000")))
