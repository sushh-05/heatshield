"""HeatShield API: site setup, weather-based plans, approval, Telegram, Discord, Gmail and worker check-ins."""
import json
import logging
import os
import uuid
from html import escape
from datetime import UTC, datetime
from urllib.request import Request, urlopen

logger = logging.getLogger("heatshield")

try:
    import boto3
except ImportError:
    boto3 = None

from core.auth import get_caller_identity, is_authorized
from core.discord import send_discord_webhook
from core.forecast import get_weather
from core.gmail import parse_recipients, send_gmail_briefing, verify_action
from core.messages import safe_bedrock_message
from core.models import SiteProfile
from core.planner import generate_plan

SITES, PLANS, WORKERS, ACKS, ALERTS = {}, {}, {}, {}, {}

def _table():
    if not boto3 or not os.getenv("TABLE_NAME"): return None
    try: return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    except Exception as e:  # noqa: BLE001
        logger.debug("DynamoDB initialization bypassed: %s", e)
        return None

def _secret(env_name, parameter_env_name):
    value = os.getenv(env_name)
    if value: return value
    parameter_name = os.getenv(parameter_env_name)
    if boto3 and parameter_name:
        try: return boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=True)["Parameter"]["Value"]
        except Exception as e:  # noqa: BLE001
            logger.warning("SSM get_parameter failed for %s: %s", parameter_name, e)
    return None

def _body(event):
    raw = event.get("body") or "{}"
    return json.loads(raw) if isinstance(raw, str) else raw

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
    expected = _secret("TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET_PARAMETER")
    if not expected: return True
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get("x-telegram-bot-api-secret-token") == expected

def _discord_secret_valid(event):
    expected = _secret("DISCORD_WEBHOOK_SECRET", "DISCORD_WEBHOOK_SECRET_PARAMETER")
    if not expected: return True
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get("x-discord-secret-token") == expected or headers.get("x-signature-ed25519") == expected

def _gmail_app_password():
    value = _secret("GMAIL_APP_PASSWORD", "GMAIL_APP_PASSWORD_PARAMETER")
    return value.replace(" ", "") if value else None

def _public_base_url(event):
    configured = os.getenv("PUBLIC_API_URL")
    if configured: return configured.rstrip("/")
    domain = event.get("requestContext", {}).get("domainName")
    if domain: return f"https://{domain}"
    host = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}.get("host")
    return f"http://{host}" if host else None

def _site(raw):
    s = SiteProfile(raw.get("siteId", uuid.uuid4().hex[:8]), raw.get("name", "Hyderabad Construction Site"), float(raw.get("latitude", 17.385)), float(raw.get("longitude", 78.487)), raw.get("timezone", "Asia/Kolkata"), int(raw.get("workStartHour", 6)), int(raw.get("workEndHour", 18)), raw.get("workload", "MODERATE"), raw.get("acclimatization", "ACCLIMATIZED"), raw.get("clothing", "NORMAL"), raw.get("defaultLanguage", "en"))
    webhook = raw.get("discordWebhookUrl") or raw.get("discord_webhook_url")
    if webhook:
        s.discord_webhook_url = webhook
    recipients = parse_recipients(raw.get("emailRecipients") or raw.get("email_recipients"))
    if recipients:
        s.email_recipients = recipients
    telegram_bot = (raw.get("telegramBotUsername") or raw.get("telegram_bot_username") or "").strip().lstrip("@")
    if telegram_bot:
        s.telegram_bot_username = telegram_bot
    discord_invite = (raw.get("discordInviteUrl") or raw.get("discord_invite_url") or "").strip()
    if discord_invite:
        s.discord_invite_url = discord_invite
    return s

def _save_site(site):
    SITES[site.site_id] = site
    if boto3 and os.getenv("TABLE_NAME"):
        try:
            boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]).put_item(Item={"pk": f"SITE#{site.site_id}", "sk": "META", **site.__dict__})
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save site %s to DynamoDB: %s", site.site_id, e)

def _load_site(site_id):
    if site_id in SITES: return SITES[site_id]
    table = _table()
    if table:
        try:
            item = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": "META"}).get("Item")
            if item: site = _site(item); SITES[site_id] = site; return site
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load site %s from DynamoDB: %s", site_id, e)
    return None

def _active_sites():
    if SITES: return list(SITES.values())
    table = _table()
    if table:
        try:
            items = table.scan(FilterExpression="sk = :meta", ExpressionAttributeValues={":meta": "META"}).get("Items", [])
            for item in items: _save_site(_site(item))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to scan active sites from DynamoDB: %s", e)
    return list(SITES.values()) or [_site({"siteId": "demo"})]

def _save_record(pk, sk, item):
    table = _table()
    if table:
        try:
            table.put_item(Item={"pk": pk, "sk": sk, **item})
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save record %s / %s to DynamoDB: %s", pk, sk, e)

def _hydrate_site_state(site_id):
    table = _table()
    if not table: return
    try:
        from boto3.dynamodb.conditions import Key
        items = table.query(KeyConditionExpression=Key("pk").eq(f"SITE#{site_id}")).get("Items", [])
        for item in items:
            sk = item.get("sk", "")
            if sk.startswith("WORKER#"): WORKERS.setdefault(site_id, {})[item.get("chatId", sk[7:])] = {k:v for k,v in item.items() if k not in ("pk", "sk")}
            elif sk.startswith("ACK#"): ACKS.setdefault(site_id, {})[item.get("chatId", sk.split("#")[-1])] = {k:v for k,v in item.items() if k not in ("pk", "sk")}
            elif sk.startswith("ALERT#"): ALERTS.setdefault(site_id, {})[item.get("alertId", sk[6:])] = {k:v for k,v in item.items() if k not in ("pk", "sk")}
            elif sk.startswith("PLAN#"): PLANS[(site_id, sk[5:])] = item.get("plan", item)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to hydrate state for site %s from DynamoDB: %s", site_id, e)

def _site_dict(site):
    d = {"siteId": site.site_id, "name": site.name, "latitude": site.latitude, "longitude": site.longitude, "timezone": site.timezone, "workStartHour": site.work_start_hour, "workEndHour": site.work_end_hour, "workload": site.workload, "acclimatization": site.acclimatization, "clothing": site.clothing, "defaultLanguage": site.default_language}
    if hasattr(site, "discord_webhook_url") and site.discord_webhook_url:
        d["discordWebhookUrl"] = site.discord_webhook_url
    if getattr(site, "email_recipients", None):
        d["emailRecipients"] = site.email_recipients
    d["telegramBotUsername"] = getattr(site, "telegram_bot_username", None) or os.getenv("TELEGRAM_BOT_USERNAME", "HeatShieldBot")
    d["discordInviteUrl"] = getattr(site, "discord_invite_url", None) or os.getenv("DISCORD_INVITE_URL", "https://discord.gg/heatshield")
    return d

def _invalidate_plans(site_id):
    """Drop cached plans for a site so the next fetch regenerates them —
    needed whenever a site's location (or any risk input) changes, since a
    cached plan was computed from weather at the *old* coordinates."""
    for key in [k for k in PLANS if k[0] == site_id]:
        del PLANS[key]
    table = _table()
    if table:
        try:
            from boto3.dynamodb.conditions import Key
            items = table.query(KeyConditionExpression=Key("pk").eq(f"SITE#{site_id}")).get("Items", [])
            for item in items:
                if item.get("sk", "").startswith("PLAN#"):
                    table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to invalidate cached plans for site %s: %s", site_id, e)

def _plan(site, mode="LIVE", replay_date=None):
    date = replay_date or datetime.now().strftime("%Y-%m-%d")
    key = (site.site_id, date)
    if key not in PLANS:
        table = _table()
        if table:
            try:
                saved = table.get_item(Key={"pk": f"SITE#{site.site_id}", "sk": f"PLAN#{date}"}).get("Item")
                if saved: PLANS[key] = saved.get("plan", saved)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to load plan for %s on %s from DynamoDB: %s", site.site_id, date, e)
    if key not in PLANS:
        PLANS[key] = generate_plan(site, get_weather(site, mode, replay_date), mode, replay_date).to_dict(); _save_record(f"SITE#{site.site_id}", f"PLAN#{date}", {"plan": PLANS[key]})
    return PLANS[key]

def _dashboard(site):
    _hydrate_site_state(site.site_id)
    plan = _plan(site)
    hours = [{"time": h["time"], "temperature": h["temperature_c"], "humidity": h["relative_humidity_pct"], "windSpeed": h.get("wind_speed_kmh"), "radiation": h.get("direct_normal_irradiance_wm2"), "risk": h["forecast_heat_risk"], "level": h["level"]} for h in plan["hours"]]
    peak = max(hours, key=lambda h: h["risk"]) if hours else {"time": "--", "temperature": 0, "humidity": 0, "risk": 0, "level": "LOW"}
    plan_view = {"title": "HeatShield work plan", "level": peak["level"].lower(), "peak": peak, "message": f"Hottest at {peak['time']} ({peak['risk']}/100). Plan your day carefully.", "checklist": [f"Heavy work only in the safe hours — follow the work blocks below.", f"Rest breaks: {plan['blocks'][0]['rest_minutes_per_hour'] if plan['blocks'] else 0} min per hour.", f"Water breaks: every {plan['blocks'][0]['water_every_minutes'] if plan['blocks'] else 30} min."], "generated_by": "weather plan"}
    return {"site": _site_dict(site), "forecast": {"location": site.name, "hourly": hours, "source": "Open-Meteo"}, "current": hours[0] if hours else peak, "peak": peak, "level": peak["level"].lower(), "plan": plan_view, "canonicalPlan": plan, "workers": list(WORKERS.get(site.site_id, {}).values()), "acknowledgements": list(ACKS.get(site.site_id, {}).values()), "alerts": list(ALERTS.get(site.site_id, {}).values()), "updated_at": datetime.now(UTC).isoformat()}

def _send_telegram(chat_id, text, plan_id):
    token = _secret("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN_PARAMETER")
    if not token: return False
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [[{"text": "I'm OK", "callback_data": f"ok:{plan_id}"}, {"text": "Not OK", "callback_data": f"unwell:{plan_id}"}]]}}
    try:
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5): return True  # nosec B310
    except Exception as e:  # noqa: BLE001
        logger.warning("Telegram sendMessage failed for chat %s: %s", chat_id, e)
        return False

def handler(event, context):
    path = event.get("rawPath", event.get("path", "/")); method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")); body = _body(event)
    protected = method in {"POST", "PUT", "DELETE"} and (path.endswith(("/sites", "/plans/generate")) or "/plans/" in path or "/alerts/" in path or ("/sites/" in path and method == "PUT"))
    if protected and not _authorized(event): return _json(401, {"error": "supervisor authorization required"})
    if path.endswith("/health"): return _json(200, {"ok": True, "service": "heatshield-api"})
    if path.endswith(("/auth/me", "/auth/profile")) and method == "GET":
        identity = get_caller_identity(event)
        return _json(200, {
            "ok": True,
            "supervisor": identity,
            "clerkConfigured": bool(os.getenv("CLERK_PUBLISHABLE_KEY") or os.getenv("CLERK_SECRET_KEY")),
        })
    if path.endswith("/dashboard") and method == "GET":
        site = _load_site("demo") or _site({"siteId": "demo"}); _save_site(site); return _json(200, _dashboard(site))
    if path.endswith("/plan") and method == "POST":
        requested_site = body.get("siteId")
        sites = [_load_site(requested_site)] if requested_site else _active_sites()
        sites = [site for site in sites if site]
        plans = []
        for site in sites:
            plan = _plan(site, "LIVE"); plans.append(plan)
        return _json(200, {"scheduled": bool(body.get("scheduled")), "plans": plans, "message": "Draft plans staged; supervisor approval is required before sending."})
    if path.endswith("/telegram/webhook") and method == "POST":
        if not _telegram_secret_valid(event): return _json(403, {"error": "invalid Telegram webhook secret"})
        message = body.get("message", {}); text = message.get("text", ""); chat_id = str(message.get("chat", {}).get("id", ""))
        if text.startswith("/start") and chat_id:
            site_id = text.split(maxsplit=1)[1] if len(text.split()) > 1 else "default"
            WORKERS.setdefault(site_id, {})[chat_id] = {"chatId": chat_id, "name": message.get("from", {}).get("first_name", "Worker"), "language": "te", "status": "JOINED", "channel": "telegram", "joinedAt": datetime.now(UTC).isoformat()}; _save_record(f"SITE#{site_id}", f"WORKER#{chat_id}", WORKERS[site_id][chat_id])
            return _json(200, {"ok": True, "joined": site_id})
        callback = body.get("callback_query", {}); data = callback.get("data", "")
        if data:
            action, plan_id = data.split(":", 1); chat_id = str(callback.get("from", {}).get("id", "unknown"))
            site_id = next((key[0] for key in PLANS if PLANS[key].get("plan_id") == plan_id), None)
            if not site_id and "-" in plan_id:
                site_id = plan_id.rsplit("-", 3)[0]
            site_id = site_id or "default"
            if action == "ok": ACKS.setdefault(site_id, {})[chat_id] = {"chatId": chat_id, "status": "ACKNOWLEDGED", "channel": "telegram", "at": datetime.now(UTC).isoformat()}; _save_record(f"SITE#{site_id}", f"ACK#{datetime.now().date()}#{chat_id}", ACKS[site_id][chat_id])
            if action == "unwell": ALERTS.setdefault(site_id, {})[chat_id] = {"alertId": chat_id, "chatId": chat_id, "status": "OPEN", "channel": "telegram", "at": datetime.now(UTC).isoformat()}; _save_record(f"SITE#{site_id}", f"ALERT#{chat_id}", ALERTS[site_id][chat_id])
        return _json(200, {"ok": True})
    if path.endswith(("/discord/webhook", "/discord/interactions")) and method == "POST":
        if not _discord_secret_valid(event): return _json(403, {"error": "invalid Discord webhook secret"})
        if body.get("type") == 1:
            return _json(200, {"type": 1})
        user = body.get("member", {}).get("user", {}) or body.get("user", {})
        user_id = str(user.get("id") or body.get("userId") or body.get("user_id") or "discord_user")
        username = user.get("username") or user.get("global_name") or body.get("name") or f"Worker_{user_id[-4:]}"
        custom_id = body.get("data", {}).get("custom_id", "") or body.get("custom_id", "")
        if ":" in custom_id:
            action, plan_id = custom_id.split(":", 1)
            site_id = next((key[0] for key in PLANS if PLANS[key].get("plan_id") == plan_id), None)
            if not site_id and "-" in plan_id:
                site_id = plan_id.rsplit("-", 3)[0]
            site_id = site_id or "default"
            if action == "ok":
                ACKS.setdefault(site_id, {})[user_id] = {"chatId": user_id, "name": username, "channel": "discord", "status": "ACKNOWLEDGED", "at": datetime.now(UTC).isoformat()}
                _save_record(f"SITE#{site_id}", f"ACK#{datetime.now().date()}#{user_id}", ACKS[site_id][user_id])
                return _json(200, {"type": 4, "data": {"content": "✅ **HeatShield Plan Acknowledged!** Stay hydrated and follow your rest intervals.", "flags": 64}})
            elif action == "unwell":
                ALERTS.setdefault(site_id, {})[user_id] = {"alertId": user_id, "chatId": user_id, "name": username, "channel": "discord", "status": "OPEN", "at": datetime.now(UTC).isoformat()}
                _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", ALERTS[site_id][user_id])
                return _json(200, {"type": 4, "data": {"content": "🚨 **Alert Recorded!** Your supervisor has been notified. Move immediately to a shaded rest area, drink cool water, and seek medical attention if symptoms persist.", "flags": 64}})
        cmd_name = body.get("data", {}).get("name", "")
        is_join = cmd_name in ("join", "start") or body.get("action") == "join" or str(body.get("content", "")).startswith("/join")
        if is_join:
            options = body.get("data", {}).get("options", [])
            target_site = "default"
            if options and isinstance(options, list):
                target_site = str(options[0].get("value", "default"))
            elif body.get("siteId"):
                target_site = str(body.get("siteId"))
            elif str(body.get("content", "")).startswith("/join"):
                parts = str(body.get("content")).split()
                if len(parts) > 1: target_site = parts[1]
            WORKERS.setdefault(target_site, {})[user_id] = {"chatId": user_id, "name": username, "channel": "discord", "language": "en", "status": "JOINED", "joinedAt": datetime.now(UTC).isoformat()}
            _save_record(f"SITE#{target_site}", f"WORKER#{user_id}", WORKERS[target_site][user_id])
            return _json(200, {"type": 4, "data": {"content": f"👷 **Joined site {target_site} via Discord!** You will receive daily heat safety briefings.", "flags": 64}, "ok": True, "joined": target_site})
        direct_action = body.get("action")
        plan_id = body.get("plan_id") or body.get("planId", "")
        if direct_action and plan_id:
            site_id = next((key[0] for key in PLANS if PLANS[key].get("plan_id") == plan_id), None)
            if not site_id and "-" in plan_id:
                site_id = plan_id.rsplit("-", 3)[0]
            site_id = site_id or "default"
            if direct_action == "ok":
                ACKS.setdefault(site_id, {})[user_id] = {"chatId": user_id, "name": username, "channel": "discord", "status": "ACKNOWLEDGED", "at": datetime.now(UTC).isoformat()}
                _save_record(f"SITE#{site_id}", f"ACK#{datetime.now().date()}#{user_id}", ACKS[site_id][user_id])
                return _json(200, {"ok": True, "status": "ACKNOWLEDGED"})
            elif direct_action == "unwell":
                ALERTS.setdefault(site_id, {})[user_id] = {"alertId": user_id, "chatId": user_id, "name": username, "channel": "discord", "status": "OPEN", "at": datetime.now(UTC).isoformat()}
                _save_record(f"SITE#{site_id}", f"ALERT#{user_id}", ALERTS[site_id][user_id])
                return _json(200, {"ok": True, "status": "OPEN"})
        return _json(200, {"ok": True})
    if path.endswith("/email/ack") and method in {"GET", "POST"}:
        query = event.get("queryStringParameters") or {}
        action, site_id, plan_id, email = query.get("a", ""), query.get("site", ""), query.get("plan", ""), str(query.get("e", "")).lower()
        if action not in {"ok", "unwell"} or not verify_action(_gmail_app_password(), action, site_id, plan_id, email, query.get("s", "")):
            return _html(403, "HeatShield", "<p>This check-in link is not valid.</p>")
        if method == "GET":
            # Mail scanners prefetch links, so the GET only shows a confirm button; the POST records the check-in.
            label = "I'm OK" if action == "ok" else "Not OK"
            color = "#22A474" if action == "ok" else "#D32F2F"
            return _html(200, "HeatShield check-in", f'<form method="post"><button type="submit" style="padding:14px 22px;border:0;border-radius:8px;background:{color};color:#fff;font-size:18px;font-weight:700">{label}</button></form>')
        name = email.split("@", 1)[0]
        if action == "ok":
            ACKS.setdefault(site_id, {})[email] = {"chatId": email, "name": name, "channel": "email", "status": "ACKNOWLEDGED", "at": datetime.now(UTC).isoformat()}
            _save_record(f"SITE#{site_id}", f"ACK#{datetime.now().date()}#{email}", ACKS[site_id][email])
            return _html(200, "Plan acknowledged", "<p>Stay hydrated and follow your rest intervals.</p>")
        ALERTS.setdefault(site_id, {})[email] = {"alertId": email, "chatId": email, "name": name, "channel": "email", "status": "OPEN", "at": datetime.now(UTC).isoformat()}
        _save_record(f"SITE#{site_id}", f"ALERT#{email}", ALERTS[site_id][email])
        return _html(200, "Alert recorded", "<p>Your supervisor has been notified. Move to a shaded rest area, drink cool water, and seek medical attention if symptoms persist.</p>")
    if path.endswith("/sites") and method == "POST": site = _site(body); _save_site(site); return _json(201, {"site": _site_dict(site)})
    if path.endswith("/sites") and method == "GET": return _json(200, {"sites": [_site_dict(s) for s in SITES.values()]})
    if "/sites/" in path:
        parts = path.strip("/").split("/"); site_index = parts.index("sites"); site_id = parts[site_index + 1] if len(parts) > site_index + 1 else "default"
        _hydrate_site_state(site_id)
        site = _load_site(site_id) or _site({"siteId": site_id}); _save_site(site)
        if len(parts) == site_index + 2 and method == "PUT":
            updated = _site({**_site_dict(site), **body, "siteId": site_id}); _save_site(updated); _invalidate_plans(site_id)
            return _json(200, {"site": _site_dict(updated)})
        if path.endswith("/status"): return _json(200, _dashboard(site))
        if path.endswith("/join") and method == "GET":
            site_dict = _site_dict(site)
            tg_url = f"https://t.me/{site_dict['telegramBotUsername']}?start={site_id}"
            discord_invite = site_dict["discordInviteUrl"]
            dc_url = f"{discord_invite}?site={site_id}" if "?" not in discord_invite else f"{discord_invite}&site={site_id}"
            return _json(200, {"siteId": site_id, "joinCode": site_id, "joinUrl": tg_url, "telegramJoinUrl": tg_url, "discordJoinUrl": dc_url})
        if path.endswith("/plans/generate") and method == "POST":
            mode, replay = body.get("mode", "LIVE"), body.get("replayDate"); plan = generate_plan(site, get_weather(site, mode, replay), mode, replay).to_dict(); PLANS[(site_id, plan["date"])] = plan; _save_record(f"SITE#{site_id}", f"PLAN#{plan['date']}", {"plan": plan}); peak = max(plan["hours"], key=lambda h: h["forecast_heat_risk"]); return _json(200, {"plan": plan, "planView": {"title": "HeatShield work plan", "level": peak["level"].lower(), "message": f"Hottest at {peak['time']} ({peak['forecast_heat_risk']}/100). Plan your day carefully.", "checklist": [f"Heavy work only in the safe hours — follow the work blocks below.", f"Rest breaks: {plan['blocks'][0]['rest_minutes_per_hour']} min per hour.", f"Water breaks: every {plan['blocks'][0]['water_every_minutes']} min."], "generated_by": "weather plan"}})
        if "/plans/" in path:
            date = parts[site_index + 3] if len(parts) > site_index + 3 else datetime.now().strftime("%Y-%m-%d"); plan = PLANS.get((site_id, date)) or _plan(site)
            if path.endswith("/approve") and method == "POST":
                caller = get_caller_identity(event)
                plan["status"] = "APPROVED"
                plan["approvedBy"] = body.get("approvedBy") or caller.get("name") or caller.get("email") or "supervisor"
                plan["approvedAt"] = datetime.now(UTC).isoformat()
                _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan})
                return _json(200, {"plan": plan})
            if path.endswith("/send") and method == "POST":
                if plan["status"] != "APPROVED": return _json(409, {"error": "Plan must be approved before sending to workers"})
                custom_email_msg = body.get("customEmailMessage")
                tg_workers = [w for w in WORKERS.get(site_id, {}).values() if w.get("channel") not in ("discord", "email")]
                tg_delivered = sum(_send_telegram(w["chatId"], safe_bedrock_message(plan, site.name, w.get("language", site.default_language)), plan["plan_id"]) for w in tg_workers)
                discord_url = getattr(site, "discord_webhook_url", None) or _secret("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_PARAMETER")
                discord_broadcast = 1 if (discord_url and send_discord_webhook(discord_url, plan, site.name, site.default_language)) else 0
                discord_workers = [w for w in WORKERS.get(site_id, {}).values() if w.get("channel") == "discord"]
                discord_count = len(discord_workers) if discord_broadcast else 0
                email_delivered = send_gmail_briefing(os.getenv("GMAIL_ADDRESS"), _gmail_app_password(), getattr(site, "email_recipients", None) or parse_recipients(os.getenv("GMAIL_RECIPIENTS")), plan, site.name, site.default_language, _public_base_url(event), custom_email_msg)
                delivered = tg_delivered + discord_count + (discord_broadcast if not discord_workers else 0) + email_delivered
                plan["status"] = "SENT"
                _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan})
                return _json(200, {"plan": plan, "delivered": delivered, "channels": {"telegram": tg_delivered, "discord": discord_count + discord_broadcast, "email": email_delivered}})
            return _json(200, {"plan": plan})
    if "/alerts/" in path and method == "POST":
        alert_id = path.strip("/").split("/")[-1]
        target_site_id = next((sid for sid, group in ALERTS.items() if alert_id in group), "demo")
        alert = next((a for group in ALERTS.values() for aid, a in group.items() if aid == alert_id), None)
        if alert: alert["status"] = "RESOLVED"; _save_record(f"SITE#{target_site_id}", f"ALERT#{alert_id}", alert); return _json(200, {"alert": alert})
    return _json(404, {"error": "route not found"})


if __name__ == "__main__":
    from server import run
    run(int(os.environ.get("PORT", "3000")))
