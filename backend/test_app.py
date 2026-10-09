import json
from app import handler
from core.heat import forecast_heat_risk, risk_level
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
