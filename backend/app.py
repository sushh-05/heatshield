"""HeatShield API and scheduled morning-plan worker."""
import json
import os
from datetime import datetime, timezone
from urllib.request import urlopen

try:
    import boto3
except ImportError:  # pragma: no cover - local tests can run without AWS SDK
    boto3 = None

DEMO_HOURLY = [
    {"time": "06:00", "temperature": 27, "humidity": 74, "risk": 22},
    {"time": "08:00", "temperature": 30, "humidity": 66, "risk": 38},
    {"time": "10:00", "temperature": 34, "humidity": 57, "risk": 61},
    {"time": "12:00", "temperature": 38, "humidity": 48, "risk": 84},
    {"time": "14:00", "temperature": 40, "humidity": 44, "risk": 91},
    {"time": "16:00", "temperature": 37, "humidity": 49, "risk": 76},
    {"time": "18:00", "temperature": 33, "humidity": 61, "risk": 52},
]


def risk_level(score):
    if score >= 80:
        return "extreme"
    if score >= 60:
        return "high"
    if score >= 35:
        return "moderate"
    return "low"


def heat_score(temperature, humidity):
    """A transparent demo score, intentionally easy to explain to judges."""
    apparent = temperature + 0.33 * (humidity / 100 * 6.105 * 2.71828 ** ((17.27 * temperature) / (237.7 + temperature))) - 4
    score = round(max(0, min(100, (apparent - 20) * 4.2)))
    return score, round(apparent, 1)


def demo_forecast():
    return {"location": "Hyderabad, India", "hourly": DEMO_HOURLY, "source": "demo forecast"}


def fetch_forecast(latitude=17.385, longitude=78.487):
    if os.getenv("HEATSHIELD_DEMO", "false").lower() == "true":
        return demo_forecast()
    try:
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&hourly=temperature_2m,relative_humidity_2m&forecast_days=1&timezone=Asia%%2FKolkata") % (latitude, longitude)
        with urlopen(url, timeout=5) as response:
            payload = json.loads(response.read())
        hourly = []
        for time, temp, humidity in zip(payload["hourly"]["time"], payload["hourly"]["temperature_2m"], payload["hourly"]["relative_humidity_2m"]):
            score, _ = heat_score(temp, humidity)
            hourly.append({"time": time[11:16], "temperature": round(temp), "humidity": round(humidity), "risk": score})
        return {"location": "Your saved location", "hourly": hourly, "source": "Open-Meteo"}
    except Exception:
        return demo_forecast()


def build_plan(forecast, language="en"):
    peak = max(forecast["hourly"], key=lambda item: item["risk"])
    level = risk_level(peak["risk"])
    localized = {
        "en": {"title": "HeatShield morning plan", "pause": "Move heavy work before 10:00 or after 17:00.", "water": "Drink water every 20 minutes; do not wait until thirsty.", "buddy": "Use a buddy check and stop immediately for dizziness, confusion, or nausea."},
        "hi": {"title": "हीटशील्ड सुबह की योजना", "pause": "भारी काम 10 बजे से पहले या शाम 5 बजे के बाद करें।", "water": "हर 20 मिनट में पानी पिएं; प्यास लगने का इंतज़ार न करें।", "buddy": "साथी के साथ जांच करते रहें और चक्कर, उलझन या मतली पर तुरंत रुकें।"},
        "te": {"title": "హీట్‌షీల్డ్ ఉదయపు ప్రణాళిక", "pause": "భారీ పనిని ఉదయం 10 లోపు లేదా సాయంత్రం 5 తర్వాత చేయండి.", "water": "ప్రతి 20 నిమిషాలకు నీరు తాగండి; దాహం వేసే వరకు ఆగవద్దు.", "buddy": "జోడీగా ఒకరినొకరు గమనించండి; తల తిరగడం, గందరగోళం లేదా వాంతి అనిపిస్తే వెంటనే ఆపండి."},
    }.get(language, None) or {}
    title = localized.get("title", "HeatShield morning plan")
    checklist = [localized.get("pause", "Schedule shade breaks and move heavy work outside the peak."), localized.get("water", "Drink water regularly."), localized.get("buddy", "Use a buddy check for symptoms.")]
    plan = {"title": title, "level": level, "peak": peak, "message": "Peak risk is %s at %s (%s/100)." % (level, peak["time"], peak["risk"]), "checklist": checklist, "generated_by": "template fallback"}
    if os.getenv("USE_BEDROCK", "false").lower() == "true":
        wording = bedrock_wording(plan, language)
        if wording:
            plan["message"] = wording
            plan["generated_by"] = "Amazon Bedrock"
    return plan


def bedrock_wording(plan, language):
    """Ask Bedrock for a concise sentence, while preserving the deterministic plan."""
    if not boto3:
        return None
    try:
        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        prompt = "Rewrite this heat-safety alert in %s, under 18 words. Keep the time and risk score exact: %s" % (language, plan["message"])
        result = client.converse(modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"), messages=[{"role": "user", "content": [{"text": prompt}]}], inferenceConfig={"maxTokens": 80, "temperature": 0.2})
        return result["output"]["message"]["content"][0]["text"].strip()
    except Exception:
        return None


def persist_plan(plan):
    if not boto3 or not os.getenv("TABLE_NAME"):
        return
    try:
        boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]).put_item(Item={"pk": "PLAN", "sk": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "plan": plan})
    except Exception:
        pass


def send_telegram(plan):
    """Optional Telegram delivery for the EventBridge morning run."""
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        endpoint = "https://api.telegram.org/bot%s/sendMessage" % token
        payload = json.dumps({"chat_id": chat_id, "text": "%s\n\n%s\n%s" % (plan["title"], plan["message"], "\n".join("• " + item for item in plan["checklist"]))}).encode()
        request = __import__("urllib.request", fromlist=["Request"]).Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5):
            return True
    except Exception:
        return False


def dashboard(language="en"):
    forecast = fetch_forecast()
    peak = max(forecast["hourly"], key=lambda item: item["risk"])
    return {"forecast": forecast, "current": forecast["hourly"][0], "peak": peak, "level": risk_level(peak["risk"]), "plan": build_plan(forecast, language), "updated_at": datetime.now(timezone.utc).isoformat()}


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}, "body": json.dumps(body)}


def handler(event, context):
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET"))
    if path.endswith("/health"):
        return response(200, {"ok": True, "service": "heatshield-api"})
    if method == "GET" and path.endswith("/dashboard"):
        return response(200, dashboard())
    if method == "POST" and path.endswith("/plan"):
        raw = event.get("body") or "{}"
        body = json.loads(raw) if isinstance(raw, str) else raw
        plan = build_plan(fetch_forecast(), body.get("language", "en"))
        persist_plan(plan)
        delivered = send_telegram(plan) if body.get("scheduled", False) else False
        return response(200, {"plan": plan, "scheduled": body.get("scheduled", False), "telegram_delivered": delivered})
    return response(404, {"error": "route not found"})
