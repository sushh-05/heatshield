import json
import os

from app import build_plan, handler, heat_score, risk_level


def test_heat_score_is_bounded():
    score, apparent = heat_score(40, 60)
    assert 0 <= score <= 100
    assert apparent > 40


def test_risk_bands():
    assert risk_level(20) == "low"
    assert risk_level(50) == "moderate"
    assert risk_level(75) == "high"
    assert risk_level(95) == "extreme"


def test_plan_localizes_telugu(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_DEMO", "true")
    plan = handler({"rawPath": "/api/plan", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"language": "te"})}, None)
    assert plan["statusCode"] == 200
    assert "నీరు" in json.loads(plan["body"])["plan"]["checklist"][1]

