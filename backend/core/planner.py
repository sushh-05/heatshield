from datetime import datetime, timezone
from .heat import forecast_heat_risk, risk_level
from .models import HeatPlan, HourlyRisk, SiteProfile, WorkBlock

DISCLAIMER = "Forecast-based guidance only. Not measured WBGT or medical advice. Follow site safety rules and supervisor instructions."

def _controls(level: str):
    return {"LOW": (0, 30, "NORMAL"), "MODERATE": (10, 25, "LIMIT_HEAVY"), "HIGH": (20, 20, "AVOID_HEAVY"), "EXTREME": (30, 15, "STOP_HEAVY")}[level]

def generate_plan(site: SiteProfile, weather: list[dict], mode="LIVE", replay_date=None) -> HeatPlan:
    hours = []
    for item in weather:
        hour = int(item["time"][11:13]) if "T" in item["time"] else int(item["time"][:2])
        if not site.work_start_hour <= hour <= site.work_end_hour: continue
        score, apparent, reasons = forecast_heat_risk(item["temperature"], item["humidity"], site)
        hours.append(HourlyRisk(item["time"][-5:], item["temperature"], item["humidity"], apparent, score, risk_level(score), reasons))
    blocks = []
    for item in hours:
        rest, water, allowed = _controls(item.level)
        blocks.append(WorkBlock(item.time, item.time, item.level, allowed, rest, water))
    date = (replay_date or datetime.now().strftime("%Y-%m-%d"))
    return HeatPlan("1.0", f"{site.site_id}-{date}", site.site_id, date, datetime.now(timezone.utc).isoformat(), mode, "OPEN_METEO", "FORECAST_HEAT_RISK_V1", False, "DRAFT", hours, blocks, None, None, DISCLAIMER)
