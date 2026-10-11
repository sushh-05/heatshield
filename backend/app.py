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

from core import followup, waterlogging
from core.auth import authenticate, get_caller_identity, is_authorized
from core.config import open_access, secret
from core.discord import send_discord_text, send_discord_webhook, verify_discord_signature
from core.forecast import WeatherUnavailable, get_weather
from core.gmail import parse_recipients, send_gmail_briefing, verify_action
from core.messages import DEMO_NOTICE, peak_index, safe_bedrock_message
from core.models import SiteProfile
from core.planner import generate_plan
from core.slack import send_slack_text, send_slack_webhook

SITES, PLANS, WORKERS, ACKS, ALERTS = {}, {}, {}, {}, {}
DELIVERIES = {}
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
# A draft built from a live forecast is rebuilt once it is this old, so the dashboard follows forecast updates.
PLAN_REFRESH_SECONDS = 3600
RISK_INPUT_FIELDS = ("latitude", "longitude", "timezone", "workStartHour", "workEndHour", "workload", "acclimatization", "clothing")


MAX_EMAIL_RECIPIENTS = 200
ACK_TTL_DAYS, REPORT_TTL_DAYS, PLAN_TTL_DAYS = 30, 14, 180
# Field names accepted in snake_case as well as the camelCase the API stores.
SITE_FIELD_ALIASES = {"site_id": "siteId", "work_start_hour": "workStartHour", "work_end_hour": "workEndHour", "default_language": "defaultLanguage", "discord_webhook_url": "discordWebhookUrl", "email_recipients": "emailRecipients", "telegram_bot_username": "telegramBotUsername", "discord_invite_url": "discordInviteUrl"}


class BadRequest(ValueError):
    """The caller sent something the API cannot accept; reported as HTTP 400."""


class Conflict(Exception):
    """The request is valid but cannot be applied to the current state; reported as HTTP 409."""


class StorageError(Exception):
    """A record could not be saved; reported as HTTP 503 so nothing is shown as recorded when it was not."""


# ── Storage ────────────────────────────────────────────────────────────

_tables = {}

def _table():
    name = os.getenv("TABLE_NAME")
    if not boto3 or not name: return None
    if name not in _tables:
        try: _tables[name] = boto3.resource("dynamodb").Table(name)
        except Exception as e:  # noqa: BLE001
            logger.debug("DynamoDB initialization bypassed: %s", e)
            return None
    return _tables[name]

def _expires(days):
    """Epoch seconds for the table's TTL attribute."""
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())

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
            logger.error("Failed to save record %s / %s to DynamoDB: %s", pk, sk, type(e).__name__)
            raise StorageError(f"could not save {sk.split('#')[0]}") from e

def _query_site_items(table, site_id, prefix=None):
    from boto3.dynamodb.conditions import Key
    condition = Key("pk").eq(f"SITE#{site_id}")
    if prefix: condition = condition & Key("sk").begins_with(prefix)
    items, kwargs = [], {"KeyConditionExpression": condition}
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
            "Access-Control-Allow-Origin": os.getenv("ALLOWED_ORIGIN") or "*",
            "Access-Control-Allow-Headers": "content-type, authorization, x-requested-with, x-telegram-bot-api-secret-token, x-discord-secret-token, x-signature-ed25519, x-signature-timestamp, x-api-key, x-clerk-auth-token",
            "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS"
        },
        "body": json.dumps(body)
    }


def _html(status, title, inner):
    page = f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title></head><body style="font-family:Arial,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;color:#182235"><h2>{escape(title)}</h2>{inner}</body></html>'
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": page}


def _is_scheduler(event):
    """The morning schedule invokes the function directly; API Gateway events always name the API's domain."""
    context = event.get("requestContext") or {}
    return event.get("source") == "heatshield.scheduler" and "domainName" not in context and not event.get("headers")

def _can_manage(event, site):
    """Whether the caller may change this site or see its crew. Sites created by a signed-in
    supervisor belong to that supervisor's organisation (or to them alone); the shared key manages all."""
    if open_access(): return True
    caller = authenticate(event)
    if not caller: return False
    if caller["provider"] == "key" or not site or not site.owner_id: return True
    return site.owner_id in {caller["user_id"], caller["organization"]} - {""}

def _owner_for(event):
    caller = authenticate(event) or {}
    return (caller.get("organization") or caller.get("user_id")) if caller.get("provider") == "clerk" else None

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

def _link_secret():
    """Signs email check-in links. A dedicated secret when set, so rotating the mail password does not break links."""
    return secret("CHECKIN_LINK_SECRET", "CHECKIN_LINK_SECRET_PARAMETER") or _gmail_app_password()

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
    try:
        number = float(value)
        if kind is int:
            if number != int(number): raise ValueError("not a whole number")
            number = int(number)
    except (TypeError, ValueError, OverflowError) as e:
        raise BadRequest(f"{label} must be a {'whole ' if kind is int else ''}number") from e
    if isinstance(value, bool): raise BadRequest(f"{label} must be a number")
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
    recipients = _first(raw, "emailRecipients", "email_recipients")
    if recipients is not None and not isinstance(recipients, (str, list, tuple)): raise BadRequest("emailRecipients must be a list of addresses or a comma-separated string")
    site.email_recipients = parse_recipients(recipients)
    if len(site.email_recipients) > MAX_EMAIL_RECIPIENTS: raise BadRequest(f"emailRecipients may hold at most {MAX_EMAIL_RECIPIENTS} addresses")
    owner = _first(raw, "ownerId")
    if owner: site.owner_id = str(owner)[:200]
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
    if site.owner_id: record["ownerId"] = site.owner_id
    return record

def _site_dict(site, private=False):
    """The site as returned by the API. The Discord webhook URL and email recipients are only
    included for the supervisor who just wrote them; public reads get a flag and a count."""
    d = _site_record(site)
    d.pop("ownerId", None)
    webhook, recipients = d.pop("discordWebhookUrl", None), d.pop("emailRecipients", None)
    d["hasDiscordWebhook"] = bool(webhook)
    d["hasSlackWebhook"] = bool(secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_PARAMETER"))
    d["emailRecipientCount"] = len(recipients or [])
    if private:
        if webhook: d["discordWebhookUrl"] = webhook
        if recipients: d["emailRecipients"] = recipients
    # Empty when nothing is configured: a made-up bot or invite would send workers to someone else's channel.
    d["telegramBotUsername"] = site.telegram_bot_username or os.getenv("TELEGRAM_BOT_USERNAME") or ""
    d["discordInviteUrl"] = site.discord_invite_url or os.getenv("DISCORD_INVITE_URL") or ""
    return d

def _save_site(site):
    _save_record(f"SITE#{site.site_id}", "META", _site_record(site))
    SITES[site.site_id] = site

def _site_from_item(item):
    try: return _site(_from_dynamo(item))
    except BadRequest as e:
        logger.error("Stored site %s is invalid and was skipped: %s", item.get("pk"), e)
        return None

def _load_site(site_id):
    """With a table, the stored record wins over this container's copy, which another container may have replaced."""
    table = _table()
    if table:
        try:
            item = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": "META"}).get("Item")
            site = _site_from_item(item) if item else None
            if site: SITES[site_id] = site
            else: SITES.pop(site_id, None)
            return site
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load site %s from DynamoDB: %s", site_id, type(e).__name__)
    return SITES.get(site_id)

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
        workers, acks, alerts = {}, {}, {}
        # Plans are read one at a time by date; old plans and check-ins are never loaded.
        for prefix in ("WORKER#", "ALERT#", f"ACK#{_plan_id_today(site_id)}#"):
            for item in _query_site_items(table, site_id, prefix):
                sk = item.get("sk", "")
                record = {k: v for k, v in item.items() if k not in ("pk", "sk", "expiresAt")}
                if sk.startswith("WORKER#"): workers[item.get("chatId", sk[7:])] = record
                elif sk.startswith("ACK#"): acks[item.get("chatId", sk.split("#")[-1])] = record
                elif sk.startswith("ALERT#"): alerts[item.get("alertId", sk[6:])] = record
        WORKERS[site_id], ACKS[site_id], ALERTS[site_id] = workers, acks, alerts
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

def _plan_id_today(site_id):
    site = SITES.get(site_id)
    return f"{site_id}-{_today(site) if site else datetime.now(UTC).strftime('%Y-%m-%d')}"

def _real_date(text):
    try: datetime.strptime(text, "%Y-%m-%d"); return bool(DATE_RE.match(text))
    except (TypeError, ValueError): return False

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
    # A work hour with no temperature or humidity is a gap, not a cool hour: the plan is not built around it.
    day = forecast["hours"][0]["time"][:10] if forecast["hours"] else ""
    gaps = [t for t in forecast.get("missing") or [] if t[:10] == day and site.work_start_hour <= int(t[11:13]) <= site.work_end_hour]
    if gaps:
        raise WeatherUnavailable(f"The forecast is missing {len(gaps)} work hour(s), first {gaps[0]}")
    existing = _stored_plan(site.site_id, plan["date"])
    if existing and existing.get("status") != "DRAFT":
        raise Conflict(f"The plan for {plan['date']} is already {existing['status'].lower()} and cannot be replaced.")
    _save_plan(site.site_id, plan)
    PLANS[(site.site_id, plan["date"])] = plan
    return plan

def _save_plan(site_id, plan):
    _save_record(f"SITE#{site_id}", f"PLAN#{plan['date']}", {"plan": plan, "expiresAt": _expires(PLAN_TTL_DAYS)})

def _stored_plan(site_id, date):
    key = (site_id, date)
    table = _table()
    if table:
        try:
            saved = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": f"PLAN#{date}"}, ConsistentRead=True).get("Item")
            if saved: PLANS[key] = _from_dynamo(saved.get("plan", saved))
            else: PLANS.pop(key, None)
        except Exception as e:  # noqa: BLE001
            # Carrying on without the stored plan could replace an approved plan with a fresh draft.
            logger.error("Failed to load plan for %s on %s from DynamoDB: %s", site_id, date, type(e).__name__)
            raise StorageError("could not read the stored plan") from e
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
    except (WeatherUnavailable, Conflict):
        if plan: return plan
        raise

def _peak_index(plan):
    return peak_index(plan["hours"])

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

def _dashboard(site, private=False):
    """The site's day. Who joined, who checked in and who is unwell is only included for a supervisor of the site."""
    _hydrate_site_state(site.site_id)
    plan = _plan(site)
    hours = [_hour_view(h) for h in plan["hours"]]
    peak = hours[_peak_index(plan)]
    plan_view = {**_plan_view(plan), "peak": peak}
    source = "Demo data" if plan.get("weather_provider") == "DEMO" else "Open-Meteo"
    return {"site": _site_dict(site), "forecast": {"location": site.name, "hourly": hours, "source": source}, "current": hours[_current_index(site, plan)], "peak": peak, "level": peak["level"].lower(), "controls": _controls(plan), "plan": plan_view, "canonicalPlan": plan, **_crew_view(site.site_id, plan, private), "updated_at": datetime.now(UTC).isoformat()}

def _crew_view(site_id, plan, private):
    workers = list(WORKERS.get(site_id, {}).values())
    acks = [a for a in ACKS.get(site_id, {}).values() if a.get("planId") == plan["plan_id"]]
    alerts = list(ALERTS.get(site_id, {}).values())
    counts = {"workers": len(workers), "acknowledgements": len(acks), "openAlerts": sum(1 for a in alerts if a.get("status") == "OPEN")}
    if not private: workers, acks, alerts = [], [], []
    return {"workers": workers, "acknowledgements": acks, "alerts": alerts, "crewCounts": counts, "crewDetailsIncluded": private}


# ── Worker check-ins ───────────────────────────────────────────────────

def _site_for_plan(plan_id):
    site_id = next((key[0] for key in PLANS if PLANS[key].get("plan_id") == plan_id), None)
    if not site_id and plan_id.count("-") >= 3:
        site_id = plan_id.rsplit("-", 3)[0]
    return site_id if site_id and SITE_ID_RE.match(site_id) else "default"

def _known_site(site_id):
    if not SITE_ID_RE.match(str(site_id)): return None
    return _load_site(site_id) or (_demo_site() if site_id == "demo" else None)

def _notify_supervisor(site, alert):
    """Pushes an unwell report to the site's supervisor channels; returns whether any message went out."""
    at = datetime.now(_zone(site)).strftime("%H:%M")
    return _supervisor_message(site, f"HeatShield alert: a worker at {site.name} reported feeling unwell at {at}. Open the dashboard and check on them now.")

def _check_in(site_id, user_id, action, channel, name=None, plan_id=None):
    """Records an OK acknowledgement or a Not OK alert against today's plan.

    Returns ACKNOWLEDGED or OPEN; STALE for an OK on an earlier plan (it says nothing about today);
    None for an unknown site or action. A Not OK is recorded whichever plan the button came from.
    """
    site = _known_site(site_id)
    if not site or action not in ("ok", "unwell"): return None
    now = datetime.now(UTC)
    current = f"{site_id}-{_today(site)}"
    record = {"chatId": user_id, "channel": channel, "at": now.isoformat(), "planId": current}
    if name: record["name"] = str(name)[:80]
    if action == "ok":
        if plan_id and plan_id != current: return "STALE"
        ack = {**record, "status": "ACKNOWLEDGED"}
        _save_record(f"SITE#{site_id}", f"ACK#{current}#{user_id}", {**ack, "expiresAt": _expires(ACK_TTL_DAYS)})
        ACKS.setdefault(site_id, {})[user_id] = ack
        return "ACKNOWLEDGED"
    alert = {**record, "alertId": user_id, "status": "OPEN"}
    _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", alert)
    ALERTS.setdefault(site_id, {})[user_id] = alert
    alert["supervisorNotified"] = _notify_supervisor(site, alert)
    if alert["supervisorNotified"]: _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", alert)
    return "OPEN"

ALERT_ADVICE = "Move to a shaded rest area, drink cool water, and seek medical attention if symptoms persist."

def _check_in_reply(site_id, user_id, status):
    """What a worker is told after a check-in. It only says the supervisor was notified when a message really went out."""
    if status == "ACKNOWLEDGED": return "Plan acknowledged. Stay hydrated and follow your rest intervals."
    if status == "STALE": return "That button is from an earlier plan. Use the buttons on today's message."
    if status == "OPEN":
        notified = ALERTS.get(site_id, {}).get(user_id, {}).get("supervisorNotified")
        lead = "Your supervisor has been notified." if notified else "Your alert is on the supervisor's dashboard. Tell your supervisor or a co-worker directly now as well."
        return f"Alert recorded. {lead} {ALERT_ADVICE}"
    return "This could not be recorded. If you feel unwell, tell your supervisor directly now."

def _join(site_id, user_id, name, channel, language):
    worker = {"chatId": user_id, "name": str(name)[:80], "language": language, "status": "JOINED", "channel": channel, "joinedAt": datetime.now(UTC).isoformat()}
    _save_record(f"SITE#{site_id}", f"WORKER#{user_id}", worker)
    WORKERS.setdefault(site_id, {})[user_id] = worker

def _telegram_api(method, payload):
    token = secret("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN_PARAMETER")
    if not token: return False
    try:
        request = Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5): return True  # nosec B310
    except Exception as e:  # noqa: BLE001
        logger.warning("Telegram %s failed: %s", method, type(e).__name__)
        return False

def _send_telegram(chat_id, text, plan_id):
    return _telegram_api("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [[{"text": "I'm OK", "callback_data": f"ok:{plan_id}"}, {"text": "Not OK", "callback_data": f"unwell:{plan_id}"}]]}})


# ── Routing ────────────────────────────────────────────────────────────

def handler(event, context):
    try:
        return _route(event)
    except BadRequest as e:
        return _json(400, {"error": str(e)})
    except Conflict as e:
        return _json(409, {"error": str(e)})
    except StorageError as e:
        return _json(503, {"error": f"Nothing was recorded: {e}. Try again."})
    except WeatherUnavailable as e:
        logger.warning("Weather unavailable: %s", e)
        return _json(503, {"error": "The weather forecast is unavailable right now. No plan was created; try again shortly."})
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error")
        # The schedule has no caller to read a status code; a raised error is what makes it retry.
        if _is_scheduler(event): raise
        return _json(500, {"error": "internal error"})

def _route(event):
    path = event.get("rawPath", event.get("path", "/")) or "/"
    method = str(event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET"))).upper()
    protected = (
        method in {"POST", "PUT"} and (path.endswith(("/sites", "/plan", "/follow-ups/run", "/plans/generate", "/waterlogging/reports")) or "/plans/" in path or "/alerts/" in path or "/sites/" in path)
    ) or (method == "GET" and path.endswith("/sites"))
    if protected and not is_authorized(event) and not (path.endswith(("/plan", "/follow-ups/run")) and _is_scheduler(event)):
        return _json(401, {"error": "supervisor authorization required"})
    if path.endswith("/health"): return _json(200, {"ok": True, "service": "heatshield-api"})
    if path.endswith(("/auth/me", "/auth/profile")) and method == "GET":
        return _json(200, {
            "ok": True,
            "supervisor": get_caller_identity(event),
            "openAccess": open_access(),
            "clerkConfigured": bool(os.getenv("CLERK_JWT_KEY") or secret("CLERK_SECRET_KEY", "CLERK_SECRET_KEY_PARAMETER")),
        })
    if path.endswith("/dashboard") and method == "GET":
        site = _demo_site()
        return _json(200, _dashboard(site, _can_manage(event, site)))
    if path.endswith("/email/ack") and method in {"GET", "POST"}:
        return _email_ack(event, method)
    if "/waterlogging" in path and method == "GET":
        return _waterlogging_read(event, path)

    body = _body(event)
    if path.endswith("/plan") and method == "POST":
        return _stage_plans(event, body)
    if path.endswith("/follow-ups/run") and method == "POST":
        return _run_follow_ups(event)
    if path.endswith("/waterlogging/reports") and method == "POST":
        return _waterlogging_report(event, body)
    if path.endswith("/telegram/webhook") and method == "POST":
        if not _telegram_secret_valid(event): return _json(403, {"error": "invalid Telegram webhook secret"})
        return _telegram_update(body)
    if path.endswith(("/discord/webhook", "/discord/interactions")) and method == "POST":
        if not _discord_request_valid(event): return _json(401, {"error": "invalid request signature"})
        return _discord_interaction(body)
    if path.endswith("/sites") and method == "POST":
        if body.get("latitude") in (None, "") or body.get("longitude") in (None, ""):
            raise BadRequest("latitude and longitude are required: a plan is only as good as the place its forecast is for")
        site = _site({**body, "ownerId": _owner_for(event)})
        if _load_site(site.site_id): raise Conflict(f"A site with the id {site.site_id} already exists. Update it instead.")
        _save_site(site)
        return _json(201, {"site": _site_dict(site, private=True)})
    if path.endswith("/sites") and method == "GET":
        return _json(200, {"sites": [_site_dict(s) for s in _all_sites() if _can_manage(event, s)]})
    if "/sites/" in path:
        return _site_route(event, path, method, body)
    if "/alerts/" in path and method == "POST":
        alert_id = path.strip("/").split("/")[-1]
        hint = str(body.get("siteId") or "")
        if SITE_ID_RE.match(hint):
            _load_site(hint)
            _hydrate_site_state(hint)
            groups = [(hint, ALERTS.get(hint, {}))]
        else:
            groups = list(ALERTS.items())
        for site_id, group in groups:
            if alert_id in group:
                if not _can_manage(event, _load_site(site_id)): return _json(403, {"error": "this site belongs to another supervisor"})
                alert = group[alert_id]
                alert["status"] = "RESOLVED"
                alert["resolvedAt"] = datetime.now(UTC).isoformat()
                alert["resolvedBy"] = get_caller_identity(event).get("name") or "supervisor"
                _save_record(f"SITE#{site_id}", f"ALERT#{alert_id}", alert)
                return _json(200, {"alert": alert})
        return _json(404, {"error": "alert not found"})
    return _json(404, {"error": "route not found"})

def _stage_plans(event, body):
    """Stages today's draft for one site or, for the morning schedule, for every site."""
    requested = body.get("siteId")
    if requested:
        site = _known_site(requested)
        if not site: return _json(404, {"error": "site not found"})
        sites = [site]
    else:
        sites = _all_sites() or [_demo_site()]
    if not _is_scheduler(event): sites = [s for s in sites if _can_manage(event, s)]

    def stage(site):
        try: return _plan(site), None
        except (WeatherUnavailable, StorageError) as e:
            logger.warning("No plan staged for %s: %s", site.site_id, e)
            return None, site.site_id
    # One forecast call per site; in parallel so a long site list still fits inside the function timeout.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(stage, sites))
    plans = [plan for plan, _ in results if plan]
    failed = [site_id for _, site_id in results if site_id]
    if failed and not plans:
        # Raised, not returned, on the schedule so the failed run is retried and shows up as an error.
        if _is_scheduler(event): raise RuntimeError(f"no plan could be staged: {', '.join(failed)}")
        raise WeatherUnavailable(f"no forecast for {', '.join(failed)}")
    return _json(200, {"scheduled": bool(body.get("scheduled")), "plans": plans, "failedSites": failed, "message": "Draft plans staged; supervisor approval is required before sending."})

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
        # A cache: failing to keep it costs a re-download, never a wrong answer.
        try: _save_record(pk, sk, item)
        except StorageError: pass

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
            kwargs = {"KeyConditionExpression": Key("pk").eq("WLREPORT") & Key("sk").gte(cutoff)}
            while True:
                page = table.query(**kwargs)
                for item in page.get("Items", []):
                    report = {k: v for k, v in _from_dynamo(item).items() if k not in ("pk", "sk", "expiresAt")}
                    WATERLOGGING_REPORTS[report["id"]] = report
                if not page.get("LastEvaluatedKey"): break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
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
    _save_record("WLREPORT", f"{now}#{report['id']}", {**report, "expiresAt": _expires(REPORT_TTL_DAYS)})
    WATERLOGGING_REPORTS[report["id"]] = report
    return _json(201, {"report": report})

def _telegram_update(body):
    message = body.get("message") if isinstance(body.get("message"), dict) else {}
    text = str(message.get("text") or "")
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if text.startswith("/start") and chat_id:
        parts = text.split(maxsplit=1)
        site = _known_site(parts[1].strip()) if len(parts) > 1 else None
        if not site:
            _telegram_api("sendMessage", {"chat_id": chat_id, "text": "This join link is not valid. Ask your supervisor for your site's join link or QR code."})
            return _json(200, {"ok": True, "joined": None})
        _join(site.site_id, chat_id, str((message.get("from") or {}).get("first_name") or "Worker"), "telegram", site.default_language)
        _telegram_api("sendMessage", {"chat_id": chat_id, "text": f"You have joined {site.name}. You will get the heat safety plan here once your supervisor approves it."})
        return _json(200, {"ok": True, "joined": site.site_id})
    callback = body.get("callback_query") if isinstance(body.get("callback_query"), dict) else {}
    data = str(callback.get("data") or "")
    if ":" in data:
        action, plan_id = data.split(":", 1)
        site_id, user_id = _site_for_plan(plan_id), str((callback.get("from") or {}).get("id", "unknown"))
        status = _check_in(site_id, user_id, action, "telegram", (callback.get("from") or {}).get("first_name"), plan_id)
        if callback.get("id"):
            _telegram_api("answerCallbackQuery", {"callback_query_id": callback["id"], "text": _check_in_reply(site_id, user_id, status)[:200], "show_alert": status != "ACKNOWLEDGED"})
    return _json(200, {"ok": True})

def _discord_interaction(body):
    if body.get("type") == 1:
        return _json(200, {"type": 1})
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    user = (body.get("member") or {}).get("user") or body.get("user") or {}
    user_id = str(user.get("id") or body.get("userId") or body.get("user_id") or "discord_user")
    username = user.get("username") or user.get("global_name") or body.get("name") or f"Worker_{user_id[-4:]}"

    def reply(text, **extra):
        return _json(200, {"type": 4, "data": {"content": text, "flags": 64}, **extra})

    custom_id = str(data.get("custom_id") or body.get("custom_id") or "")
    if ":" in custom_id:
        action, plan_id = custom_id.split(":", 1)
        site_id = _site_for_plan(plan_id)
        status = _check_in(site_id, user_id, action, "discord", username, plan_id)
        prefix = {"ACKNOWLEDGED": "✅ ", "OPEN": "🚨 "}.get(status, "")
        return reply(prefix + _check_in_reply(site_id, user_id, status))
    content = str(body.get("content") or "")
    if data.get("name") in ("join", "start") or body.get("action") == "join" or content.startswith("/join"):
        options = data.get("options")
        candidate = ""
        if options and isinstance(options, list) and isinstance(options[0], dict):
            candidate = str(options[0].get("value", ""))
        elif body.get("siteId"):
            candidate = str(body.get("siteId"))
        elif len(content.split()) > 1:
            candidate = content.split()[1]
        site = _known_site(candidate)
        if not site:
            return reply("That site code is not valid. Ask your supervisor for your site's join code.", ok=False, joined=None)
        _join(site.site_id, user_id, username, "discord", site.default_language)
        return reply(f"👷 **Joined {site.name} via Discord.** You will receive daily heat safety briefings.", ok=True, joined=site.site_id)
    plan_id = str(body.get("plan_id") or body.get("planId") or "")
    if body.get("action") and plan_id:
        status = _check_in(_site_for_plan(plan_id), user_id, str(body.get("action")), "discord", username, plan_id)
        if status: return _json(200, {"ok": True, "status": status})
    return _json(200, {"ok": True})

def _email_ack(event, method):
    query = event.get("queryStringParameters") or {}
    action, site_id, plan_id, email = query.get("a", ""), query.get("site", ""), query.get("plan", ""), str(query.get("e", "")).lower()
    if action not in {"ok", "unwell"} or not verify_action(_link_secret(), action, site_id, plan_id, email, query.get("s", "")):
        return _html(403, "HeatShield", "<p>This check-in link is not valid.</p>")
    if method == "GET":
        # Mail scanners prefetch links, so the GET only shows a confirm button; the POST records the check-in.
        label = "I'm OK" if action == "ok" else "Not OK"
        color = "#22A474" if action == "ok" else "#D32F2F"
        return _html(200, "HeatShield check-in", f'<form method="post"><button type="submit" style="padding:14px 22px;border:0;border-radius:8px;background:{color};color:#fff;font-size:18px;font-weight:700">{label}</button></form>')
    status = _check_in(site_id, email, action, "email", email.split("@", 1)[0], plan_id)
    titles = {"ACKNOWLEDGED": (200, "Plan acknowledged"), "OPEN": (200, "Alert recorded"), "STALE": (410, "This link is from an earlier plan")}
    code, title = titles.get(status, (404, "Check-in not recorded"))
    return _html(code, title, f"<p>{escape(_check_in_reply(site_id, email, status))}</p>")

def _site_route(event, path, method, body):
    parts = path.strip("/").split("/")
    index = parts.index("sites")
    site_id = parts[index + 1] if len(parts) > index + 1 else ""
    rest = parts[index + 2:]
    if not SITE_ID_RE.match(site_id): return _json(404, {"error": "site not found"})
    site = _load_site(site_id)
    manages = _can_manage(event, site)
    if method != "GET" and not manages: return _json(403, {"error": "this site belongs to another supervisor"})

    if method == "PUT" and not rest:
        before = _site_record(site) if site else {}
        changes = {SITE_FIELD_ALIASES.get(key, key): value for key, value in body.items()}
        owner = before.get("ownerId") or _owner_for(event)
        updated = _site({**before, **changes, "siteId": site_id, "ownerId": owner})
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
        return _json(200, _dashboard(site, manages))
    if rest == ["join"] and method == "GET":
        site_dict = _site_dict(site)
        tg_url = f"https://t.me/{site_dict['telegramBotUsername']}?start={site_id}" if site_dict["telegramBotUsername"] else None
        discord_invite = site_dict["discordInviteUrl"]
        dc_url = (f"{discord_invite}?site={site_id}" if "?" not in discord_invite else f"{discord_invite}&site={site_id}") if discord_invite else None
        return _json(200, {"siteId": site_id, "joinCode": site_id, "joinUrl": tg_url, "telegramJoinUrl": tg_url, "discordJoinUrl": dc_url})
    if rest == ["plans", "generate"] and method == "POST":
        replaying = body.get("mode") == "REPLAY"
        replay_date = str(body.get("replayDate") or "") if replaying else None
        if replaying and not _real_date(replay_date): raise BadRequest("replayDate must be a real date written as YYYY-MM-DD")
        plan = _generate(site, "REPLAY" if replaying else "LIVE", replay_date)
        return _json(200, {"plan": plan, "planView": _plan_view(plan), "controls": _controls(plan)})
    if rest[:1] == ["plans"]:
        action = rest[-1] if len(rest) > 1 and rest[-1] in ("approve", "send") else None
        named = rest[1:-1] if action else rest[1:]
        if len(named) > 1 or (named and not _real_date(named[0])): raise BadRequest("the plan date must be a real date written as YYYY-MM-DD")
        date = named[0] if named else _today(site)
        plan = _stored_plan(site_id, date)
        if not plan and date == _today(site): plan = _plan(site)
        if not plan: return _json(404, {"error": f"no plan for {date}"})
        if action == "approve" and method == "POST":
            if plan["status"] == "SENT": raise Conflict("This plan has already been sent to workers.")
            caller = get_caller_identity(event)
            # A verified identity always wins; a name typed by the client is only trusted in open-access mode.
            approver = caller.get("name") if caller.get("authenticated") else (body.get("approvedBy") or caller.get("name"))
            plan["status"] = "APPROVED"
            plan["approvedBy"] = str(approver or "supervisor")[:120]
            plan["approvedAt"] = datetime.now(UTC).isoformat()
            _save_plan(site_id, plan)
            return _json(200, {"plan": plan})
        if action == "send" and method == "POST":
            if plan.get("mode") == "REPLAY": return _json(409, {"error": "A plan built from past weather cannot be sent to workers. Create a plan from today's weather first."})
            if plan["status"] not in ("APPROVED", "SENT"): return _json(409, {"error": "Plan must be approved before sending to workers"})
            if date != _today(site): return _json(409, {"error": f"The plan for {date} is not today's plan and cannot be sent. Approve and send today's plan."})
            result = _send_plan(event, site, plan, date, body)
            return _json(200 if result["delivered"] else 409, result)
        return _json(200, {"plan": plan})
    return _json(404, {"error": "route not found"})

def _delivery_record(site_id, date):
    """Who has already received this plan, so a retry only goes to the people and channels that were missed.
    Kept apart from the plan because the plan is public and this holds addresses."""
    key = (site_id, date)
    table = _table()
    if table:
        try:
            item = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": f"DELIVERY#{date}"}, ConsistentRead=True).get("Item")
            if item: DELIVERIES[key] = {k: v for k, v in _from_dynamo(item).items() if k not in ("pk", "sk", "expiresAt")}
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load delivery record for %s on %s: %s", site_id, date, type(e).__name__)
    return DELIVERIES.setdefault(key, {"telegram": [], "email": [], "discord": False, "slack": False})

def _save_delivery(site_id, date, record):
    _save_record(f"SITE#{site_id}", f"DELIVERY#{date}", {**record, "expiresAt": _expires(PLAN_TTL_DAYS)})

def _send_plan(event, site, plan, date, body):
    """Sends the plan to everyone who has not had it yet. Returns what went out now, what failed, and the totals."""
    site_id = site.site_id
    done = _delivery_record(site_id, date)
    workers = list(WORKERS.get(site_id, {}).values())
    demo = plan.get("weather_provider") == "DEMO"

    tg_pending = [w for w in workers if w.get("channel") not in ("discord", "email") and w["chatId"] not in done["telegram"]]
    def deliver(worker):
        return _send_telegram(worker["chatId"], safe_bedrock_message(plan, site.name, worker.get("language") or site.default_language), plan["plan_id"])
    # Sent in parallel so a large crew still fits inside the 30-second API limit.
    with ThreadPoolExecutor(max_workers=8) as pool:
        tg_results = list(pool.map(deliver, tg_pending))
    tg_sent = [w["chatId"] for w, ok in zip(tg_pending, tg_results) if ok]
    done["telegram"] += tg_sent

    discord_now = slack_now = False
    discord_url = site.discord_webhook_url or secret("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_PARAMETER")
    if discord_url and not done["discord"]:
        discord_now = done["discord"] = bool(send_discord_webhook(discord_url, plan, site.name, site.default_language))
    slack_url = secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_PARAMETER")
    if slack_url and not done["slack"]:
        slack_now = done["slack"] = bool(send_slack_webhook(slack_url, plan, site.name, site.default_language))

    recipients = site.email_recipients or parse_recipients(os.getenv("GMAIL_RECIPIENTS"))
    email_pending = [r for r in recipients if r not in done["email"]]
    custom_email_msg = str(body.get("customEmailMessage") or "")[:4000] or None
    if custom_email_msg and demo: custom_email_msg = f"{DEMO_NOTICE}\n\n{custom_email_msg}"
    email_sent = send_gmail_briefing(os.getenv("GMAIL_ADDRESS"), _gmail_app_password(), email_pending, plan, site.name, site.default_language, _public_base_url(event), custom_email_msg, _link_secret())
    done["email"] += email_sent

    discord_workers = sum(1 for w in workers if w.get("channel") == "discord")
    channels = {"telegram": len(tg_sent), "discord": (max(discord_workers, 1) if discord_now else 0), "slack": int(slack_now), "email": len(email_sent)}
    failed = {
        "telegram": len(tg_pending) - len(tg_sent),
        "discord": int(bool(discord_url) and not done["discord"]),
        "slack": int(bool(slack_url) and not done["slack"]),
        "email": len(email_pending) - len(email_sent),
    }
    delivered = sum(channels.values())
    reached = bool(done["telegram"] or done["email"] or done["discord"] or done["slack"])
    if delivered:
        done.setdefault("sentAt", datetime.now(UTC).isoformat())
        _save_delivery(site_id, date, done)
        # A plan that reached nobody stays APPROVED so it is never shown as sent.
        if plan["status"] != "SENT":
            plan["status"] = "SENT"
            _save_plan(site_id, plan)
    result = {"plan": plan, "delivered": delivered, "channels": channels, "failed": failed, "retryAvailable": any(failed.values())}
    if not delivered:
        if reached and not any(failed.values()): result["error"] = "This plan has already been sent to everyone on this site."
        elif reached: result["error"] = "The retry reached nobody new. The channels that failed are still failing; check their settings and try again."
        else: result["error"] = "The plan was not delivered to anyone: no worker has joined yet, or no message channel is working. It is still approved; try again once a channel is set up."
    return result


# ── Follow-ups: the recurring check ────────────────────────────────────

def _supervisor_message(site, text):
    """Posts to the channels a supervisor watches; returns whether any accepted it."""
    sent = False
    slack_url = secret("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_PARAMETER")
    if slack_url: sent = send_slack_text(slack_url, text) or sent
    discord_url = site.discord_webhook_url or secret("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_PARAMETER")
    if discord_url: sent = send_discord_text(discord_url, text) or sent
    return sent

def _escalate_alert(site, alert, stage, now):
    """An alert nobody has handled: tell the supervisor again, tell the worker what to do, and ask co-workers to go to them."""
    site_id, user_id = site.site_id, alert["chatId"]
    minutes = int(followup.minutes_since(alert.get("at"), now) or 0)
    workers = WORKERS.get(site_id, {})
    alert["supervisorNotified"] = _supervisor_message(site, followup.supervisor_escalation_text(site.name, minutes, stage)) or bool(alert.get("supervisorNotified"))
    if alert.get("channel") == "telegram" and not alert.get("firstAidSent"):
        language = workers.get(user_id, {}).get("language") or site.default_language
        alert["firstAidSent"] = _telegram_api("sendMessage", {"chat_id": user_id, "text": followup.text(followup.FIRST_AID, language)})
    asked = set(alert.get("buddiesAsked") or [])
    acknowledged = {a["chatId"] for a in ACKS.get(site_id, {}).values()}
    for buddy in followup.pick_buddies([w for w in workers.values() if w["chatId"] not in asked], acknowledged, user_id, followup.config()["max_buddies"] - len(asked)):
        language = buddy.get("language") or site.default_language
        name = alert.get("name") or workers.get(user_id, {}).get("name") or followup.text(followup.UNNAMED, language)
        if _telegram_api("sendMessage", {"chat_id": buddy["chatId"], "text": followup.text(followup.BUDDY, language, name=name, site=site.name, minutes=minutes)}):
            asked.add(buddy["chatId"])
    alert.update({"escalations": stage, "lastEscalatedAt": now.isoformat(), "buddiesAsked": sorted(asked)})
    _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", alert)

def _chase_check_ins(site, plan, now):
    """After a plan is sent: remind each worker who has not checked in, once, then tell the supervisor how many are missing, once."""
    site_id, cfg = site.site_id, followup.config()
    record = _delivery_record(site_id, plan["date"])
    age = followup.minutes_since(record.get("sentAt"), now)
    # Nothing is chased once the workday is over: a late reminder helps nobody.
    if age is None or datetime.now(_zone(site)).hour > site.work_end_hour: return 0, False
    acknowledged = {a["chatId"] for a in ACKS.get(site_id, {}).values() if a.get("planId") == plan["plan_id"]}
    acknowledged |= {a["chatId"] for a in ALERTS.get(site_id, {}).values() if a.get("planId") == plan["plan_id"]}
    workers, reminded, changed = WORKERS.get(site_id, {}), 0, False
    if age >= cfg["remind_after_minutes"]:
        already = set(record.get("reminded") or [])
        for chat_id in followup.unacknowledged(record["telegram"], acknowledged | already):
            language = workers.get(chat_id, {}).get("language") or site.default_language
            reminder = followup.text(followup.REMINDER, language, site=site.name)
            if plan.get("weather_provider") == "DEMO": reminder = f"{DEMO_NOTICE}\n\n{reminder}"
            if _send_telegram(chat_id, reminder, plan["plan_id"]):
                already.add(chat_id); reminded += 1
        if reminded: record["reminded"], changed = sorted(already), True
    summarised = False
    if age >= cfg["summary_after_minutes"] and not record.get("summarySent"):
        delivered = record["telegram"] + record["email"]
        missing = followup.unacknowledged(delivered, acknowledged)
        # A full crew needs no message; the dashboard already shows it.
        if missing: _supervisor_message(site, followup.supervisor_summary_text(site.name, len(missing), len(delivered), len(record.get("reminded") or [])))
        record["summarySent"], changed, summarised = True, True, bool(missing)
    if changed: _save_delivery(site_id, plan["date"], record)
    return reminded, summarised

def _follow_up_site(site, now):
    result = {"siteId": site.site_id, "escalated": 0, "reminded": 0, "summarySent": False}
    try:
        _hydrate_site_state(site.site_id)
        for alert in list(ALERTS.get(site.site_id, {}).values()):
            stage = followup.due_stage(alert, now)
            if stage:
                _escalate_alert(site, alert, stage, now)
                result["escalated"] += 1
        plan = _stored_plan(site.site_id, _today(site))
        if plan and plan.get("status") == "SENT":
            result["reminded"], result["summarySent"] = _chase_check_ins(site, plan, now)
    except Exception as e:  # noqa: BLE001
        # One site's failure must not stop the check for the others.
        logger.exception("Follow-up failed for site %s", site.site_id)
        result["error"] = type(e).__name__
    return result

def _run_follow_ups(event):
    """The recurring check, called by the schedule every few minutes: escalates alerts nobody
    has handled and chases missing check-ins. It never sends a plan or approves anything."""
    sites = _all_sites()
    if not _is_scheduler(event): sites = [s for s in sites if _can_manage(event, s)]
    now = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda site: _follow_up_site(site, now), sites))
    acted = [r for r in results if r["escalated"] or r["reminded"] or r["summarySent"] or r.get("error")]
    return _json(200, {"checkedSites": len(sites), "escalated": sum(r["escalated"] for r in results), "reminded": sum(r["reminded"] for r in results), "sites": acted})


if __name__ == "__main__":
    from server import run
    run(int(os.environ.get("PORT", "3000")))
