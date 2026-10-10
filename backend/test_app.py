import json

from app import handler
from core.heat import forecast_heat_risk, risk_level
from core.messages import compose_message
from core.models import SiteProfile
from core.planner import generate_plan


def test_risk_bands():
    assert risk_level(20) == "LOW"
    assert risk_level(50) == "MODERATE"
    assert risk_level(75) == "HIGH"
    assert risk_level(95) == "EXTREME"

def test_new_worker_never_gets_less_protection():
    normal = SiteProfile("a", "A", 17, 78)
    new = SiteProfile("b", "B", 17, 78, acclimatization="NEW_OR_UNKNOWN")
    assert forecast_heat_risk(38, 60, new)[0] >= forecast_heat_risk(38, 60, normal)[0]

def test_plan_is_draft_and_disclaims_wbgt():
    site = SiteProfile("site", "Build", 17, 78)
    plan = generate_plan(site, [{"time": "2026-10-10T12:00", "temperature": 38, "humidity": 55}])
    assert plan.status == "DRAFT"
    assert plan.measured_wbgt is False
    assert "Not measured WBGT" in plan.disclaimer

def test_send_requires_approval():
    event = {"rawPath": "/api/sites/demo/plans/2026-10-10/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}
    assert handler(event, None)["statusCode"] == 409

def test_health():
    result = handler({"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}}, None)
    assert json.loads(result["body"])["ok"] is True

def test_wind_and_radiation_change_risk():
    site = SiteProfile("site", "Build", 17, 78)
    calm_hot, _, _ = forecast_heat_risk(38, 55, site, 2, 800)
    windy_hot, _, _ = forecast_heat_risk(38, 55, site, 25, 100)
    assert calm_hot > windy_hot

def test_message_accepts_serialized_plan():
    site = SiteProfile("site", "Build", 17, 78)
    plan = generate_plan(site, [{"time": "2026-10-10T12:00", "temperature": 38, "humidity": 55}]).to_dict()
    assert "HeatShield" in compose_message(plan, site.name, "en")

def test_scheduled_plan_endpoint_stages_draft():
    result = handler({"rawPath": "/api/plan", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"scheduled": True})}, None)
    payload = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert payload["plans"][0]["status"] == "DRAFT"

def test_telegram_secret_is_enforced(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    event = {"rawPath": "/telegram/webhook", "requestContext": {"http": {"method": "POST"}}, "headers": {"x-telegram-bot-api-secret-token": "wrong"}, "body": "{}"}
    assert handler(event, None)["statusCode"] == 403

def test_discord_ping():
    event = {
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"type": 1}),
    }
    res = handler(event, None)
    assert res["statusCode"] == 200
    assert json.loads(res["body"]) == {"type": 1}

def test_discord_component_interactions():
    event_ok = {
        "rawPath": "/discord/interactions",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({
            "type": 3,
            "data": {"custom_id": "ok:demo-2026-10-10"},
            "member": {"user": {"id": "dc_worker_1", "username": "Rajesh"}},
        }),
    }
    res_ok = handler(event_ok, None)
    assert res_ok["statusCode"] == 200
    payload_ok = json.loads(res_ok["body"])
    assert payload_ok["type"] == 4
    assert "Acknowledged" in payload_ok["data"]["content"]

    event_unwell = {
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({
            "type": 3,
            "data": {"custom_id": "unwell:demo-2026-10-10"},
            "user": {"id": "dc_worker_2", "username": "Suresh"},
        }),
    }
    res_unwell = handler(event_unwell, None)
    assert res_unwell["statusCode"] == 200
    payload_unwell = json.loads(res_unwell["body"])
    assert payload_unwell["type"] == 4
    assert "Alert Recorded" in payload_unwell["data"]["content"]

def test_discord_slash_command_join():
    event_join = {
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({
            "type": 2,
            "data": {"name": "join", "options": [{"name": "site", "value": "hyd-site"}]},
            "user": {"id": "dc_worker_3", "username": "Anil"},
        }),
    }
    res = handler(event_join, None)
    assert res["statusCode"] == 200
    payload = json.loads(res["body"])
    assert payload["ok"] is True
    assert payload["joined"] == "hyd-site"

def test_discord_secret_is_enforced(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_SECRET", "dc_secret_123")
    event = {
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-discord-secret-token": "wrong_secret"},
        "body": json.dumps({"type": 1}),
    }
    assert handler(event, None)["statusCode"] == 403

def test_multi_channel_plan_send(monkeypatch):
    site_id = "test-site"
    handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"siteId": site_id, "name": "Test Site"})}, None)
    handler({"rawPath": f"/api/sites/{site_id}/plans/generate", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    handler({"rawPath": f"/api/sites/{site_id}/plans/2026-10-10/approve", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)

    handler({
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"type": 2, "data": {"name": "join", "options": [{"name": "site", "value": site_id}]}, "user": {"id": "dc_1", "username": "WorkerDC"}}),
    }, None)

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/mock")
    import app
    monkeypatch.setattr(app, "send_discord_webhook", lambda *args, **kwargs: True)

    send_res = handler({"rawPath": f"/api/sites/{site_id}/plans/2026-10-10/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    assert send_res["statusCode"] == 200
    payload = json.loads(send_res["body"])
    assert payload["plan"]["status"] == "SENT"
    assert payload["delivered"] >= 1
    assert "channels" in payload
    assert payload["channels"]["discord"] >= 1

