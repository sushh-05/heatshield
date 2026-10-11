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
    assert "not a medical test" in plan.disclaimer

def test_send_requires_approval():
    event = {"rawPath": "/api/sites/demo/plans/2026-10-10/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}
    assert handler(event, None)["statusCode"] == 409

def test_update_site_in_place_invalidates_cached_plan():
    """Moving an existing site should drop its cached plan — otherwise the
    dashboard would keep serving weather computed for the old coordinates."""
    from app import PLANS

    site_id = "put-test-site"
    created = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"siteId": site_id, "name": "Original", "latitude": 17.0, "longitude": 78.0})}, None)
    assert created["statusCode"] == 201

    status_before = handler({"rawPath": f"/api/sites/{site_id}/status", "requestContext": {"http": {"method": "GET"}}}, None)
    assert status_before["statusCode"] == 200
    assert any(key[0] == site_id for key in PLANS), "expected a cached plan after the first status fetch"

    updated = handler({"rawPath": f"/api/sites/{site_id}", "requestContext": {"http": {"method": "PUT"}}, "body": json.dumps({"latitude": 28.6, "longitude": 77.2, "name": "Moved"})}, None)
    assert updated["statusCode"] == 200
    updated_site = json.loads(updated["body"])["site"]
    assert updated_site["siteId"] == site_id
    assert updated_site["name"] == "Moved"
    assert updated_site["latitude"] == 28.6
    assert updated_site["longitude"] == 77.2
    assert not any(key[0] == site_id for key in PLANS), "cached plan for the old location should be invalidated"

def test_site_has_editable_bot_name_and_join_links_use_it():
    site_id = "bot-name-test-site"
    created = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({
        "siteId": site_id, "name": "Custom Bot Site",
        "telegramBotUsername": "@MyCrewBot", "discordInviteUrl": "https://discord.gg/mycrew",
    })}, None)
    assert created["statusCode"] == 201
    created_site = json.loads(created["body"])["site"]
    # A leading "@" is stripped so the stored username drops straight into a t.me/<username> URL.
    assert created_site["telegramBotUsername"] == "MyCrewBot"
    assert created_site["discordInviteUrl"] == "https://discord.gg/mycrew"

    join = handler({"rawPath": f"/api/sites/{site_id}/join", "requestContext": {"http": {"method": "GET"}}}, None)
    assert join["statusCode"] == 200
    join_body = json.loads(join["body"])
    assert join_body["telegramJoinUrl"] == f"https://t.me/MyCrewBot?start={site_id}"
    assert join_body["discordJoinUrl"] == f"https://discord.gg/mycrew?site={site_id}"

def test_site_without_custom_bot_name_falls_back_to_default():
    site_id = "default-bot-name-site"
    handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"siteId": site_id, "name": "Default Bot Site"})}, None)
    join = handler({"rawPath": f"/api/sites/{site_id}/join", "requestContext": {"http": {"method": "GET"}}}, None)
    join_body = json.loads(join["body"])
    assert join_body["telegramJoinUrl"] == f"https://t.me/HeatShieldBot?start={site_id}"

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

def test_auth_me_demo_profile():
    res = handler({"rawPath": "/auth/me", "requestContext": {"http": {"method": "GET"}}}, None)
    assert res["statusCode"] == 200
    payload = json.loads(res["body"])
    assert payload["ok"] is True
    assert payload["supervisor"]["name"] == "John Doe"
    assert payload["supervisor"]["role"] == "Field Safety Supervisor"
    assert "OSHA 30-Hour Construction Safety" in payload["supervisor"]["certifications"]

def test_auth_me_with_clerk_jwt():
    import base64
    import time
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    claims = {
        "sub": "user_2live_clerk_999",
        "email": "jane.safety@apexcorp.com",
        "name": "Jane Safety Officer",
        "role": "Chief Safety Inspector",
        "exp": int(time.time()) + 3600,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"{header}.{payload_b64}.mock_sig"

    event = {
        "rawPath": "/auth/me",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {"authorization": f"Bearer {token}"},
    }
    res = handler(event, None)
    assert res["statusCode"] == 200
    data = json.loads(res["body"])
    assert data["supervisor"]["user_id"] == "user_2live_clerk_999"
    assert data["supervisor"]["email"] == "jane.safety@apexcorp.com"
    assert data["supervisor"]["name"] == "Jane Safety Officer"
    assert data["supervisor"]["role"] == "Chief Safety Inspector"
    assert data["supervisor"]["provider"] == "clerk"
    assert data["supervisor"]["authenticated"] is True

def test_plan_approval_records_clerk_identity():
    import base64
    import time
    site_id = "clerk-auth-site"
    handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"siteId": site_id, "name": "Clerk Auth Site"})}, None)
    handler({"rawPath": f"/api/sites/{site_id}/plans/generate", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)

    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    claims = {"sub": "user_clerk_lead", "name": "Captain Safe", "email": "lead@safe.org", "exp": int(time.time()) + 3600}
    token = f"{header}.{base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')}.sig"

    approve_event = {
        "rawPath": f"/api/sites/{site_id}/plans/2026-10-10/approve",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"authorization": f"Bearer {token}"},
        "body": "{}",
    }
    res = handler(approve_event, None)
    assert res["statusCode"] == 200
    approved_plan = json.loads(res["body"])["plan"]
    assert approved_plan["status"] == "APPROVED"
    assert approved_plan["approvedBy"] == "Captain Safe"



def test_gmail_plan_send_and_check_in_links(monkeypatch):
    import re
    from urllib.parse import parse_qsl, urlparse

    import app
    from core import gmail

    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def login(self, user, password): assert password == "abcdefgh"
        def send_message(self, message): sent.append(message)

    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(app, "send_discord_webhook", lambda *args, **kwargs: False)
    monkeypatch.setenv("GMAIL_ADDRESS", "safety@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh")
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.com")

    site_id = "gmail-site"
    created = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"siteId": site_id, "name": "Gmail Site", "emailRecipients": "Lead@Example.com, not-an-email, crew@example.com"})}, None)
    assert json.loads(created["body"])["site"]["emailRecipients"] == ["lead@example.com", "crew@example.com"]
    handler({"rawPath": f"/api/sites/{site_id}/plans/generate", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    handler({"rawPath": f"/api/sites/{site_id}/plans/2026-10-10/approve", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)

    send_res = handler({"rawPath": f"/api/sites/{site_id}/plans/2026-10-10/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    payload = json.loads(send_res["body"])
    assert payload["channels"]["email"] == 2
    assert [m["To"] for m in sent] == ["lead@example.com", "crew@example.com"]

    text = sent[0].get_body(preferencelist=("plain",)).get_content()
    ok_url, unwell_url = re.findall(r"https://api\.example\.com/email/ack\?\S+", text)

    def ack(url, method, **overrides):
        parsed = urlparse(url)
        query = {**dict(parse_qsl(parsed.query)), **overrides}
        return handler({"rawPath": parsed.path, "queryStringParameters": query, "requestContext": {"http": {"method": method}}}, None)

    assert ack(ok_url, "GET")["statusCode"] == 200
    assert "lead@example.com" not in app.ACKS.get(site_id, {})
    assert ack(ok_url, "POST")["statusCode"] == 200
    assert app.ACKS[site_id]["lead@example.com"]["channel"] == "email"
    assert ack(ok_url, "POST", a="unwell")["statusCode"] == 403
    assert ack(ok_url, "POST", e="crew@example.com")["statusCode"] == 403
    assert ack(unwell_url, "POST")["statusCode"] == 200
    assert app.ALERTS[site_id]["lead@example.com"]["status"] == "OPEN"

def test_gmail_is_skipped_when_unconfigured(monkeypatch):
    from core import gmail

    def fail(*args, **kwargs): raise AssertionError("SMTP must not be opened without credentials")
    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL", fail)
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert gmail.send_gmail_briefing(None, None, ["crew@example.com"], {}, "Site") == 0
