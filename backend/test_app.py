import json

from app import handler
from core.heat import forecast_heat_risk, risk_level
from core.messages import compose_message
from core.models import SiteProfile
from core.planner import generate_plan


def call(method, path, body=None, headers=None, query=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}}
    if body is not None: event["body"] = body if isinstance(body, str) else json.dumps(body)
    if headers: event["headers"] = headers
    if query: event["queryStringParameters"] = query
    result = handler(event, None)
    try: payload = json.loads(result["body"])
    except (TypeError, ValueError): payload = result["body"]
    return result["statusCode"], payload


def new_site_with_plan(site_id, **site):
    """Creates a site and today's draft plan; returns the plan's date."""
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": site_id, "name": site_id, **site})[0] == 201
    status, payload = call("POST", f"/api/sites/{site_id}/plans/generate", {})
    assert status == 200
    return payload["plan"]["date"]


def signed_clerk_token(monkeypatch, claims):
    """A real RS256 token plus the matching CLERK_JWT_KEY, so verification runs for real."""
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setenv("CLERK_JWT_KEY", public_pem)
    return jwt.encode(claims, key, algorithm="RS256")


def forged_token(claims):
    import base64
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.forged"


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
    date = new_site_with_plan("needs-approval")
    assert call("POST", f"/api/sites/needs-approval/plans/{date}/send", {})[0] == 409

def test_plan_for_an_unknown_date_is_not_silently_swapped_for_todays():
    new_site_with_plan("dated-site")
    assert call("POST", "/api/sites/dated-site/plans/2001-01-01/approve", {})[0] == 404

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
    created = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"latitude": 17.4, "longitude": 78.5, 
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

def test_site_without_a_bot_has_no_join_link_rather_than_a_made_up_one(monkeypatch):
    site_id = "no-bot-site"
    call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": site_id, "name": "No Bot Site"})
    join = call("GET", f"/api/sites/{site_id}/join")[1]
    assert join["telegramJoinUrl"] is None and join["discordJoinUrl"] is None
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "DeployedBot")
    assert call("GET", f"/api/sites/{site_id}/join")[1]["telegramJoinUrl"] == f"https://t.me/DeployedBot?start={site_id}"

def test_a_scheduled_run_that_stages_nothing_raises_so_it_is_retried(monkeypatch):
    import pytest

    import app
    from core.forecast import WeatherUnavailable
    def down(*args, **kwargs): raise WeatherUnavailable("offline")
    monkeypatch.setattr(app, "get_weather", down)
    with pytest.raises(RuntimeError):
        handler({"source": "heatshield.scheduler", "rawPath": "/api/plan", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)

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
    date = new_site_with_plan("demo-crew")
    event_ok = {
        "rawPath": "/discord/interactions",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({
            "type": 3,
            "data": {"custom_id": f"ok:demo-crew-{date}"},
            "member": {"user": {"id": "dc_worker_1", "username": "Rajesh"}},
        }),
    }
    res_ok = handler(event_ok, None)
    assert res_ok["statusCode"] == 200
    payload_ok = json.loads(res_ok["body"])
    assert payload_ok["type"] == 4
    assert "acknowledged" in payload_ok["data"]["content"]

    event_unwell = {
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({
            "type": 3,
            "data": {"custom_id": f"unwell:demo-crew-{date}"},
            "user": {"id": "dc_worker_2", "username": "Suresh"},
        }),
    }
    res_unwell = handler(event_unwell, None)
    assert res_unwell["statusCode"] == 200
    payload_unwell = json.loads(res_unwell["body"])
    assert payload_unwell["type"] == 4
    assert "Alert recorded" in payload_unwell["data"]["content"]
    # No supervisor channel is configured here, so the worker must not be told that someone was notified.
    assert "has been notified" not in payload_unwell["data"]["content"]
    assert "Tell your supervisor" in payload_unwell["data"]["content"]

def test_discord_slash_command_join():
    call("POST", "/api/sites", {"siteId": "hyd-site", "name": "Hyd", "latitude": 17.4, "longitude": 78.5})
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
    assert handler(event, None)["statusCode"] == 401

def test_multi_channel_plan_send(monkeypatch):
    site_id = "test-site"
    date = new_site_with_plan(site_id)
    assert call("POST", f"/api/sites/{site_id}/plans/{date}/approve", {})[0] == 200

    handler({
        "rawPath": "/discord/webhook",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"type": 2, "data": {"name": "join", "options": [{"name": "site", "value": site_id}]}, "user": {"id": "dc_1", "username": "WorkerDC"}}),
    }, None)

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/mock")
    import app
    monkeypatch.setattr(app, "send_discord_webhook", lambda *args, **kwargs: True)

    send_res = handler({"rawPath": f"/api/sites/{site_id}/plans/{date}/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    assert send_res["statusCode"] == 200
    payload = json.loads(send_res["body"])
    assert payload["plan"]["status"] == "SENT"
    assert payload["delivered"] >= 1
    assert "channels" in payload
    assert payload["channels"]["discord"] >= 1

def test_auth_me_in_open_access_is_an_unauthenticated_demo_identity():
    status, payload = call("GET", "/auth/me")
    assert status == 200
    assert payload["openAccess"] is True
    assert payload["supervisor"]["provider"] == "demo"
    assert payload["supervisor"]["authenticated"] is False
    assert "John Doe" not in json.dumps(payload)

def test_supervisor_routes_are_closed_unless_open_access_is_enabled(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "locked", "name": "Locked"})[0] == 401
    assert call("PUT", "/api/sites/demo", {"name": "Renamed"})[0] == 401
    assert call("POST", "/api/sites/demo/plans/generate", {})[0] == 401
    assert call("GET", "/api/sites")[0] == 401
    # The public dashboard stays readable.
    assert call("GET", "/api/sites/demo/status")[0] == 200

def test_supervisor_key_authorizes(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    monkeypatch.setenv("SUPERVISOR_KEY", "s3cret-key")
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "keyed", "name": "Keyed"}, headers={"x-api-key": "wrong"})[0] == 401
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "keyed", "name": "Keyed"}, headers={"Authorization": "Bearer s3cret-key"})[0] == 201

def test_forged_clerk_token_is_rejected(monkeypatch):
    import time
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    signed_clerk_token(monkeypatch, {"sub": "someone", "exp": int(time.time()) + 3600})  # configures the real verification key
    forged = forged_token({"sub": "attacker", "name": "Attacker", "exp": int(time.time()) + 3600})
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "forged", "name": "Forged"}, headers={"authorization": f"Bearer {forged}"})[0] == 401
    status, payload = call("GET", "/auth/me", headers={"authorization": f"Bearer {forged}"})
    assert payload["supervisor"]["authenticated"] is False
    assert payload["supervisor"]["name"] == ""

def test_expired_clerk_token_is_rejected(monkeypatch):
    import time
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    token = signed_clerk_token(monkeypatch, {"sub": "user_1", "exp": int(time.time()) - 3600})
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "expired", "name": "Expired"}, headers={"authorization": f"Bearer {token}"})[0] == 401

def test_auth_me_with_verified_clerk_token(monkeypatch):
    import time
    token = signed_clerk_token(monkeypatch, {"sub": "user_2live_clerk_999", "email": "jane.safety@apexcorp.com", "name": "Jane Safety Officer", "role": "Chief Safety Inspector", "exp": int(time.time()) + 3600})
    status, data = call("GET", "/auth/me", headers={"authorization": f"Bearer {token}"})
    assert status == 200
    assert data["supervisor"]["user_id"] == "user_2live_clerk_999"
    assert data["supervisor"]["email"] == "jane.safety@apexcorp.com"
    assert data["supervisor"]["name"] == "Jane Safety Officer"
    assert data["supervisor"]["role"] == "Chief Safety Inspector"
    assert data["supervisor"]["provider"] == "clerk"
    assert data["supervisor"]["authenticated"] is True

def test_plan_approval_records_verified_identity_over_a_typed_name(monkeypatch):
    import time
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    token = signed_clerk_token(monkeypatch, {"sub": "user_clerk_lead", "name": "Captain Safe", "email": "lead@safe.org", "exp": int(time.time()) + 3600})
    auth = {"authorization": f"Bearer {token}"}
    assert call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "clerk-auth-site", "name": "Clerk Auth Site"}, headers=auth)[0] == 201
    date = call("POST", "/api/sites/clerk-auth-site/plans/generate", {}, headers=auth)[1]["plan"]["date"]
    status, payload = call("POST", f"/api/sites/clerk-auth-site/plans/{date}/approve", {"approvedBy": "Someone Else"}, headers=auth)
    assert status == 200
    assert payload["plan"]["status"] == "APPROVED"
    assert payload["plan"]["approvedBy"] == "Captain Safe"


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
    created = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"latitude": 17.4, "longitude": 78.5, "siteId": site_id, "name": "Gmail Site", "emailRecipients": "Lead@Example.com, not-an-email, crew@example.com"})}, None)
    assert json.loads(created["body"])["site"]["emailRecipients"] == ["lead@example.com", "crew@example.com"]
    date = call("POST", f"/api/sites/{site_id}/plans/generate", {})[1]["plan"]["date"]
    assert call("POST", f"/api/sites/{site_id}/plans/{date}/approve", {})[0] == 200

    # The recipient list is returned to the supervisor who set it, never on the public dashboard.
    public_site = call("GET", f"/api/sites/{site_id}/status")[1]["site"]
    assert "emailRecipients" not in public_site and public_site["emailRecipientCount"] == 2

    send_res = handler({"rawPath": f"/api/sites/{site_id}/plans/{date}/send", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
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
    assert gmail.send_gmail_briefing(None, None, ["crew@example.com"], {}, "Site") == []


# ── Persistence ────────────────────────────────────────────────────────

class FakeTable:
    """An in-memory table that enforces DynamoDB's type rules (for example: no Python floats)."""
    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        from boto3.dynamodb.types import TypeSerializer
        TypeSerializer().serialize(Item)
        self.items[(Item["pk"], Item["sk"])] = Item

    def get_item(self, Key, **kwargs):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)

    def query(self, KeyConditionExpression, **kwargs):
        expression = KeyConditionExpression.get_expression()
        matches = lambda sk: True
        if expression["operator"] == "AND":
            pk_condition, sk_condition = expression["values"]
            sk_expression = sk_condition.get_expression()
            bound = sk_expression["values"][1]
            matches = {"begins_with": lambda sk: sk.startswith(bound), ">=": lambda sk: sk >= bound}[sk_expression["operator"]]
            expression = pk_condition.get_expression()
        pk = expression["values"][1]
        return {"Items": [item for (item_pk, sk), item in self.items.items() if item_pk == pk and matches(sk)]}

    def scan(self, **kwargs):
        return {"Items": [item for (_, sk), item in self.items.items() if sk == "META"]}


def test_site_and_plan_survive_a_cold_start(monkeypatch):
    import app
    table = FakeTable()
    monkeypatch.setattr(app, "_table", lambda: table)

    created = call("POST", "/api/sites", {"siteId": "durable", "name": "Durable Site", "latitude": 12.97, "longitude": 77.59, "defaultLanguage": "te", "workStartHour": 7, "telegramBotUsername": "DurableBot", "discordWebhookUrl": "https://discord.com/api/webhooks/1/abc", "emailRecipients": "crew@example.com"})
    assert created[0] == 201
    date = call("POST", "/api/sites/durable/plans/generate", {})[1]["plan"]["date"]
    assert call("POST", f"/api/sites/durable/plans/{date}/approve", {"approvedBy": "Asha"})[0] == 200
    assert ("SITE#durable", "META") in table.items and ("SITE#durable", f"PLAN#{date}") in table.items

    for store in (app.SITES, app.PLANS, app.WORKERS, app.ACKS, app.ALERTS): store.clear()  # a new Lambda container

    status, dashboard = call("GET", "/api/sites/durable/status")
    assert status == 200
    site = dashboard["site"]
    assert (site["siteId"], site["name"], site["latitude"], site["longitude"]) == ("durable", "Durable Site", 12.97, 77.59)
    assert (site["defaultLanguage"], site["workStartHour"], site["telegramBotUsername"]) == ("te", 7, "DurableBot")
    assert site["hasDiscordWebhook"] is True and site["emailRecipientCount"] == 1
    assert dashboard["canonicalPlan"]["status"] == "APPROVED"
    assert dashboard["canonicalPlan"]["approvedBy"] == "Asha"
    assert app.SITES["durable"].discord_webhook_url == "https://discord.com/api/webhooks/1/abc"
    assert app.SITES["durable"].email_recipients == ["crew@example.com"]

def test_scheduled_run_plans_every_stored_site(monkeypatch):
    import app
    table = FakeTable()
    monkeypatch.setattr(app, "_table", lambda: table)
    call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "site-a", "name": "A"})
    call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "site-b", "name": "B"})
    for store in (app.SITES, app.PLANS): store.clear()
    status, payload = call("POST", "/api/plan", {"scheduled": True})
    assert status == 200
    assert sorted(plan["site_id"] for plan in payload["plans"]) == ["site-a", "site-b"]


# ── Weather ────────────────────────────────────────────────────────────

def test_weather_outage_returns_503_instead_of_canned_numbers(monkeypatch):
    import app
    from core.forecast import WeatherUnavailable

    def down(*args, **kwargs): raise WeatherUnavailable("down")
    monkeypatch.setattr(app, "get_weather", down)
    call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "no-weather", "name": "No Weather"})
    assert call("POST", "/api/sites/no-weather/plans/generate", {})[0] == 503
    assert call("GET", "/api/sites/no-weather/status")[0] == 503
    assert not any(key[0] == "no-weather" for key in app.PLANS)

def test_live_fetch_failure_is_not_replaced_by_demo_data(monkeypatch):
    from urllib.error import URLError

    import pytest

    from core import forecast

    monkeypatch.setenv("HEATSHIELD_DEMO", "false")
    def offline(*args, **kwargs): raise URLError("offline")
    monkeypatch.setattr(forecast, "urlopen", offline)
    with pytest.raises(forecast.WeatherUnavailable):
        forecast.get_weather(SiteProfile("s", "S", 17, 78))

def test_demo_plan_is_labelled_as_demo_data():
    new_site_with_plan("labelled")
    dashboard = call("GET", "/api/sites/labelled/status")[1]
    assert dashboard["forecast"]["source"] == "Demo data"
    assert dashboard["canonicalPlan"]["weather_provider"] == "DEMO"

def test_site_timezone_follows_the_forecast_location(monkeypatch):
    import app
    monkeypatch.setattr(app, "get_weather", lambda *a, **k: {"hours": [{"time": "2026-10-10T12:00", "temperature": 30, "humidity": 50}], "timezone": "Europe/London", "provider": "OPEN_METEO"})
    call("POST", "/api/sites", {"siteId": "london", "name": "London", "latitude": 51.5, "longitude": -0.12})
    call("POST", "/api/sites/london/plans/generate", {})
    assert app.SITES["london"].timezone == "Europe/London"


# ── Dashboard safety numbers ───────────────────────────────────────────

def test_dashboard_controls_come_from_the_hottest_hour():
    new_site_with_plan("peak-site")
    dashboard = call("GET", "/api/sites/peak-site/status")[1]
    plan = dashboard["canonicalPlan"]
    peak_index = max(range(len(plan["hours"])), key=lambda i: plan["hours"][i]["forecast_heat_risk"])
    peak_block = plan["blocks"][peak_index]
    assert peak_block["level"] != plan["blocks"][0]["level"], "the canned day should get hotter than its first hour"
    assert dashboard["controls"]["restMinutesPerHour"] == peak_block["rest_minutes_per_hour"]
    assert dashboard["controls"]["waterEveryMinutes"] == peak_block["water_every_minutes"]
    assert dashboard["controls"]["restMinutesPerHour"] > 0
    assert f"{peak_block['rest_minutes_per_hour']} min per hour" in dashboard["plan"]["checklist"][1]
    assert dashboard["current"]["apparentTemperature"] is not None


# ── Input handling ─────────────────────────────────────────────────────

def test_bad_input_is_a_400_not_a_500():
    assert call("POST", "/api/sites", "{not json")[0] == 400
    assert call("POST", "/api/sites", "[1, 2]")[0] == 400
    for bad in ({"latitude": "north"}, {"latitude": 123}, {"workload": "EXTREME"}, {"siteId": "bad id!"}, {"timezone": "Mars/Olympus"},
                {"discordInviteUrl": "javascript:alert(1)"}, {"discordInviteUrl": "https://evil.example/invite"},
                {"discordWebhookUrl": "https://internal.example/api/webhooks/1"}, {"telegramBotUsername": "bad/name"}):
        status, payload = call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "name": "Bad", **bad})
        assert status == 400, bad
        assert "error" in payload

def test_unknown_site_is_404_and_is_not_created_by_reading_it():
    import app
    assert call("GET", "/api/sites/never-made/status")[0] == 404
    assert call("GET", "/api/sites/never-made/join")[0] == 404
    assert "never-made" not in app.SITES

def test_telegram_callback_without_an_action_is_ignored():
    assert call("POST", "/telegram/webhook", {"callback_query": {"data": "garbage", "from": {"id": 1}}})[0] == 200

def test_changing_channels_keeps_the_approved_plan():
    date = new_site_with_plan("channel-edit")
    call("POST", f"/api/sites/channel-edit/plans/{date}/approve", {})
    assert call("PUT", "/api/sites/channel-edit", {"telegramBotUsername": "NewCrewBot"})[0] == 200
    assert call("GET", "/api/sites/channel-edit/status")[1]["canonicalPlan"]["status"] == "APPROVED"

def test_past_weather_plan_cannot_be_sent_to_workers():
    call("POST", "/api/sites", {"latitude": 17.4, "longitude": 78.5, "siteId": "replay-site", "name": "Replay"})
    plan = call("POST", "/api/sites/replay-site/plans/generate", {"mode": "REPLAY", "replayDate": "2026-05-20"})[1]["plan"]
    assert plan["date"] == "2026-05-20"
    assert call("POST", "/api/sites/replay-site/plans/2026-05-20/approve", {})[0] == 200
    assert call("POST", "/api/sites/replay-site/plans/2026-05-20/send", {})[0] == 409

def test_alert_can_be_resolved_for_its_site():
    call("POST", "/api/sites", {"siteId": "alert-site", "name": "Alert", "latitude": 17.4, "longitude": 78.5})
    call("POST", "/telegram/webhook", {"callback_query": {"data": "unwell:alert-site-2026-10-10", "from": {"id": 77}}})
    status, payload = call("POST", "/api/alerts/77", {"siteId": "alert-site"})
    assert status == 200 and payload["alert"]["status"] == "RESOLVED"
    assert call("POST", "/api/alerts/does-not-exist", {})[0] == 404


# ── Webhook authenticity ───────────────────────────────────────────────

def test_webhooks_fail_closed_without_secrets_outside_open_access(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    assert call("POST", "/telegram/webhook", {"message": {"text": "/start demo", "chat": {"id": 5}}})[0] == 403
    assert call("POST", "/discord/interactions", {"type": 1})[0] == 401

def test_discord_interactions_require_a_valid_ed25519_signature(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    public_hex = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", public_hex)
    import time
    body, timestamp = json.dumps({"type": 1}), str(int(time.time()))
    signature = key.sign(timestamp.encode() + body.encode()).hex()

    good = {"x-signature-ed25519": signature, "x-signature-timestamp": timestamp}
    assert call("POST", "/discord/interactions", body, headers=good) == (200, {"type": 1})
    assert call("POST", "/discord/interactions", body, headers={**good, "x-signature-timestamp": str(int(timestamp) + 1)})[0] == 401
    # A correctly signed interaction captured earlier cannot be replayed.
    old = "1700000000"
    replayed = {"x-signature-ed25519": key.sign(old.encode() + body.encode()).hex(), "x-signature-timestamp": old}
    assert call("POST", "/discord/interactions", body, headers=replayed)[0] == 401
    assert call("POST", "/discord/interactions", body, headers={"x-signature-ed25519": "00" * 64, "x-signature-timestamp": timestamp})[0] == 401
    assert call("POST", "/discord/interactions", body)[0] == 401

def test_plan_that_reaches_nobody_is_not_marked_sent(monkeypatch):
    import app
    monkeypatch.setattr(app, "send_discord_webhook", lambda *args, **kwargs: False)
    date = new_site_with_plan("nobody-home")
    call("POST", f"/api/sites/nobody-home/plans/{date}/approve", {})
    assert call("POST", f"/api/sites/nobody-home/plans/{date}/send", {})[0] == 409
    assert call("GET", "/api/sites/nobody-home/status")[1]["canonicalPlan"]["status"] == "APPROVED"


# ── Waterlogging watch (all upstream APIs mocked; rainfall values below are synthetic) ──

def _hours(start, count):
    from datetime import timedelta
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(count)]

def _history_series(days=1000):
    """Synthetic record: mostly dry, a 2 mm hour every 4 days, and a 40 mm/8 h storm every 100 days."""
    series = [0.0] * (days * 24)
    for day in range(0, days, 4): series[day * 24 + 12] = 2.0
    for day in range(50, days, 100):
        for hour in range(8): series[day * 24 + hour] = 5.0
    return series

def mock_waterlogging(monkeypatch, recent_rain=None, forecast_rain=None, history_days=1000, fail=(), gdacs=None, live_gaps=0, metar=None, other_models=None):
    """Replaces every outbound request. `recent_rain` / `forecast_rain` are per-hour mm lists, newest last."""
    from datetime import UTC, datetime, timedelta

    from core import waterlogging
    calls = []
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    recent = ([0.0] * 48 + list(recent_rain or []))[-48:]
    if live_gaps: recent[-live_gaps:] = [None] * live_gaps
    future = (list(forecast_rain or []) + [0.0] * 48)[:47]
    live = {"hourly": {"time": _hours(now - timedelta(hours=47), 95), "precipitation": recent + future}}
    for name, last_hour in (other_models or {}).items():
        live["hourly"][f"precipitation_{name}"] = [0.0] * 47 + [last_hour] + [0.0] * 47
    history = {"hourly": {"precipitation": _history_series(history_days)}}

    def fake_fetch(url, source):
        calls.append(source)
        if source in fail:
            waterlogging._mark(source, False, "mocked outage")
            raise waterlogging.SourceError(f"{source}: mocked outage")
        waterlogging._mark(source, True)
        count = url.split("latitude=")[1].split("&")[0].count("%2C") + 1 if "latitude=" in url else 1
        if source == "open-meteo-forecast": return [live] * count
        if source == "open-meteo-history": return [history] * count
        if source == "aviation-weather-metar": return metar
        return {"features": gdacs or []}

    monkeypatch.setattr(waterlogging, "fetch_json", fake_fetch)
    return calls

def station_report(wx="", minutes_ago=20, raw=None, lat=17.45, lon=78.47, station="VOHS"):
    import time
    return {"icaoId": station, "name": "Test Airport", "obsTime": int(time.time()) - minutes_ago * 60, "lat": lat, "lon": lon, "wxString": wx or None, "rawOb": raw or f"METAR {station} 110900Z 01004KT 4000 {wx} FEW020 32/23 Q1011 NOSIG"}

def wl_point(lat=17.385, lon=78.487):
    status, payload = call("GET", "/api/waterlogging/point", query={"lat": str(lat), "lon": str(lon)})
    assert status == 200
    return payload["cells"][0]

def test_waterlogging_area_is_a_grid_around_the_site(monkeypatch):
    calls = mock_waterlogging(monkeypatch)
    status, payload = call("GET", "/api/waterlogging", query={"lat": "17.385", "lon": "78.487"})
    assert status == 200
    assert len(payload["cells"]) == 9 and payload["focusCellId"] == "17.4,78.5"
    assert {c["risk"] for c in payload["cells"]} == {"LOW"}
    assert all(c["kind"] == "ESTIMATE" for c in payload["cells"])
    # Nine cells cost one request per source, and a repeat within the cache window costs none.
    assert sorted(calls) == ["aviation-weather-metar", "gdacs", "open-meteo-forecast", "open-meteo-history"]
    call("GET", "/api/waterlogging", query={"lat": "17.385", "lon": "78.487"})
    assert len(calls) == 4

def test_waterlogging_levels_follow_the_cells_own_record(monkeypatch):
    from core import waterlogging
    mock_waterlogging(monkeypatch, recent_rain=[0.0])
    reference = wl_point()["reference"]
    thresholds = reference["accumulation24hMm"]
    assert thresholds["MODERATE"] < thresholds["HIGH"] <= thresholds["VERY_HIGH"]

    for level in ("HIGH", "VERY_HIGH"):
        waterlogging.reset_state()
        # Spread over 24 hours so only the accumulation, not the hourly intensity, is unusual.
        mock_waterlogging(monkeypatch, recent_rain=[thresholds[level] / 24 + 0.05] * 24)
        cell = wl_point()
        assert cell["risk"] == level, (level, cell["rainfall"], thresholds)
        assert cell["drivers"][0]["metric"] == "accumulation24h"
        assert cell["confidence"]["label"] == "low", "an uncalibrated rainfall estimate must not claim more than low confidence"
        assert any("not calibrated" in text for text in cell["limitations"])

def test_waterlogging_forecast_risk_is_separate_from_current_risk(monkeypatch):
    mock_waterlogging(monkeypatch, forecast_rain=[6.0] * 12)
    cell = wl_point()
    assert cell["risk"] == "LOW" and cell["forecastRisk"] in ("HIGH", "VERY_HIGH")

def test_waterlogging_light_rain_stays_low_even_where_rain_is_rare(monkeypatch):
    # 6 mm in a day is far above this synthetic record's usual day, but under the noise floor.
    mock_waterlogging(monkeypatch, recent_rain=[0.25] * 24)
    cell = wl_point()
    assert cell["rainfall"]["last24hMm"] == 6.0 and cell["risk"] == "LOW"

def test_waterlogging_reports_insufficient_data_instead_of_guessing(monkeypatch):
    from core import waterlogging
    heavy = [10.0] * 24

    mock_waterlogging(monkeypatch, recent_rain=heavy, history_days=200)
    cell = wl_point()
    assert cell["risk"] == "INSUFFICIENT_DATA" and cell["rainfall"]["last24hMm"] == 240.0
    assert cell["confidence"] == {"score": 0.0, "label": "none"}

    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, recent_rain=heavy, fail=("open-meteo-history",))
    assert wl_point()["risk"] == "INSUFFICIENT_DATA"

    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, recent_rain=heavy, fail=("open-meteo-forecast",))
    cell = wl_point()
    assert cell["risk"] == "INSUFFICIENT_DATA" and cell["rainfall"] is None

    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, recent_rain=heavy, live_gaps=8)
    cell = wl_point()
    assert cell["rainfall"] is None and cell["risk"] == "INSUFFICIENT_DATA", "hours with no data must not be read as zero rain"

def test_waterlogging_source_outage_is_visible(monkeypatch):
    mock_waterlogging(monkeypatch, fail=("gdacs",))
    status, payload = call("GET", "/api/waterlogging/point", query={"lat": "17.385", "lon": "78.487"})
    statuses = {s["id"]: s["status"] for s in payload["sources"]}
    assert statuses["gdacs"]["ok"] is False and statuses["open-meteo-forecast"]["ok"] is True
    assert any("GDACS" in text for text in payload["cells"][0]["limitations"])

def test_only_a_verified_report_confirms_waterlogging(monkeypatch):
    import time
    mock_waterlogging(monkeypatch)
    # Open-access (not signed in): the report is recorded as unverified and confirms nothing.
    status, payload = call("POST", "/api/waterlogging/reports", {"lat": 17.385, "lon": 78.487, "note": "water on the road"})
    assert status == 201 and payload["report"]["verified"] is False
    cell = wl_point()
    assert cell["risk"] == "LOW" and cell["kind"] == "ESTIMATE" and len(cell["unverifiedReports"]) == 1

    token = signed_clerk_token(monkeypatch, {"sub": "user_1", "name": "Asha Rao", "exp": int(time.time()) + 3600})
    status, payload = call("POST", "/api/waterlogging/reports", {"lat": 17.385, "lon": 78.487}, headers={"authorization": f"Bearer {token}"})
    assert status == 201 and payload["report"]["verified"] is True and payload["report"]["reportedBy"] == "Asha Rao"
    cell = wl_point()
    assert (cell["risk"], cell["kind"]) == ("CONFIRMED", "CONFIRMED")
    assert cell["confirmedReports"][0]["reportedBy"] == "Asha Rao"
    # A neighbouring cell is not confirmed by it.
    assert wl_point(17.385, 78.687)["risk"] == "LOW"

def test_waterlogging_report_needs_a_supervisor_when_access_is_closed(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    assert call("POST", "/api/waterlogging/reports", {"lat": 17.385, "lon": 78.487})[0] == 401

def test_official_flood_alert_is_shown_but_never_confirms(monkeypatch):
    alert = {"properties": {"eventid": 7, "name": "Flood in India", "alertlevel": "Red", "iscurrent": "true", "country": "India", "fromdate": "2026-10-01T00:00:00", "todate": "2026-10-11T00:00:00", "url": {"report": "https://www.gdacs.org/report.aspx?eventid=7"}}, "geometry": {"type": "Point", "coordinates": [78.6, 17.5]}}
    far = {"properties": {**alert["properties"], "eventid": 8}, "geometry": {"type": "Point", "coordinates": [-54.8, -25.5]}}
    mock_waterlogging(monkeypatch, recent_rain=[3.0] * 24, gdacs=[alert, far])
    cell = wl_point()
    assert [a["id"] for a in cell["nearbyAlerts"]] == ["gdacs-7"]
    assert cell["kind"] == "ESTIMATE" and cell["risk"] != "CONFIRMED"
    assert cell["confidence"]["label"] == "medium", "an independent official alert raises confidence, not the category"

def test_waterlogging_rejects_bad_coordinates():
    assert call("GET", "/api/waterlogging", query={"lat": "north", "lon": "1"})[0] == 400
    assert call("GET", "/api/waterlogging/point", query={"lat": "95", "lon": "1"})[0] == 400
    assert call("POST", "/api/waterlogging/reports", {"lat": 1})[0] == 400

def test_fetch_json_retries_transient_errors_then_gives_up(monkeypatch):
    import io
    from urllib.error import HTTPError

    import pytest

    from core import waterlogging
    attempts = []
    monkeypatch.setattr(waterlogging.time, "sleep", lambda seconds: None)

    def flaky(request, timeout):
        attempts.append(1)
        if len(attempts) < 2: raise HTTPError(request.full_url, 503, "busy", {}, io.BytesIO())
        return io.BytesIO(b'{"ok": true}')
    monkeypatch.setattr(waterlogging, "urlopen", flaky)
    assert waterlogging.fetch_json("https://example.org/x", "gdacs") == {"ok": True} and len(attempts) == 2

    attempts.clear()
    def missing(request, timeout):
        attempts.append(1); raise HTTPError(request.full_url, 404, "gone", {}, io.BytesIO())
    monkeypatch.setattr(waterlogging, "urlopen", missing)
    with pytest.raises(waterlogging.SourceError): waterlogging.fetch_json("https://example.org/x", "gdacs")
    assert len(attempts) == 1, "a permanent error is not retried"

def test_fetch_json_stops_at_its_request_budget(monkeypatch):
    import io

    import pytest

    from core import waterlogging
    monkeypatch.setenv("WL_MAX_UPSTREAM_PER_MIN", "3")
    monkeypatch.setattr(waterlogging, "urlopen", lambda request, timeout: io.BytesIO(b"{}"))
    for _ in range(3): waterlogging.fetch_json("https://example.org/x", "gdacs")
    with pytest.raises(waterlogging.SourceError, match="budget"): waterlogging.fetch_json("https://example.org/x", "gdacs")

def test_waterlogging_reference_record_is_kept_across_a_cold_start(monkeypatch):
    import app
    from core import waterlogging
    table = FakeTable()
    monkeypatch.setattr(app, "_table", lambda: table)
    calls = mock_waterlogging(monkeypatch)
    wl_point()
    assert ("WLCELL#17.4,78.5", "HISTORY") in table.items
    waterlogging.reset_state()  # a new container
    calls.clear()
    wl_point()
    assert "open-meteo-history" not in calls, "the multi-megabyte rainfall record should not be downloaded again"

def test_waterlogging_station_report_contradicts_modelled_rain(monkeypatch):
    # The model says it rained in the last hour; the airport 7 km away reported haze and no rain.
    mock_waterlogging(monkeypatch, recent_rain=[0.2], metar=[station_report("HZ"), station_report("HZ", minutes_ago=50)], other_models={"gfs_seamless": 0.0, "icon_seamless": 0.0})
    cell = wl_point()
    assert cell["rainfall"]["lastHourMm"] == 0.2 and cell["rainfall"]["lastHourCheck"] == "CONTRADICTED"
    assert cell["observation"]["stationId"] == "VOHS" and cell["observation"]["rainReported"] is False and cell["observation"]["reports"] == 2
    assert any("reported none" in text for text in cell["limitations"])
    assert any("2 of 3 show no rain" in text for text in cell["limitations"])
    assert cell["rainfall"]["modelSpread"]["lastHourMm"] == {"min": 0.0, "max": 0.2}
    assert cell["confidence"]["score"] < 0.25

def test_waterlogging_station_report_supports_or_corrects_the_model(monkeypatch):
    from core import waterlogging
    mock_waterlogging(monkeypatch, recent_rain=[3.0], metar=[station_report("-SHRA")])
    cell = wl_point()
    assert cell["rainfall"]["lastHourCheck"] == "SUPPORTED" and cell["observation"]["rainReported"] is True
    assert any("also reported rain" in text for text in cell["evidence"])
    assert cell["confidence"]["label"] == "low", "a station agreeing about rain must not raise confidence in the risk estimate"

    # Rain that ended before the latest report still counts (RERA), and a dry model is then the one in doubt.
    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, metar=[station_report(raw="METAR VOHS 110900Z 01004KT 6000 FEW020 32/23 Q1011 RERA NOSIG")])
    cell = wl_point()
    assert cell["rainfall"]["lastHourCheck"] == "CONTRADICTED" and any("may be too low" in text for text in cell["limitations"])

    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, metar=[station_report("HZ")])
    assert wl_point()["rainfall"]["lastHourCheck"] == "SUPPORTED"

def test_waterlogging_station_check_is_skipped_when_nothing_usable(monkeypatch):
    from core import waterlogging
    unusable = [
        None,                                                        # the source's empty answer
        [station_report("HZ", minutes_ago=200)],                     # too old
        [station_report("HZ", lat=19.0, lon=80.0)],                  # too far away
        [station_report("VCSH")] + [station_report(raw="METAR VOHS 110900Z AUTO 01004KT 10SM CLR 32/23 A2992 RMK AO1")],  # see below
    ]
    for reports in unusable[:3]:
        waterlogging.reset_state()
        mock_waterlogging(monkeypatch, recent_rain=[0.2], metar=reports)
        cell = wl_point()
        assert cell["observation"] is None and cell["rainfall"]["lastHourCheck"] == "UNCHECKED"
        assert any("could not be checked" in text for text in cell["limitations"])
    # Showers "in the vicinity" are not rain at the station, and a station without a rain sensor is ignored.
    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, recent_rain=[0.2], metar=unusable[3])
    cell = wl_point()
    assert cell["observation"]["reports"] == 1 and cell["observation"]["rainReported"] is False

    waterlogging.reset_state()
    mock_waterlogging(monkeypatch, recent_rain=[0.2], fail=("aviation-weather-metar",))
    cell = wl_point()
    assert cell["observation"] is None and cell["risk"] == "LOW"


# ── Access control, crew privacy and delivery ──────────────────────────

SITE = {"latitude": 17.4, "longitude": 78.5}


def clerk_auth(monkeypatch, key, **claims):
    """Bearer header for a Clerk user. Every user in a test must be signed by the same key."""
    import time

    import jwt
    return {"authorization": "Bearer " + jwt.encode({"exp": int(time.time()) + 3600, **claims}, key, algorithm="RS256")}


def clerk_key(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("CLERK_JWT_KEY", key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    return key


def test_staging_plans_needs_a_supervisor_but_the_schedule_still_runs(monkeypatch):
    call("POST", "/api/sites", {"siteId": "listed", "name": "Listed", **SITE})
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    assert call("POST", "/api/plan", {})[0] == 401
    assert call("POST", "/api/plan", {"scheduled": True})[0] == 401
    scheduled = handler({"source": "heatshield.scheduler", "rawPath": "/api/plan", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"scheduled": True})}, None)
    assert scheduled["statusCode"] == 200 and json.loads(scheduled["body"])["plans"]
    # The marker only counts on a direct invocation; a request that came through the API cannot claim it.
    through_api = {"source": "heatshield.scheduler", "rawPath": "/api/plan", "requestContext": {"domainName": "api.example.com", "http": {"method": "POST"}}, "body": "{}"}
    assert handler(through_api, None)["statusCode"] == 401


def test_staging_a_plan_for_an_unknown_site_is_404():
    assert call("POST", "/api/plan", {"siteId": "nope"})[0] == 404


def test_public_dashboard_hides_who_joined_and_who_is_unwell(monkeypatch):
    date = new_site_with_plan("private-crew")
    call("POST", "/telegram/webhook", {"message": {"text": "/start private-crew", "chat": {"id": 501}, "from": {"first_name": "Ravi"}}})
    call("POST", "/telegram/webhook", {"callback_query": {"data": f"unwell:private-crew-{date}", "from": {"id": 501}}})
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    monkeypatch.setenv("SUPERVISOR_KEY", "k")

    status, public = call("GET", "/api/sites/private-crew/status")
    assert status == 200
    assert public["workers"] == [] and public["alerts"] == [] and public["acknowledgements"] == []
    assert public["crewCounts"] == {"workers": 1, "acknowledgements": 0, "openAlerts": 1}
    assert "Ravi" not in json.dumps(public) and "501" not in json.dumps(public["crewCounts"])

    private = call("GET", "/api/sites/private-crew/status", headers={"x-api-key": "k"})[1]
    assert private["workers"][0]["name"] == "Ravi" and private["alerts"][0]["status"] == "OPEN"


def test_a_supervisor_cannot_read_or_change_another_organisations_site(monkeypatch):
    key = clerk_key(monkeypatch)
    asha = clerk_auth(monkeypatch, key, sub="user_asha", o={"id": "org_a", "rol": "admin"})
    colleague = clerk_auth(monkeypatch, key, sub="user_colleague", org_id="org_a")
    outsider = clerk_auth(monkeypatch, key, sub="user_outsider", o={"id": "org_b"})

    body = {"siteId": "org-a-site", "name": "A", "discordWebhookUrl": "https://discord.com/api/webhooks/1/secret", "emailRecipients": "crew@example.com", **SITE}
    assert call("POST", "/api/sites", body, headers=asha)[0] == 201

    assert call("PUT", "/api/sites/org-a-site", {}, headers=outsider)[0] == 403
    assert call("POST", "/api/sites/org-a-site/plans/generate", {}, headers=outsider)[0] == 403
    assert call("GET", "/api/sites", headers=outsider)[1]["sites"] == []
    assert call("GET", "/api/sites/org-a-site/status", headers=outsider)[1]["crewDetailsIncluded"] is False

    status, payload = call("PUT", "/api/sites/org-a-site", {"name": "A renamed"}, headers=colleague)
    assert status == 200 and payload["site"]["name"] == "A renamed"
    assert [s["siteId"] for s in call("GET", "/api/sites", headers=colleague)[1]["sites"]] == ["org-a-site"]


def test_creating_a_site_needs_coordinates_and_never_overwrites():
    assert call("POST", "/api/sites", {"siteId": "nowhere", "name": "Nowhere"})[0] == 400
    assert call("POST", "/api/sites", {"siteId": "once", "name": "First", **SITE})[0] == 201
    assert call("POST", "/api/sites", {"siteId": "once", "name": "Second", **SITE})[0] == 409
    assert call("GET", "/api/sites/once/status")[1]["site"]["name"] == "First"


def test_an_approved_plan_is_not_replaced_by_a_new_draft():
    date = new_site_with_plan("frozen")
    call("POST", f"/api/sites/frozen/plans/{date}/approve", {"approvedBy": "Asha"})
    assert call("POST", "/api/sites/frozen/plans/generate", {})[0] == 409
    plan = call("GET", "/api/sites/frozen/status")[1]["canonicalPlan"]
    assert plan["status"] == "APPROVED" and plan["approvedBy"] == "Asha"


def test_malformed_and_stale_plan_dates_are_refused(monkeypatch):
    date = new_site_with_plan("dated")
    assert call("POST", f"/api/sites/dated/plans/{date.replace('-', '')}/approve", {})[0] == 400
    assert call("POST", "/api/sites/dated/plans/2026-13-45/approve", {})[0] == 400
    assert call("POST", "/api/sites/dated/plans/generate", {"mode": "REPLAY", "replayDate": "2026-13-45"})[0] == 400
    assert call("GET", "/api/sites/dated/status")[1]["canonicalPlan"]["status"] == "DRAFT"

    import app
    old = {**app.PLANS[("dated", date)], "date": "2020-01-01", "plan_id": "dated-2020-01-01", "status": "APPROVED"}
    app.PLANS[("dated", "2020-01-01")] = old
    assert call("POST", "/api/sites/dated/plans/2020-01-01/send", {})[0] == 409


def test_bad_site_values_are_400_not_500():
    assert call("POST", "/api/sites", {"siteId": "bad1", "emailRecipients": 5, **SITE})[0] == 400
    assert call("POST", "/api/sites", {"siteId": "bad2", "workStartHour": 6.9, **SITE})[0] == 400
    result = handler({"rawPath": "/api/sites", "requestContext": {"http": {"method": "POST"}}, "body": '{"siteId": "bad3", "latitude": 17.4, "longitude": 78.5, "workStartHour": 1e999}'}, None)
    assert result["statusCode"] == 400
    assert handler({"rawPath": "/email/ack", "queryStringParameters": {"a": "ok", "site": "x", "plan": "x-1", "e": "a@b.co", "s": "é"}, "requestContext": {"http": {"method": "POST"}}}, None)["statusCode"] == 403


def test_site_update_accepts_snake_case_names():
    call("POST", "/api/sites", {"siteId": "snake", "name": "Snake", "workStartHour": 6, **SITE})
    assert call("PUT", "/api/sites/snake", {"work_start_hour": 9})[1]["site"]["workStartHour"] == 9


def test_hottest_hour_is_the_one_that_feels_hottest_when_scores_tie():
    new_site_with_plan("tied", workload="HEAVY", acclimatization="NEW_OR_UNKNOWN", clothing="HEAVY_OR_PPE")
    dashboard = call("GET", "/api/sites/tied/status")[1]
    scores = {h["time"]: h["risk"] for h in dashboard["forecast"]["hourly"]}
    assert scores["12:00"] == scores["14:00"] == 100
    assert dashboard["peak"]["time"] == "14:00", "14:00 is 40 C against 38 C at noon"


def test_forecast_with_a_gap_in_work_hours_makes_no_plan(monkeypatch):
    import app
    hours = [{"time": f"2026-10-10T{h:02d}:00", "temperature": 30, "humidity": 50} for h in (6, 7, 8)]
    monkeypatch.setattr(app, "get_weather", lambda *a, **k: {"hours": hours, "timezone": None, "provider": "OPEN_METEO", "missing": ["2026-10-10T13:00", "2026-10-10T14:00"]})
    call("POST", "/api/sites", {"siteId": "gappy", "name": "Gappy", **SITE})
    assert call("POST", "/api/sites/gappy/plans/generate", {})[0] == 503
    assert ("gappy", "2026-10-10") not in app.PLANS


def test_live_forecast_reports_hours_without_values(monkeypatch):
    import io

    from core import forecast
    payload = {"timezone": "Asia/Kolkata", "hourly": {"time": ["2026-10-10T12:00", "2026-10-10T13:00"], "temperature_2m": [30, None], "relative_humidity_2m": [50, 60]}}
    monkeypatch.setenv("HEATSHIELD_DEMO", "false")
    monkeypatch.setattr(forecast, "urlopen", lambda url, timeout: io.BytesIO(json.dumps(payload).encode()))
    result = forecast.get_weather(SiteProfile("s", "S", 17.4, 78.5))
    assert [h["time"] for h in result["hours"]] == ["2026-10-10T12:00"] and result["missing"] == ["2026-10-10T13:00"]


def test_demo_plan_message_says_it_is_demo_data():
    site = SiteProfile("s", "S", 17.4, 78.5)
    demo = generate_plan(site, [{"time": "2026-10-10T12:00", "temperature": 38, "humidity": 48}], provider="DEMO")
    live = generate_plan(site, [{"time": "2026-10-10T12:00", "temperature": 38, "humidity": 48}])
    assert compose_message(demo, "S").startswith("DEMO DATA")
    assert "DEMO DATA" not in compose_message(live, "S")


def test_check_ins_belong_to_todays_plan(monkeypatch):
    date = new_site_with_plan("daily")
    stale = {"type": 3, "data": {"custom_id": "ok:daily-2020-01-01"}, "user": {"id": "u1", "username": "Old"}}
    assert "earlier plan" in call("POST", "/discord/webhook", stale)[1]["data"]["content"]
    assert call("GET", "/api/sites/daily/status")[1]["acknowledgements"] == []

    call("POST", "/discord/webhook", {"type": 3, "data": {"custom_id": f"ok:daily-{date}"}, "user": {"id": "u1", "username": "Now"}})
    assert [a["planId"] for a in call("GET", "/api/sites/daily/status")[1]["acknowledgements"]] == [f"daily-{date}"]

    # Feeling unwell is recorded whichever message the button was on.
    call("POST", "/discord/webhook", {"type": 3, "data": {"custom_id": "unwell:daily-2020-01-01"}, "user": {"id": "u2", "username": "Ill"}})
    assert call("GET", "/api/sites/daily/status")[1]["crewCounts"]["openAlerts"] == 1


def test_unwell_report_is_pushed_to_the_supervisor_channel(monkeypatch):
    import app
    posted = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setattr(app, "send_slack_text", lambda url, text: posted.append(text) or True)
    date = new_site_with_plan("notify")
    reply = call("POST", "/discord/webhook", {"type": 3, "data": {"custom_id": f"unwell:notify-{date}"}, "user": {"id": "u9", "username": "Kiran"}})[1]
    assert "has been notified" in reply["data"]["content"]
    assert len(posted) == 1 and "unwell" in posted[0] and "Kiran" not in posted[0]


def test_joining_an_unknown_site_is_refused():
    import app
    assert call("POST", "/telegram/webhook", {"message": {"text": "/start", "chat": {"id": 9}}})[1]["joined"] is None
    assert call("POST", "/telegram/webhook", {"message": {"text": "/start no-such-site", "chat": {"id": 9}}})[1]["joined"] is None
    assert app.WORKERS == {}


def test_telegram_worker_gets_the_sites_language():
    import app
    call("POST", "/api/sites", {"siteId": "english", "name": "English", "defaultLanguage": "en", **SITE})
    call("POST", "/telegram/webhook", {"message": {"text": "/start english", "chat": {"id": 12}}})
    assert app.WORKERS["english"]["12"]["language"] == "en"


def test_partial_delivery_can_be_retried_without_repeating_anyone(monkeypatch):
    import app
    monkeypatch.setattr(app, "send_discord_webhook", lambda *a, **k: False)
    slack = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setattr(app, "send_slack_webhook", lambda *a, **k: slack.append(1) or True)
    telegram, working = [], {"up": False}
    monkeypatch.setattr(app, "_send_telegram", lambda chat_id, text, plan_id: working["up"] and (telegram.append(chat_id) or True))

    date = new_site_with_plan("retry")
    for chat in (1, 2):
        call("POST", "/telegram/webhook", {"message": {"text": "/start retry", "chat": {"id": chat}}})
    call("POST", f"/api/sites/retry/plans/{date}/approve", {})

    status, first = call("POST", f"/api/sites/retry/plans/{date}/send", {})
    assert status == 200 and first["plan"]["status"] == "SENT"
    assert first["channels"] == {"telegram": 0, "discord": 0, "slack": 1, "email": 0}
    assert first["failed"]["telegram"] == 2 and first["retryAvailable"] is True

    working["up"] = True
    status, second = call("POST", f"/api/sites/retry/plans/{date}/send", {})
    assert status == 200 and second["channels"]["telegram"] == 2 and second["retryAvailable"] is False
    assert sorted(telegram) == ["1", "2"] and len(slack) == 1, "Slack already had the plan and is not sent it twice"

    status, third = call("POST", f"/api/sites/retry/plans/{date}/send", {})
    assert status == 409 and "already been sent" in third["error"]
    assert len(telegram) == 2


def test_a_failed_save_is_reported_not_swallowed(monkeypatch):
    import app

    class BrokenTable(FakeTable):
        def put_item(self, Item): raise RuntimeError("throttled")
    monkeypatch.setattr(app, "_table", lambda: BrokenTable())
    assert call("POST", "/api/sites", {"siteId": "lost", "name": "Lost", **SITE})[0] == 503
    assert "lost" not in app.SITES


def test_old_check_ins_are_not_loaded_after_a_cold_start(monkeypatch):
    import app
    table = FakeTable()
    monkeypatch.setattr(app, "_table", lambda: table)
    date = new_site_with_plan("cold")
    table.put_item({"pk": "SITE#cold", "sk": "ACK#cold-2020-01-01#old", "chatId": "old", "planId": "cold-2020-01-01", "status": "ACKNOWLEDGED"})
    call("POST", "/discord/webhook", {"type": 3, "data": {"custom_id": f"ok:cold-{date}"}, "user": {"id": "new", "username": "New"}})
    for store in (app.SITES, app.PLANS, app.WORKERS, app.ACKS, app.ALERTS): store.clear()
    assert [a["chatId"] for a in call("GET", "/api/sites/cold/status")[1]["acknowledgements"]] == ["new"]


def test_waterlogging_gap_in_the_latest_hours_is_not_read_as_dry():
    from datetime import UTC, datetime

    from core import waterlogging
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    times = _hours(datetime(2026, 10, 9, 13, tzinfo=UTC), 24)
    assert waterlogging.summarise_live(times, [0.0] * 23 + [None], now) is None
    assert waterlogging.summarise_live(times, [None] + [0.0] * 23, now)["missingHoursLast24h"] == 1


def test_slack_briefing_uses_the_hottest_hour_and_labels_demo_data():
    from core.slack import build_slack_payload
    site = SiteProfile("s", "S", 17.4, 78.5)
    weather = [{"time": "2026-10-10T10:00", "temperature": 30, "humidity": 50}, {"time": "2026-10-10T14:00", "temperature": 40, "humidity": 44}]
    payload = build_slack_payload(generate_plan(site, weather, provider="DEMO"), "S")
    text = json.dumps(payload)
    assert "at 14:00" in text and "DEMO DATA" in text
    assert "DEMO DATA" not in json.dumps(build_slack_payload(generate_plan(site, weather), "S"))


def test_unsigned_and_wrong_algorithm_tokens_are_rejected(monkeypatch):
    import base64
    import hashlib
    import hmac as hmac_module
    import time
    key = clerk_key(monkeypatch)
    import os
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    claims = {"sub": "attacker", "exp": int(time.time()) + 3600}
    unsigned = f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."
    signing_input = f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}"
    mac = hmac_module.new(os.environ["CLERK_JWT_KEY"].encode(), signing_input.encode(), hashlib.sha256).digest()
    confused = f"{signing_input}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"
    for token in (unsigned, confused):
        assert call("GET", "/api/sites", headers={"authorization": f"Bearer {token}"})[0] == 401
    assert call("GET", "/api/sites", headers=clerk_auth(monkeypatch, key, sub="real"))[0] == 200


# ── Follow-ups: alert escalation and check-in reminders ────────────────

def _minutes_ago(minutes):
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _capture_follow_up_messages(monkeypatch):
    """Records every Telegram call and supervisor post made during a test."""
    import app
    telegram, supervisor = [], []
    monkeypatch.setattr(app, "_telegram_api", lambda method, payload: telegram.append((method, payload)) or True)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setattr(app, "send_slack_text", lambda url, text: supervisor.append(text) or True)
    monkeypatch.setattr(app, "send_slack_webhook", lambda *a, **k: True)
    monkeypatch.setattr(app, "send_discord_webhook", lambda *a, **k: False)
    return telegram, supervisor


def _crew(site_id, *chat_ids, language="en"):
    date = new_site_with_plan(site_id, defaultLanguage=language)
    for chat_id in chat_ids:
        call("POST", "/telegram/webhook", {"message": {"text": f"/start {site_id}", "chat": {"id": chat_id}, "from": {"first_name": f"W{chat_id}"}}})
    return date


def test_an_unhandled_alert_is_escalated_once_per_step(monkeypatch):
    import app
    telegram, supervisor = _capture_follow_up_messages(monkeypatch)
    date = _crew("esc", 1, 2, 3, language="hi")
    call("POST", "/telegram/webhook", {"callback_query": {"data": f"unwell:esc-{date}", "from": {"id": 1, "first_name": "Ravi"}}})
    telegram.clear(); supervisor.clear()

    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 0, "a fresh alert is not escalated yet"

    app.ALERTS["esc"]["1"]["at"] = _minutes_ago(12)
    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 1
    assert len(supervisor) == 1 and "12 minutes ago" in supervisor[0] and "Ravi" not in supervisor[0]
    to_worker = [p for m, p in telegram if p["chat_id"] == "1"]
    assert len(to_worker) == 1 and "छाँव" in to_worker[0]["text"], "first-aid steps go to the worker in the site's language"
    to_buddies = {p["chat_id"]: p["text"] for m, p in telegram if p["chat_id"] in ("2", "3")}
    assert set(to_buddies) == {"2", "3"} and all("Ravi" in text for text in to_buddies.values())

    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 0, "the same step is not repeated five minutes later"

    telegram.clear()
    app.ALERTS["esc"]["1"]["at"] = _minutes_ago(31)
    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 1
    assert len(supervisor) == 2 and "STILL OPEN" in supervisor[1]
    assert telegram == [], "the worker and the same co-workers are not messaged twice"
    assert app.ALERTS["esc"]["1"]["escalations"] == 2

    app.ALERTS["esc"]["1"]["at"] = _minutes_ago(300)
    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 0


def test_a_resolved_alert_is_never_escalated(monkeypatch):
    import app
    telegram, supervisor = _capture_follow_up_messages(monkeypatch)
    date = _crew("handled", 1, 2)
    call("POST", "/telegram/webhook", {"callback_query": {"data": f"unwell:handled-{date}", "from": {"id": 1}}})
    assert call("POST", "/api/alerts/1", {"siteId": "handled"})[0] == 200
    app.ALERTS["handled"]["1"]["at"] = _minutes_ago(45)
    supervisor.clear(); telegram.clear()
    assert call("POST", "/api/follow-ups/run", {})[1]["escalated"] == 0
    assert supervisor == [] and telegram == []


def test_workers_who_have_not_checked_in_are_reminded_once(monkeypatch):
    import app
    telegram, supervisor = _capture_follow_up_messages(monkeypatch)
    sent = []
    monkeypatch.setattr(app, "_send_telegram", lambda chat_id, text, plan_id: sent.append((chat_id, text)) or True)
    monkeypatch.setattr(app, "_zone", lambda site: __import__("zoneinfo").ZoneInfo("UTC"))
    date = _crew("remind", 1, 2, 3)
    app.SITES["remind"].work_end_hour = 23
    call("POST", f"/api/sites/remind/plans/{date}/approve", {})
    assert call("POST", f"/api/sites/remind/plans/{date}/send", {})[0] == 200
    call("POST", "/telegram/webhook", {"callback_query": {"data": f"ok:remind-{date}", "from": {"id": 1}}})
    sent.clear(); supervisor.clear()

    assert call("POST", "/api/follow-ups/run", {})[1]["reminded"] == 0, "nobody is chased straight after sending"

    record = app.DELIVERIES[("remind", date)]
    record["sentAt"] = _minutes_ago(35)
    assert call("POST", "/api/follow-ups/run", {})[1]["reminded"] == 2
    assert sorted(chat for chat, _ in sent) == ["2", "3"] and all("not checked in" in text for _, text in sent)
    assert all(text.startswith("DEMO DATA") for _, text in sent), "a reminder about a demo plan says so"
    assert supervisor == []

    sent.clear()
    assert call("POST", "/api/follow-ups/run", {})[1]["reminded"] == 0, "one reminder each, not one every five minutes"

    call("POST", "/telegram/webhook", {"callback_query": {"data": f"ok:remind-{date}", "from": {"id": 2}}})
    record["sentAt"] = _minutes_ago(65)
    result = call("POST", "/api/follow-ups/run", {})[1]
    assert result["sites"] == [{"siteId": "remind", "escalated": 0, "reminded": 0, "summarySent": True}]
    assert len(supervisor) == 1 and "1 of 3 workers" in supervisor[0] and "W3" not in supervisor[0]

    supervisor.clear()
    call("POST", "/api/follow-ups/run", {})
    assert supervisor == [], "the summary is sent once"


def test_a_full_crew_needs_no_summary_and_an_unsent_plan_is_not_chased(monkeypatch):
    import app
    telegram, supervisor = _capture_follow_up_messages(monkeypatch)
    sent = []
    monkeypatch.setattr(app, "_send_telegram", lambda chat_id, text, plan_id: sent.append(chat_id) or True)
    monkeypatch.setattr(app, "_zone", lambda site: __import__("zoneinfo").ZoneInfo("UTC"))
    date = _crew("quiet", 1)
    app.SITES["quiet"].work_end_hour = 23
    call("POST", f"/api/sites/quiet/plans/{date}/approve", {})
    assert call("POST", "/api/follow-ups/run", {})[1]["reminded"] == 0 and sent == [], "an approved plan that was never sent has nobody to chase"

    call("POST", f"/api/sites/quiet/plans/{date}/send", {})
    call("POST", "/telegram/webhook", {"callback_query": {"data": f"ok:quiet-{date}", "from": {"id": 1}}})
    app.DELIVERIES[("quiet", date)]["sentAt"] = _minutes_ago(90)
    sent.clear(); supervisor.clear()
    call("POST", "/api/follow-ups/run", {})
    assert sent == [] and supervisor == []


def test_follow_ups_need_a_supervisor_or_the_schedule(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_OPEN_ACCESS", "false")
    assert call("POST", "/api/follow-ups/run", {})[0] == 401
    scheduled = handler({"source": "heatshield.scheduler", "rawPath": "/api/follow-ups/run", "requestContext": {"http": {"method": "POST"}}, "body": "{}"}, None)
    assert scheduled["statusCode"] == 200


def test_follow_up_rules():
    from datetime import UTC, datetime, timedelta

    from core import followup
    now = datetime.now(UTC)
    at = lambda minutes: (now - timedelta(minutes=minutes)).isoformat()
    assert followup.due_stage({"status": "OPEN", "at": at(5)}, now) is None
    assert followup.due_stage({"status": "OPEN", "at": at(10)}, now) == 1
    assert followup.due_stage({"status": "OPEN", "at": at(40), "escalations": 0}, now) == 1, "steps are taken one at a time"
    assert followup.due_stage({"status": "OPEN", "at": at(40), "escalations": 1}, now) == 2
    assert followup.due_stage({"status": "RESOLVED", "at": at(40)}, now) is None
    assert followup.due_stage({"status": "OPEN", "at": "not a time"}, now) is None

    workers = [{"chatId": "ill"}, {"chatId": "away"}, {"chatId": "here"}, {"chatId": "dc", "channel": "discord"}]
    assert [w["chatId"] for w in followup.pick_buddies(workers, {"here"}, "ill", 3)] == ["here", "away"]
    for table in (followup.FIRST_AID, followup.BUDDY, followup.REMINDER, followup.UNNAMED):
        assert set(table) == {"en", "hi", "te"}

