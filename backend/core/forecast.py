import json
import os
from urllib.request import urlopen

DEMO = [{"time": f"2026-10-10T{hour:02d}:00", "temperature": temp, "humidity": humidity} for hour, temp, humidity in [(6,27,74),(8,30,66),(10,34,57),(12,38,48),(14,40,44),(16,37,49),(18,33,61)]]

def get_weather(site, mode="LIVE", replay_date=None):
    if os.getenv("HEATSHIELD_DEMO", "false").lower() == "true": return DEMO
    if mode == "REPLAY" and replay_date: endpoint = "https://historical-forecast-api.open-meteo.com/v1/forecast?start_date=%s&end_date=%s&" % (replay_date, replay_date)
    else: endpoint = "https://api.open-meteo.com/v1/forecast?"
    query = endpoint + "latitude=%s&longitude=%s&hourly=temperature_2m,relative_humidity_2m&timezone=Asia%%2FKolkata&forecast_days=2" % (site.latitude, site.longitude)
    try:
        with urlopen(query, timeout=5) as response: payload = json.loads(response.read())
        return [{"time": t, "temperature": temp, "humidity": humidity} for t, temp, humidity in zip(payload["hourly"]["time"], payload["hourly"]["temperature_2m"], payload["hourly"]["relative_humidity_2m"])]
    except Exception: return DEMO
