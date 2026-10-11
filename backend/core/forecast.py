import json
import os
import re
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

DEMO = [{"time": f"2026-10-10T{hour:02d}:00", "temperature": temp, "humidity": humidity, "wind_speed": wind, "radiation": radiation} for hour, temp, humidity, wind, radiation in [(6,27,74,4,80),(8,30,66,6,280),(10,34,57,8,520),(12,38,48,5,720),(14,40,44,4,760),(16,37,49,7,560),(18,33,61,10,180)]]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WeatherUnavailable(Exception):
    """The forecast could not be fetched; callers must not substitute made-up numbers."""


def demo_mode() -> bool:
    return os.getenv("HEATSHIELD_DEMO", "false").lower() == "true"


def get_weather(site, mode="LIVE", replay_date=None):
    """Hourly forecast for a site as {"hours", "timezone", "provider"}.

    Hours are in the local time of the site's coordinates; "timezone" is the zone
    Open-Meteo resolved for them. Canned data is served only in explicit demo mode.
    """
    if demo_mode(): return {"hours": DEMO, "timezone": None, "provider": "DEMO", "missing": []}
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,direct_normal_irradiance",
        "timezone": "auto",
    }
    if mode == "REPLAY" and replay_date:
        if not _DATE_RE.match(str(replay_date)):
            raise ValueError("replayDate must be YYYY-MM-DD")
        endpoint = "https://historical-forecast-api.open-meteo.com/v1/forecast"
        params.update(start_date=replay_date, end_date=replay_date)
    else:
        endpoint = "https://api.open-meteo.com/v1/forecast"
        params["forecast_days"] = 2
    try:
        with urlopen(f"{endpoint}?{urlencode(params)}", timeout=5) as response: payload = json.loads(response.read())  # nosec B310
        hourly = payload["hourly"]
        times = hourly["time"]
        hours = [{"time": t, "temperature": temp, "humidity": humidity, "wind_speed": wind, "radiation": radiation} for t, temp, humidity, wind, radiation in zip(times, hourly["temperature_2m"], hourly["relative_humidity_2m"], hourly.get("wind_speed_10m", [None] * len(times)), hourly.get("direct_normal_irradiance", [None] * len(times))) if temp is not None and humidity is not None]
        # Hours with no temperature or humidity are reported, not silently dropped: a gap is not a cool hour.
        missing = [t for t, temp, humidity in zip(times, hourly["temperature_2m"], hourly["relative_humidity_2m"]) if temp is None or humidity is None]
        return {"hours": hours, "timezone": payload.get("timezone"), "provider": "OPEN_METEO", "missing": missing}
    except (URLError, TimeoutError, OSError, HTTPException, json.JSONDecodeError, KeyError, TypeError) as e:
        raise WeatherUnavailable(f"Open-Meteo forecast unavailable: {type(e).__name__}") from e
