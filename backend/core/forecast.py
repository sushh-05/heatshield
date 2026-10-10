import json
import os
from urllib.request import urlopen

DEMO = [{"time": f"2026-10-10T{hour:02d}:00", "temperature": temp, "humidity": humidity, "wind_speed": wind, "radiation": radiation} for hour, temp, humidity, wind, radiation in [(6,27,74,4,80),(8,30,66,6,280),(10,34,57,8,520),(12,38,48,5,720),(14,40,44,4,760),(16,37,49,7,560),(18,33,61,10,180)]]

def get_weather(site, mode="LIVE", replay_date=None):
    if os.getenv("HEATSHIELD_DEMO", "false").lower() == "true": return DEMO
    if mode == "REPLAY" and replay_date: endpoint = "https://historical-forecast-api.open-meteo.com/v1/forecast?start_date=%s&end_date=%s&" % (replay_date, replay_date)
    else: endpoint = "https://api.open-meteo.com/v1/forecast?"
    query = endpoint + "latitude=%s&longitude=%s&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,direct_normal_irradiance&timezone=Asia%%2FKolkata" % (site.latitude, site.longitude)
    if mode != "REPLAY": query += "&forecast_days=2"
    try:
        with urlopen(query, timeout=5) as response: payload = json.loads(response.read())
        hourly = payload["hourly"]
        return [{"time": t, "temperature": temp, "humidity": humidity, "wind_speed": wind, "radiation": radiation} for t, temp, humidity, wind, radiation in zip(hourly["time"], hourly["temperature_2m"], hourly["relative_humidity_2m"], hourly.get("wind_speed_10m", [None] * len(hourly["time"])), hourly.get("direct_normal_irradiance", [None] * len(hourly["time"]))) ]
    except Exception: return DEMO
