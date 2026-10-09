"""HeatShield API: site setup, deterministic planning, approval, Telegram and alerts."""
import json
import os
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen

try:
    import boto3
except ImportError:
    boto3 = None

from core.forecast import get_weather
from core.messages import safe_bedrock_message
from core.models import SiteProfile
from core.planner import generate_plan

SITES, PLANS, WORKERS, ACKS, ALERTS = {}, {}, {}, {}, {}

def _table():
    if not boto3 or not os.getenv("TABLE_NAME"): return None
    try: return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    except Exception: return None

def _body(event):
    raw = event.get("body") or "{}"
    return json.loads(raw) if isinstance(raw, str) else raw

def _json(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type"}, "body": json.dumps(body)}

def _site(raw):
    return SiteProfile(raw.get("siteId", uuid.uuid4().hex[:8]), raw.get("name", "Hyderabad Construction Site"), float(raw.get("latitude", 17.385)), float(raw.get("longitude", 78.487)), raw.get("timezone", "Asia/Kolkata"), int(raw.get("workStartHour", 6)), int(raw.get("workEndHour", 18)), raw.get("workload", "MODERATE"), raw.get("acclimatization", "ACCLIMATIZED"), raw.get("clothing", "NORMAL"), raw.get("defaultLanguage", "en"))

def _save_site(site):
    SITES[site.site_id] = site
    if boto3 and os.getenv("TABLE_NAME"):
        try: boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]).put_item(Item={"pk": f"SITE#{site.site_id}", "sk": "META", **site.__dict__})
        except Exception: pass

def _load_site(site_id):
    if site_id in SITES: return SITES[site_id]
    table = _table()
    if table:
        try:
            item = table.get_item(Key={"pk": f"SITE#{site_id}", "sk": "META"}).get("Item")
            if item: site = _site(item); SITES[site_id] = site; return site
        except Exception: pass
    return None

def _save_record(pk, sk, item):
    table = _table()
    if table:
        try: table.put_item(Item={"pk": pk, "sk": sk, **item})
        except Exception: pass

def _site_dict(site): return {"siteId": site.site_id, "name": site.name, "latitude": site.latitude, "longitude": site.longitude, "timezone": site.timezone, "workStartHour": site.work_start_hour, "workEndHour": site.work_end_hour, "workload": site.workload, "acclimatization": site.acclimatization, "clothing": site.clothing, "defaultLanguage": site.default_language}

def _plan(site, mode="LIVE", replay_date=None):
    date = replay_date or datetime.now().strftime("%Y-%m-%d")
    key = (site.site_id, date)
    if key not in PLANS:
        table = _table()
        if table:
            try:
                saved = table.get_item(Key={"pk": f"SITE#{site.site_id}", "sk": f"PLAN#{date}"}).get("Item")
                if saved: PLANS[key] = saved.get("plan", saved)
            except Exception: pass
    if key not in PLANS:
        PLANS[key] = generate_plan(site, get_weather(site, mode, replay_date), mode, replay_date).to_dict(); _save_record(f"SITE#{site.site_id}", f"PLAN#{date}", {"plan": PLANS[key]})
    return PLANS[key]

def _dashboard(site):
    plan = _plan(site)
    hours = [{"time": h["time"], "temperature": h["temperature_c"], "humidity": h["relative_humidity_pct"], "risk": h["forecast_heat_risk"], "level": h["level"]} for h in plan["hours"]]
    peak = max(hours, key=lambda h: h["risk"]) if hours else {"time": "--", "temperature": 0, "humidity": 0, "risk": 0, "level": "LOW"}
    plan_view = {"title": "HeatShield morning plan", "level": peak["level"].lower(), "peak": peak, "message": f"Peak risk is {peak['level'].lower()} at {peak['time']} ({peak['risk']}/100).", "checklist": ["Heavy work: follow the approved work blocks.", f"Rest: {plan['blocks'][0]['rest_minutes_per_hour'] if plan['blocks'] else 0} minutes each hour.", f"Water: every {plan['blocks'][0]['water_every_minutes'] if plan['blocks'] else 30} minutes."], "generated_by": "deterministic planner"}
    return {"site": _site_dict(site), "forecast": {"location": site.name, "hourly": hours, "source": "Open-Meteo"}, "current": hours[0] if hours else peak, "peak": peak, "level": peak["level"].lower(), "plan": plan_view, "canonicalPlan": plan, "workers": list(WORKERS.get(site.site_id, {}).values()), "acknowledgements": list(ACKS.get(site.site_id, {}).values()), "alerts": list(ALERTS.get(site.site_id, {}).values()), "updated_at": datetime.now(timezone.utc).isoformat()}

def _send_telegram(chat_id, text, plan_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: return False
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [[{"text": "OK", "callback_data": f"ok:{plan_id}"}, {"text": "I feel unwell", "callback_data": f"unwell:{plan_id}"}]]}}
    try:
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5): return True
    except Exception: return False

def handler(event, context):
    path = event.get("rawPath", event.get("path", "/")); method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")); body = _body(event)
    if path.endswith("/health"): return _json(200, {"ok": True, "service": "heatshield-api"})
    if path.endswith("/dashboard") and method == "GET":
        site = _load_site("demo") or _site({"siteId": "demo"}); _save_site(site); return _json(200, _dashboard(site))
    if path.endswith("/telegram/webhook") and method == "POST":
        message = body.get("message", {}); text = message.get("text", ""); chat_id = str(message.get("chat", {}).get("id", ""))
        if text.startswith("/start") and chat_id:
            site_id = text.split(maxsplit=1)[1] if len(text.split()) > 1 else "default"
            WORKERS.setdefault(site_id, {})[chat_id] = {"chatId": chat_id, "name": message.get("from", {}).get("first_name", "Worker"), "language": "te", "status": "JOINED", "joinedAt": datetime.now(timezone.utc).isoformat()}; _save_record(f"SITE#{site_id}", f"WORKER#{chat_id}", WORKERS[site_id][chat_id])
            return _json(200, {"ok": True, "joined": site_id})
        callback = body.get("callback_query", {}); data = callback.get("data", "")
        if data:
            action, plan_id = data.split(":", 1); chat_id = str(callback.get("from", {}).get("id", "unknown")); site_id = next((key[0] for key in PLANS if PLANS[key]["plan_id"] == plan_id), "default")
            if action == "ok": ACKS.setdefault(site_id, {})[chat_id] = {"chatId": chat_id, "status": "ACKNOWLEDGED", "at": datetime.now(timezone.utc).isoformat()}; _save_record(f"SITE#{site_id}", f"ACK#{datetime.now().date()}#{chat_id}", ACKS[site_id][chat_id])
            if action == "unwell": ALERTS.setdefault(site_id, {})[chat_id] = {"alertId": chat_id, "chatId": chat_id, "status": "OPEN", "at": datetime.now(timezone.utc).isoformat()}; _save_record(f"SITE#{site_id}", f"ALERT#{chat_id}", ALERTS[site_id][chat_id])
        return _json(200, {"ok": True})
    if path.endswith("/sites") and method == "POST": site = _site(body); _save_site(site); return _json(201, {"site": _site_dict(site)})
    if path.endswith("/sites") and method == "GET": return _json(200, {"sites": [_site_dict(s) for s in SITES.values()]})
    if "/sites/" in path:
        parts = path.strip("/").split("/"); site_index = parts.index("sites"); site_id = parts[site_index + 1] if len(parts) > site_index + 1 else "default"; site = _load_site(site_id) or _site({"siteId": site_id}); _save_site(site)
        if path.endswith("/status"): return _json(200, _dashboard(site))
        if path.endswith("/join") and method == "GET": return _json(200, {"siteId": site_id, "joinCode": site_id, "joinUrl": f"https://t.me/{os.getenv('TELEGRAM_BOT_USERNAME', 'HeatShieldBot')}?start={site_id}"})
        if path.endswith("/plans/generate") and method == "POST":
            mode, replay = body.get("mode", "LIVE"), body.get("replayDate"); plan = generate_plan(site, get_weather(site, mode, replay), mode, replay).to_dict(); PLANS[(site_id, plan["date"])] = plan; _save_record(f"SITE#{site_id}", f"PLAN#{plan['date']}", {"plan": plan}); peak = max(plan["hours"], key=lambda h: h["forecast_heat_risk"]); return _json(200, {"plan": plan, "planView": {"title": "HeatShield morning plan", "level": peak["level"].lower(), "message": f"Peak risk is {peak['level'].lower()} at {peak['time']} ({peak['forecast_heat_risk']}/100).", "checklist": ["Heavy work: follow the approved work blocks.", f"Rest: {plan['blocks'][0]['rest_minutes_per_hour']} minutes each hour.", f"Water: every {plan['blocks'][0]['water_every_minutes']} minutes."], "generated_by": "deterministic planner"}})
        if "/plans/" in path:
            date = parts[site_index + 3] if len(parts) > site_index + 3 else datetime.now().strftime("%Y-%m-%d"); plan = PLANS.get((site_id, date)) or _plan(site)
            if path.endswith("/approve") and method == "POST": plan["status"] = "APPROVED"; plan["approvedBy"] = body.get("approvedBy", "supervisor"); plan["approvedAt"] = datetime.now(timezone.utc).isoformat(); _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan}); return _json(200, {"plan": plan})
            if path.endswith("/send") and method == "POST":
                if plan["status"] != "APPROVED": return _json(409, {"error": "Plan must be approved before sending to workers"})
                delivered = sum(_send_telegram(worker["chatId"], safe_bedrock_message(plan, site.name, worker.get("language", site.default_language)), plan["plan_id"]) for worker in WORKERS.get(site_id, {}).values()); plan["status"] = "SENT"; _save_record(f"SITE#{site_id}", f"PLAN#{date}", {"plan": plan}); return _json(200, {"plan": plan, "delivered": delivered})
            return _json(200, {"plan": plan})
    if "/alerts/" in path and method == "POST":
        alert_id = path.strip("/").split("/")[-1]
        alert = next((a for group in ALERTS.values() for aid, a in group.items() if aid == alert_id), None)
        if alert: alert["status"] = "RESOLVED"; _save_record(f"SITE#{site_id if 'site_id' in locals() else 'demo'}", f"ALERT#{alert_id}", alert); return _json(200, {"alert": alert})
    return _json(404, {"error": "route not found"})
