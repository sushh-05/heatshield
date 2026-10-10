from .models import RiskLevel, SiteProfile


def risk_level(score: int) -> RiskLevel:
    if score >= 80: return "EXTREME"
    if score >= 60: return "HIGH"
    if score >= 35: return "MODERATE"
    return "LOW"

def forecast_heat_risk(temperature_c: float, humidity_pct: float, site: SiteProfile, wind_speed_kmh: float | None = None, direct_normal_irradiance_wm2: float | None = None) -> tuple[int, float, list[str]]:
    """Explainable forecast estimate; never represents measured WBGT."""
    apparent = temperature_c + 0.33 * (humidity_pct / 100 * 6.105 * 2.71828 ** ((17.27 * temperature_c) / (237.7 + temperature_c))) - 4
    score = max(0, min(100, round((apparent - 20) * 4.2)))
    reasons = [f"Feels like approximately {apparent:.1f}°C"]
    if humidity_pct >= 70: reasons.append("High humidity reduces sweat evaporation")
    if site.workload == "HEAVY": score = min(100, score + 8); reasons.append("Heavy workload increases exposure")
    if site.acclimatization == "NEW_OR_UNKNOWN": score = min(100, score + 8); reasons.append("New or unknown acclimatization needs extra caution")
    if site.clothing == "HEAVY_OR_PPE": score = min(100, score + 5); reasons.append("Heavy clothing or PPE reduces heat release")
    if direct_normal_irradiance_wm2 is not None and direct_normal_irradiance_wm2 >= 600: score = min(100, score + 8); reasons.append("Strong direct solar radiation increases heat load")
    if wind_speed_kmh is not None and wind_speed_kmh < 5: score = min(100, score + 3); reasons.append("Low wind reduces cooling")
    elif wind_speed_kmh is not None and wind_speed_kmh >= 20: score = max(0, score - 3); reasons.append("Higher wind supports evaporative cooling")
    return score, round(apparent, 1), reasons
