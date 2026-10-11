"""Waterlogging watch: rainfall-based risk estimates for grid cells anywhere in the world.

What this module can and cannot say:

* Rainfall comes from Open-Meteo weather models. It is a model estimate on a
  roughly 10 km grid, not a rain gauge and not a street-level measurement.
* Risk is an ESTIMATE of how unusual the current and forecast rain is for that
  grid cell, judged against the cell's own rainfall record. It is not calibrated
  against local waterlogging incidents, and it never states that a place is flooded.
* "Confirmed" is used only for a report submitted by a signed-in (verified) supervisor.
  Reports made without sign-in are listed as unverified and change nothing.
* GDACS flood events are shown as official alerts in the wider region. They are
  country/region scale and never turn a cell into "confirmed".
* Missing rainfall or too short a record yields INSUFFICIENT_DATA, never a guess.
* Modelled rain is checked against two things before it is shown: other weather
  models, and the nearest airport weather station (METAR). A station that reported
  no rain contradicts a model that shows some, and the result says so.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("heatshield.waterlogging")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORY_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
METAR_URL = "https://aviationweather.gov/api/data/metar"
# The first model is the one the risk levels use (it matches the reference record); the rest are a cross-check.
PRIMARY_MODEL = "best_match"
CROSS_CHECK_MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless")
# METAR precipitation codes: drizzle, rain, snow, snow grains, ice pellets, hail, small hail, unknown precipitation.
PRECIPITATION_CODES = {"DZ", "RA", "SN", "SG", "PL", "GR", "GS", "UP"}
USER_AGENT = "HeatShield-WaterloggingWatch/0.1 (non-commercial)"

LEVELS = ("LOW", "MODERATE", "HIGH", "VERY_HIGH")


def _env_float(name: str, default: float) -> float:
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return default


def config() -> dict:
    """Every tunable of the risk model. Defaults are starting points, not validated thresholds:
    each deployment should calibrate them against local incident records (see docs/WATERLOGGING.md)."""
    return {
        # Cell size in degrees. 0.1 degree is about 11 km, close to the grid of the global models
        # Open-Meteo serves; a finer grid would only repeat the same model value under different labels.
        "grid_degrees": 0.1,
        # A level is reached when rain is at or above this share of the cell's own past hours.
        # 0.95 for the 24-hour total means "wetter than 95% of all 24-hour periods on record here".
        "accumulation_percentiles": {"MODERATE": _env_float("WL_ACC_P_MODERATE", 0.95), "HIGH": _env_float("WL_ACC_P_HIGH", 0.99), "VERY_HIGH": _env_float("WL_ACC_P_VERY_HIGH", 0.999)},
        "intensity_percentiles": {"MODERATE": _env_float("WL_INT_P_MODERATE", 0.99), "HIGH": _env_float("WL_INT_P_HIGH", 0.999), "VERY_HIGH": _env_float("WL_INT_P_VERY_HIGH", 0.9999)},
        # Noise floors, not flood thresholds: below these amounts rain is never rated above LOW,
        # so that a dry climate's "unusual" drizzle is not reported as a risk.
        "min_accumulation_mm": _env_float("WL_MIN_ACC_MM", 10.0),
        "min_intensity_mm_per_hour": _env_float("WL_MIN_INT_MM", 5.0),
        "wet_hour_mm": 0.1,
        # The rainfall record used as each cell's reference.
        "history_start": os.getenv("WL_HISTORY_START", "2022-07-01"),
        "min_history_days": int(_env_float("WL_MIN_HISTORY_DAYS", 730)),
        # Caching and politeness towards the free APIs.
        "live_ttl_seconds": int(_env_float("WL_LIVE_TTL", 600)),
        "history_ttl_seconds": int(_env_float("WL_HISTORY_TTL", 30 * 86400)),
        "alerts_ttl_seconds": int(_env_float("WL_ALERTS_TTL", 1800)),
        "max_upstream_calls_per_minute": int(_env_float("WL_MAX_UPSTREAM_PER_MIN", 20)),
        "request_timeout_seconds": 20,
        "retries": 2,
        # Evidence windows.
        "stale_after_minutes": 90,
        "report_active_hours": _env_float("WL_REPORT_ACTIVE_HOURS", 6.0),
        "alert_radius_km": _env_float("WL_ALERT_RADIUS_KM", 150.0),
        # Station cross-check: how far away a station may be, and how old its latest report may be.
        "station_max_km": _env_float("WL_STATION_MAX_KM", 30.0),
        "station_window_minutes": 75,
    }


SOURCES = {
    "open-meteo-forecast": {
        "name": "Open-Meteo Weather Forecast API", "url": "https://open-meteo.com/en/docs",
        "provides": "Hourly precipitation for the past 2 days and next 2 days, from weather models.",
        "observes": "Model estimate of rainfall. Not a gauge, not a flood observation.",
        "licence": "CC BY 4.0; free tier is for non-commercial use only", "attribution": "Weather data by Open-Meteo.com",
        "key_required": False, "limits": "600 calls/minute, 5,000/hour, 10,000/day (provider terms, checked 2026-10-11)",
    },
    "open-meteo-history": {
        "name": "Open-Meteo Historical Forecast API", "url": "https://open-meteo.com/en/docs/historical-forecast-api",
        "provides": "Archived hourly precipitation from the same models, from about 2022, used as each cell's reference record.",
        "observes": "Model estimate of past rainfall. The provider notes model versions change over time, so it is not a consistent long-term climate record.",
        "licence": "CC BY 4.0; free tier is for non-commercial use only", "attribution": "Weather data by Open-Meteo.com",
        "key_required": False, "limits": "Shares the Open-Meteo free limits; large requests may count as several calls (not verified)",
    },
    "aviation-weather-metar": {
        "name": "Airport weather stations (METAR)", "url": "https://aviationweather.gov/data/api/",
        "provides": "Routine weather reports from airport stations, including whether rain was observed, usually every 30 to 60 minutes.",
        "observes": "A direct observation at one airport. It says whether rain fell there, rarely how much, and nothing about places away from the station.",
        "licence": "US government (NOAA/NWS Aviation Weather Center) public data", "attribution": "Station reports: NOAA Aviation Weather Center",
        "key_required": False, "limits": "No key. This app asks at most once per 10 minutes per area; the provider's published rate limit was not re-checked",
    },
    "gdacs": {
        "name": "GDACS flood events", "url": "https://www.gdacs.org/gdacsapi/swagger/index.html",
        "provides": "Current significant flood events worldwide with an alert level and a single reference point.",
        "observes": "Authoritative event-level alerts for large disasters. Region scale; says nothing about a particular street.",
        "licence": "Provided 'as is' for information; not a substitute for official local warnings. No licence text is published on the terms page.", "attribution": "Global Disaster Alert and Coordination System (GDACS)",
        "key_required": False, "limits": "No request limits are published; this app asks at most once per 30 minutes",
    },
    "supervisor-reports": {
        "name": "Supervisor reports", "url": "",
        "provides": "Waterlogging seen on the ground, submitted by a signed-in supervisor.",
        "observes": "Direct human observation. The only source that produces 'confirmed'.",
        "licence": "Your own data", "attribution": "", "key_required": False, "limits": "",
    },
}


class SourceError(Exception):
    """An upstream source could not be read; the caller reports the gap instead of guessing."""


# ── Upstream access: caching, a call budget, retries ───────────────────

_cache: dict[str, tuple[float, object]] = {}
_source_status: dict[str, dict] = {}
_call_times: list[float] = []


def reset_state() -> None:
    """Forget cached data and call history (used by tests)."""
    _cache.clear(); _source_status.clear(); _call_times.clear()


def _cache_get(key: str):
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def _cache_put(key: str, value, ttl: float) -> None:
    _cache[key] = (time.time() + ttl, value)


def _mark(source: str, ok: bool, detail: str = "") -> None:
    now = datetime.now(UTC).isoformat()
    previous = _source_status.get(source, {})
    _source_status[source] = {"ok": ok, "checkedAt": now, "detail": detail, "lastSuccessAt": now if ok else previous.get("lastSuccessAt")}


def fetch_json(url: str, source: str):
    """GET a JSON document with a timeout, bounded retries with exponential backoff, and a per-minute call budget."""
    cfg = config()
    now = time.time()
    _call_times[:] = [t for t in _call_times if now - t < 60]
    if len(_call_times) >= cfg["max_upstream_calls_per_minute"]:
        _mark(source, False, "paused: this server reached its own per-minute request budget")
        raise SourceError(f"{source}: request budget reached, try again in a minute")
    last_error = "unknown error"
    for attempt in range(cfg["retries"] + 1):
        _call_times.append(time.time())
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=cfg["request_timeout_seconds"]) as response:  # nosec B310
                body = response.read()
            # Some sources answer "nothing here" with an empty body rather than an empty document.
            payload = json.loads(body) if body.strip() else None
            _mark(source, True)
            return payload
        except HTTPError as e:
            last_error = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504): break
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            last_error = type(e).__name__
        if attempt < cfg["retries"]: time.sleep(0.5 * 2 ** attempt)
    _mark(source, False, last_error)
    raise SourceError(f"{source}: {last_error}")


# ── Grid ───────────────────────────────────────────────────────────────

def cell_for(lat: float, lon: float) -> dict:
    """The grid cell containing a point: its id, centre and bounds."""
    size = config()["grid_degrees"]
    centre_lat = round(round(lat / size) * size, 4)
    centre_lon = round(round(lon / size) * size, 4)
    half = size / 2
    return {"id": f"{centre_lat:.1f},{centre_lon:.1f}", "lat": centre_lat, "lon": centre_lon, "bounds": [[round(centre_lat - half, 4), round(centre_lon - half, 4)], [round(centre_lat + half, 4), round(centre_lon + half, 4)]]}


def neighbourhood(lat: float, lon: float) -> list[dict]:
    """The cell containing the point plus its eight neighbours (about 33 km across)."""
    size = config()["grid_degrees"]
    centre = cell_for(lat, lon)
    cells = []
    for dlat in (1, 0, -1):
        for dlon in (-1, 0, 1):
            cell_lat, cell_lon = centre["lat"] + dlat * size, centre["lon"] + dlon * size
            if -90 <= cell_lat <= 90:
                cells.append(cell_for(cell_lat, ((cell_lon + 180) % 360) - 180))
    return cells


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


# ── Rainfall: live and reference record ────────────────────────────────

def _percentile(sorted_values: list[float], fraction: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(fraction * (len(sorted_values) - 1)))]


def summarise_history(hourly: list) -> dict | None:
    """Reduces a cell's hourly rainfall record to the reference values the risk model compares against."""
    cfg = config()
    values = [v for v in hourly if v is not None]
    if not values: return None
    rolling, window_sum = [], 0.0
    for index, value in enumerate(values):
        window_sum += value
        if index >= 24: window_sum -= values[index - 24]
        if index >= 23: rolling.append(window_sum)
    if not rolling: return None
    rolling.sort()
    intensities = sorted(values)
    return {
        "hours": len(values),
        "days": len(values) // 24,
        "missingHours": len(hourly) - len(values),
        "accumulation24hMm": {level: round(_percentile(rolling, p), 1) for level, p in cfg["accumulation_percentiles"].items()},
        "intensityMmPerHour": {level: round(_percentile(intensities, p), 1) for level, p in cfg["intensity_percentiles"].items()},
        "wettest24hMm": round(rolling[-1], 1),
        "highestMmPerHour": round(intensities[-1], 1),
    }


def _locations(payload) -> list:
    return payload if isinstance(payload, list) else [payload]


def load_history(cells: list[dict], store=None) -> dict[str, dict | None]:
    """Reference rainfall for each cell, from memory, then the persistent store, then Open-Meteo."""
    cfg = config()
    result: dict[str, dict | None] = {}
    missing = []
    for cell in cells:
        cached = _cache_get(f"history:{cell['id']}")
        if cached is None and store:
            saved = store.load(f"WLCELL#{cell['id']}", "HISTORY")
            if saved and saved.get("expiresAt", 0) > time.time() and saved.get("summary"):
                cached = saved["summary"]
                _cache_put(f"history:{cell['id']}", cached, saved["expiresAt"] - time.time())
        if cached is None: missing.append(cell)
        else: result[cell["id"]] = cached
    if not missing: return result
    end = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d")
    query = urlencode({"latitude": ",".join(str(c["lat"]) for c in missing), "longitude": ",".join(str(c["lon"]) for c in missing), "start_date": cfg["history_start"], "end_date": end, "hourly": "precipitation", "timezone": "GMT"})
    try:
        payload = _locations(fetch_json(f"{HISTORY_URL}?{query}", "open-meteo-history"))
    except SourceError as e:
        logger.warning("Rainfall history unavailable: %s", e)
        for cell in missing: result[cell["id"]] = None
        return result
    for cell, location in zip(missing, payload):
        summary = summarise_history((location.get("hourly") or {}).get("precipitation") or [])
        if summary:
            summary["period"] = [cfg["history_start"], end]
            _cache_put(f"history:{cell['id']}", summary, cfg["history_ttl_seconds"])
            if store: store.save(f"WLCELL#{cell['id']}", "HISTORY", {"summary": summary, "expiresAt": int(time.time() + cfg["history_ttl_seconds"])})
        result[cell["id"]] = summary
    for cell in missing: result.setdefault(cell["id"], None)
    return result


def summarise_live(times: list[str], values: list, now: datetime) -> dict | None:
    """Recent and forecast rain for one cell. Each hourly value is the rain that fell in the hour ending at that time."""
    cfg = config()
    current = now.strftime("%Y-%m-%dT%H:00")
    past = [(t, v) for t, v in zip(times, values) if t <= current]
    future = [(t, v) for t, v in zip(times, values) if t > current]
    last_24 = past[-24:]
    if len(last_24) < 24: return None
    missing = sum(1 for _, v in last_24 if v is None)
    # A gap is not the same as no rain: with too many missing hours the totals would understate the rain.
    if missing > 6: return None
    recent = [v or 0.0 for _, v in last_24]
    next_24 = [v for _, v in future[:24] if v is not None]
    return {
        "asOf": f"{last_24[-1][0]}Z",
        "lastHourMm": round(recent[-1], 1),
        "peakLast3hMmPerHour": round(max(recent[-3:]), 1),
        "last6hMm": round(sum(recent[-6:]), 1),
        "last24hMm": round(sum(recent), 1),
        "wetHoursLast24h": sum(1 for v in recent if v >= cfg["wet_hour_mm"]),
        "missingHoursLast24h": missing,
        "forecastNext6hMm": round(sum(next_24[:6]), 1) if len(next_24) >= 6 else None,
        "forecastNext24hMm": round(sum(next_24), 1) if len(next_24) == 24 else None,
        "forecastPeakMmPerHour": round(max(next_24), 1) if next_24 else None,
    }


def model_spread(times: list[str], hourly: dict, now: datetime) -> dict | None:
    """How far the weather models disagree about recent rain: the lowest and highest value any model gives."""
    current = now.strftime("%Y-%m-%dT%H:00")
    last_hour, last_24h = [], []
    for key, values in hourly.items():
        if not key.startswith("precipitation"): continue
        past = [v for t, v in zip(times, values or []) if t <= current][-24:]
        if len(past) < 24 or any(v is None for v in past): continue
        last_hour.append(past[-1]); last_24h.append(sum(past))
    if len(last_hour) < 2: return None
    return {
        "models": len(last_hour),
        "lastHourMm": {"min": round(min(last_hour), 1), "max": round(max(last_hour), 1)},
        "last24hMm": {"min": round(min(last_24h), 1), "max": round(max(last_24h), 1)},
        "modelsWithNoRainLastHour": sum(1 for v in last_hour if v < config()["wet_hour_mm"]),
    }


def load_live(cells: list[dict], now: datetime | None = None) -> dict[str, dict | None]:
    cfg = config()
    now = now or datetime.now(UTC)
    result: dict[str, dict | None] = {}
    missing = []
    for cell in cells:
        cached = _cache_get(f"live:{cell['id']}")
        if cached is None: missing.append(cell)
        else: result[cell["id"]] = cached
    if not missing: return result
    query = urlencode({"latitude": ",".join(str(c["lat"]) for c in missing), "longitude": ",".join(str(c["lon"]) for c in missing), "hourly": "precipitation", "past_days": 2, "forecast_days": 2, "timezone": "GMT", "models": ",".join((PRIMARY_MODEL, *CROSS_CHECK_MODELS))})
    try:
        payload = _locations(fetch_json(f"{FORECAST_URL}?{query}", "open-meteo-forecast"))
    except SourceError as e:
        logger.warning("Live rainfall unavailable: %s", e)
        for cell in missing: result[cell["id"]] = None
        return result
    fetched_at = datetime.now(UTC).isoformat()
    for cell, location in zip(missing, payload):
        hourly = location.get("hourly") or {}
        times = hourly.get("time") or []
        live = summarise_live(times, hourly.get(f"precipitation_{PRIMARY_MODEL}") or hourly.get("precipitation") or [], now)
        if live:
            live["fetchedAt"] = fetched_at
            live["modelSpread"] = model_spread(times, hourly, now)
            _cache_put(f"live:{cell['id']}", live, cfg["live_ttl_seconds"])
        result[cell["id"]] = live
    for cell in missing: result.setdefault(cell["id"], None)
    return result


def _reports_precipitation(report: dict) -> bool:
    """Whether a METAR reports precipitation falling at the station, now or since the previous report."""
    def has_precipitation(token: str) -> bool:
        token = token.lstrip("+-")
        codes = {token[i:i + 2] for i in range(0, len(token), 2)}
        # VC is "in the vicinity", DR/BL are drifting and blowing snow: none is precipitation at the station.
        return not token.startswith("VC") and not codes & {"DR", "BL"} and bool(codes & PRECIPITATION_CODES)

    if any(has_precipitation(token) for token in str(report.get("wxString") or "").split()): return True
    for token in str(report.get("rawOb") or "").split():
        # Everything after these is remarks or a forecast trend, not an observation.
        if token in ("RMK", "NOSIG", "BECMG", "TEMPO"): break
        if token.startswith("RE") and len(token) >= 4 and has_precipitation(token[2:]): return True
    return False


def load_station_observations(cells: list[dict], now: datetime | None = None) -> dict[str, dict | None]:
    """For each cell, what the nearest airport weather station reported in the last hour or so, or None.

    None covers every case where there is nothing to check against: no station close enough,
    no recent report, a station that cannot detect rain, or the source being unreachable.
    """
    cfg = config()
    now = now or datetime.now(UTC)
    if not cells: return {}
    # About 0.3 degrees of margin so a station just outside the cells is still found.
    box = (min(c["bounds"][0][0] for c in cells) - 0.3, min(c["bounds"][0][1] for c in cells) - 0.3, max(c["bounds"][1][0] for c in cells) + 0.3, max(c["bounds"][1][1] for c in cells) + 0.3)
    key = "metar:" + ",".join(f"{value:.2f}" for value in box)
    reports = _cache_get(key)
    if reports is None:
        try:
            reports = fetch_json(f"{METAR_URL}?{urlencode({'bbox': ','.join(f'{value:.2f}' for value in box), 'format': 'json', 'hours': 2})}", "aviation-weather-metar") or []
        except SourceError as e:
            logger.warning("Station reports unavailable: %s", e)
            return {cell["id"]: None for cell in cells}
        _cache_put(key, reports, cfg["live_ttl_seconds"])

    stations: dict[str, list[dict]] = {}
    for report in reports if isinstance(reports, list) else []:
        try:
            age_minutes = (now.timestamp() - float(report["obsTime"])) / 60
            float(report["lat"]); float(report["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        raw = str(report.get("rawOb") or "")
        # An automated station marked AO1 has no precipitation sensor, so its silence about rain means nothing.
        if " AUTO " in raw and " AO1" in raw: continue
        if -10 <= age_minutes <= cfg["station_window_minutes"] and report.get("icaoId"):
            stations.setdefault(str(report["icaoId"]), []).append(report)

    result: dict[str, dict | None] = {}
    for cell in cells:
        nearest = None
        for station_id, recent in stations.items():
            latest = max(recent, key=lambda r: r["obsTime"])
            distance = distance_km(cell["lat"], cell["lon"], float(latest["lat"]), float(latest["lon"]))
            if distance <= cfg["station_max_km"] and (nearest is None or distance < nearest["distanceKm"]):
                nearest = {
                    "stationId": station_id, "stationName": str(latest.get("name") or station_id)[:80], "distanceKm": round(distance),
                    "observedAt": datetime.fromtimestamp(float(latest["obsTime"]), UTC).isoformat(), "reports": len(recent),
                    "rainReported": any(_reports_precipitation(r) for r in recent), "weather": str(latest.get("wxString") or "")[:40] or None,
                }
        result[cell["id"]] = nearest
    return result


def load_flood_alerts() -> list[dict] | None:
    """Current GDACS flood events worldwide, or None when GDACS cannot be read."""
    cfg = config()
    cached = _cache_get("gdacs")
    if cached is not None: return cached
    today = datetime.now(UTC)
    query = urlencode({"eventlist": "FL", "alertlevel": "Green;Orange;Red", "fromDate": (today - timedelta(days=30)).strftime("%Y-%m-%d"), "toDate": today.strftime("%Y-%m-%d")})
    try:
        payload = fetch_json(f"{GDACS_URL}?{query}", "gdacs")
    except SourceError as e:
        logger.warning("GDACS unavailable: %s", e)
        return None
    alerts = []
    for feature in (payload or {}).get("features") or []:
        properties, geometry = feature.get("properties") or {}, feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coordinates) < 2 or str(properties.get("iscurrent")).lower() != "true": continue
        report_url = (properties.get("url") or {}).get("report") if isinstance(properties.get("url"), dict) else None
        alerts.append({"id": f"gdacs-{properties.get('eventid')}", "name": properties.get("name") or "Flood", "alertLevel": properties.get("alertlevel"), "country": properties.get("country"), "from": properties.get("fromdate"), "to": properties.get("todate"), "lat": coordinates[1], "lon": coordinates[0], "url": report_url if isinstance(report_url, str) and report_url.startswith("https://") else None, "source": "gdacs"})
    _cache_put("gdacs", alerts, cfg["alerts_ttl_seconds"])
    return alerts


# ── Risk model ─────────────────────────────────────────────────────────

def _band(value: float | None, thresholds: dict, floor: float) -> int:
    """Index into LEVELS: how far up the cell's own thresholds a value reaches (0 = LOW)."""
    if value is None or value < floor: return 0
    band = 0
    for index, level in enumerate(LEVELS[1:], start=1):
        if value >= max(thresholds[level], floor): band = index
    return band


def assess(cell: dict, live: dict | None, history: dict | None, reports: list[dict], alerts: list[dict] | None, now: datetime | None = None, observation: dict | None = None) -> dict:
    """One cell's result: rainfall, estimated risk, evidence, confidence, and every caveat that applies."""
    cfg = config()
    now = now or datetime.now(UTC)
    limitations, evidence = [], []
    result = {**cell, "rainfall": live, "reference": history, "kind": "ESTIMATE", "risk": "INSUFFICIENT_DATA", "forecastRisk": "INSUFFICIENT_DATA", "drivers": [], "confirmedReports": [], "unverifiedReports": [], "nearbyAlerts": [], "observation": observation}

    active = [r for r in reports if r.get("cellId") == cell["id"] and (now - datetime.fromisoformat(r["at"])).total_seconds() <= cfg["report_active_hours"] * 3600]
    confirmed = [r for r in active if r.get("verified")]
    result["confirmedReports"] = confirmed
    result["unverifiedReports"] = [r for r in active if not r.get("verified")]

    if alerts is None:
        limitations.append("Official flood alerts (GDACS) could not be read, so none are shown.")
    else:
        near = sorted(({**a, "distanceKm": round(distance_km(cell["lat"], cell["lon"], a["lat"], a["lon"]))} for a in alerts), key=lambda a: a["distanceKm"])
        result["nearbyAlerts"] = [a for a in near if a["distanceKm"] <= cfg["alert_radius_km"]]

    if live is None:
        limitations.append("Rainfall for this area is unavailable or has gaps right now, so no risk is estimated.")
    if history is None:
        limitations.append("No rainfall record could be loaded for this area, so there is nothing to compare today's rain against.")
    elif history["days"] < cfg["min_history_days"]:
        limitations.append(f"The rainfall record here covers only {history['days']} days; at least {cfg['min_history_days']} are needed for a comparison.")

    # Cross-check the modelled rain before anyone reads it as fact.
    contradicted = False
    if live:
        wet = cfg["wet_hour_mm"]
        modelled = live["lastHourMm"] >= wet
        live = result["rainfall"] = {**live, "lastHourCheck": "UNCHECKED"}
        if observation is None:
            limitations.append(f"No weather station within {cfg['station_max_km']:.0f} km reported recently, so the modelled rainfall could not be checked against an observation.")
        elif modelled and not observation["rainReported"]:
            live["lastHourCheck"], contradicted = "CONTRADICTED", True
            limitations.append(f"The model shows {live['lastHourMm']} mm of rain in the last hour, but the nearest weather station ({observation['stationId']}, {observation['distanceKm']} km away) reported none. Modelled rain is often wrong for a particular hour, so treat these rainfall figures as uncertain.")
        elif not modelled and observation["rainReported"]:
            live["lastHourCheck"], contradicted = "CONTRADICTED", True
            limitations.append(f"The nearest weather station ({observation['stationId']}, {observation['distanceKm']} km away) reported rain in the last hour that the model does not show, so the rainfall figures here may be too low.")
        else:
            live["lastHourCheck"] = "SUPPORTED"
            evidence.append(f"The nearest weather station ({observation['stationId']}, {observation['distanceKm']} km away) {'also reported rain' if modelled else 'also reported no rain'} in the last hour.")
        spread = live.get("modelSpread")
        if spread and modelled and spread["modelsWithNoRainLastHour"]:
            limitations.append(f"Weather models disagree about the last hour here: {spread['modelsWithNoRainLastHour']} of {spread['models']} show no rain.")

    score = 0.0
    if live and history and history["days"] >= cfg["min_history_days"]:
        acc_band = _band(live["last24hMm"], history["accumulation24hMm"], cfg["min_accumulation_mm"])
        int_band = _band(live["peakLast3hMmPerHour"], history["intensityMmPerHour"], cfg["min_intensity_mm_per_hour"])
        result["risk"] = LEVELS[max(acc_band, int_band)]
        if acc_band: result["drivers"].append({"metric": "accumulation24h", "valueMm": live["last24hMm"], "level": LEVELS[acc_band], "thresholdMm": history["accumulation24hMm"][LEVELS[acc_band]]})
        if int_band: result["drivers"].append({"metric": "intensity", "valueMm": live["peakLast3hMmPerHour"], "level": LEVELS[int_band], "thresholdMm": history["intensityMmPerHour"][LEVELS[int_band]]})
        if live["forecastNext24hMm"] is not None:
            result["forecastRisk"] = LEVELS[max(_band(live["forecastNext24hMm"], history["accumulation24hMm"], cfg["min_accumulation_mm"]), _band(live["forecastPeakMmPerHour"], history["intensityMmPerHour"], cfg["min_intensity_mm_per_hour"]))]
        else:
            limitations.append("The forecast is incomplete, so no forecast risk is given.")
        # An uncalibrated rainfall estimate starts as low confidence; only independent evidence raises it.
        score = 0.25
        evidence.append("Model rainfall compared with this area's own rainfall record.")
        if history["days"] >= 1095: score += 0.1
        # A station agreeing about the rain says nothing about whether rain causes waterlogging here,
        # so agreement does not raise confidence; a contradiction lowers it.
        if contradicted: score -= 0.1
        if live["missingHoursLast24h"]:
            score -= 0.1
            limitations.append(f"{live['missingHoursLast24h']} of the last 24 hours had no rainfall value and were counted as zero.")
        age_minutes = (now - datetime.fromisoformat(live["fetchedAt"])).total_seconds() / 60
        if age_minutes > cfg["stale_after_minutes"]:
            score -= 0.15
            limitations.append(f"Rainfall was last refreshed {int(age_minutes)} minutes ago.")
        serious = [a for a in result["nearbyAlerts"] if a.get("alertLevel") in ("Orange", "Red")]
        if serious and result["risk"] != "LOW":
            score += 0.15
            evidence.append(f"An official {serious[0]['alertLevel']} flood alert is active within {cfg['alert_radius_km']:.0f} km.")
        limitations.append("This is an estimate from modelled rainfall on a grid of about 11 km. It is not calibrated against local waterlogging incidents and does not show conditions on individual streets.")

    if result["unverifiedReports"]:
        evidence.append(f"{len(result['unverifiedReports'])} unverified report(s) of waterlogging here. Unverified reports do not change the risk level.")
    if confirmed:
        # A verified direct observation outranks any rainfall estimate, and is the only way a cell becomes confirmed.
        result["kind"], result["risk"], score = "CONFIRMED", "CONFIRMED", 0.9
        evidence.insert(0, f"Waterlogging reported on site by a signed-in supervisor ({len(confirmed)} report{'s' if len(confirmed) != 1 else ''} in the last {cfg['report_active_hours']:.0f} hours).")

    score = max(0.0, min(1.0, round(score, 2)))
    result["confidence"] = {"score": score, "label": "none" if score == 0 else "low" if score < 0.4 else "medium" if score < 0.7 else "high"}
    result["evidence"], result["limitations"] = evidence, limitations
    return result


def assess_cells(cells: list[dict], reports: list[dict], store=None) -> dict:
    live = load_live(cells)
    history = load_history(cells, store)
    alerts = load_flood_alerts()
    observations = load_station_observations(cells)
    now = datetime.now(UTC)
    return {
        "cells": [assess(cell, live.get(cell["id"]), history.get(cell["id"]), reports, alerts, now, observations.get(cell["id"])) for cell in cells],
        "updatedAt": now.isoformat(),
        "refreshSeconds": config()["live_ttl_seconds"],
        "gridKm": round(config()["grid_degrees"] * 111),
        "sources": source_report(),
    }


def source_report() -> list[dict]:
    return [{"id": key, **info, "status": _source_status.get(key)} for key, info in SOURCES.items()]
