# Waterlogging watch

A minimal prototype, built into HeatShield, that estimates waterlogging risk from rainfall for the area around a work site and for any location clicked on the map.

It is honest about three different things:

| What you see | Where it comes from | What it means |
|---|---|---|
| Rainfall in mm | Open-Meteo weather models | A model estimate on a grid of about 11 km. Not a rain gauge, not a street-level measurement. |
| Station check | The nearest airport weather station (METAR) within 30 km, and three other weather models | Whether rain was actually observed at that airport in the last 75 minutes. If the station contradicts the model, the panel shows the station's reading for the last hour and flags the modelled figures as uncertain. It says whether rain fell, rarely how much, and nothing about places away from the station. |
| **Estimated risk** (Low, Moderate, High, Very high) | The rainfall above, compared with that grid cell's own rainfall record | How unusual the rain is for that place. It is not calibrated against local waterlogging incidents and never says a place is flooded. |
| **Confirmed report** | A report submitted by a signed-in supervisor | Someone saw waterlogging there in the last 6 hours. This is the only thing that produces "confirmed". |
| Official flood alert | GDACS | A significant flood event somewhere in the region (within 150 km). It raises confidence in a non-low estimate; it never confirms anything about a particular cell. |
| **Insufficient data** | Missing rainfall, gaps, or too short a record | No risk is given. Missing data is never read as "no rain" or "safe". |

## How it works

```
Browser (React + Leaflet)
   │  GET /api/waterlogging?lat=&lon=        nine grid cells around a point
   │  GET /api/waterlogging/point?lat=&lon=  the one cell containing a point
   │  POST /api/waterlogging/reports         supervisor report (auth required)
   ▼
backend/app.py ──► backend/core/waterlogging.py
                      ├─ Open-Meteo forecast API     recent + forecast hourly rain   (cached 10 min per cell)
                      ├─ Open-Meteo historical API   each cell's rainfall record     (cached 30 days, kept in DynamoDB)
                      ├─ Aviation Weather METAR      nearest station's rain reports  (cached 10 min per area)
                      ├─ GDACS                       current flood events            (cached 30 min)
                      └─ supervisor reports          DynamoDB / memory
```

The map is divided into cells of 0.1° (about 11 km), matching the grid of the global weather models. A finer grid would repeat the same model value under different labels, so the map draws squares rather than point markers.

All nine cells of a view are requested in one call per source, and results are cached per cell, so a dashboard left open costs about 6 forecast calls an hour.

## The risk model

For each cell the backend downloads hourly rainfall from mid-2022 to three days ago and reduces it to two sets of reference values:

- **24-hour accumulation**: the values that 95%, 99% and 99.9% of all 24-hour periods in the record stayed below.
- **Hourly intensity**: the values that 99%, 99.9% and 99.99% of all hours stayed below.

The current level is the higher of the two comparisons:

| Level | 24-hour rain is at or above… | or peak hourly rain (last 3 h) is at or above… |
|---|---|---|
| Moderate | the cell's 95th percentile | the cell's 99th percentile |
| High | the 99th percentile | the 99.9th percentile |
| Very high | the 99.9th percentile | the 99.99th percentile |

The forecast level applies the same comparison to the next 24 hours.

So the thresholds are different for every cell: in the Hyderabad cell at the time of writing, High begins at 29.4 mm in 24 hours or 7.4 mm in one hour; a wetter or drier place gets its own numbers. There is no universal rainfall threshold.

Two noise floors apply everywhere: rain under 10 mm in 24 hours and under 5 mm in an hour is never rated above Low. These are not flood thresholds. They stop a dry climate's "unusual" drizzle from being reported as a risk.

A cell needs at least 730 days of record; otherwise it shows Insufficient data.

### Confidence

| Situation | Score | Label |
|---|---|---|
| No estimate possible | 0 | none |
| Rainfall estimate only | 0.25 (+0.1 with three or more years of record) | low |
| Estimate above Low, plus an Orange or Red GDACS alert within 150 km | +0.15 | medium |
| Hours missing in the last day / data older than 90 minutes | −0.1 / −0.15 | |
| Nearest station contradicts the modelled last hour | −0.1 | |
| Nearest station agrees with the model | no change | |
| Verified supervisor report | 0.9 | high |

These weights are design choices, not measured accuracy. A station agreeing about the rain does not raise confidence, because it says nothing about whether that rain causes waterlogging.

### Checking the modelled rain

Modelled rain can be wrong for a particular hour: on 2026-10-11 the default model showed light rain over Kolkata while the airport station reported haze and four of six models showed none. Two checks now run on every cell:

- **Station.** The nearest METAR station within 30 km (`WL_STATION_MAX_KM`) with a report in the last 75 minutes. Rain counts as reported when a report carries a precipitation code (DZ, RA, SN, SG, PL, GR, GS, UP) or a "recent" group such as `RERA`. Showers "in the vicinity" (`VCSH`) do not count, forecast trend groups are ignored, and automated stations without a rain sensor (`AO1`) are skipped.
- **Other models.** The request also asks for ECMWF IFS, GFS and ICON. The lowest and highest values are shown when they differ.

The risk level still uses the default model, because the reference record comes from the same model. A contradiction changes what is displayed and lowers confidence; it does not change the level. Cells with no station nearby are marked as unchecked.

### Calibrating for a location

Every number above is a default in `config()` in [backend/core/waterlogging.py](../backend/core/waterlogging.py), overridable by environment variable:

| Variable | Default | Meaning |
|---|---|---|
| `WL_ACC_P_MODERATE` / `_HIGH` / `_VERY_HIGH` | 0.95 / 0.99 / 0.999 | Percentiles of 24-hour rain |
| `WL_INT_P_MODERATE` / `_HIGH` / `_VERY_HIGH` | 0.99 / 0.999 / 0.9999 | Percentiles of hourly rain |
| `WL_MIN_ACC_MM`, `WL_MIN_INT_MM` | 10, 5 | Noise floors |
| `WL_HISTORY_START`, `WL_MIN_HISTORY_DAYS` | 2022-07-01, 730 | Reference record |
| `WL_LIVE_TTL`, `WL_HISTORY_TTL`, `WL_ALERTS_TTL` | 600, 2592000, 1800 | Cache lifetimes in seconds |
| `WL_MAX_UPSTREAM_PER_MIN` | 20 | Outbound request budget per server process |
| `WL_REPORT_ACTIVE_HOURS`, `WL_ALERT_RADIUS_KM` | 6, 150 | Evidence windows |

The percentiles have not been validated against real waterlogging anywhere. To calibrate a city properly you need dated incident records for it (when and where water actually stood). Compare each incident with that cell's rainfall on that day, then move the percentiles until the level reached on incident days is the one you want, and check how often the same level occurs on days with no incident. Until that is done, treat every level as "rain is unusual here", not "flooding is likely here".

## Endpoints

`GET /api/waterlogging?lat=17.385&lon=78.487` returns nine cells, plus `focusCellId`, recent `reports`, and per-source status. `siteId=demo` may be given instead of coordinates.

`GET /api/waterlogging/point?lat=17.385&lon=78.487` returns the one cell containing the point. Real response, trimmed (11 October 2026, a dry day):

```json
{
  "updatedAt": "2026-10-11T09:08:05+00:00",
  "refreshSeconds": 600,
  "gridKm": 11,
  "cells": [{
    "id": "17.4,78.5", "lat": 17.4, "lon": 78.5,
    "kind": "ESTIMATE", "risk": "LOW", "forecastRisk": "LOW",
    "confidence": {"score": 0.35, "label": "low"},
    "rainfall": {"asOf": "2026-10-11T09:00Z", "lastHourMm": 0.0, "peakLast3hMmPerHour": 0.0, "last6hMm": 0.0,
                 "last24hMm": 0.0, "wetHoursLast24h": 0, "missingHoursLast24h": 0,
                 "forecastNext6hMm": 0.0, "forecastNext24hMm": 0.0, "forecastPeakMmPerHour": 0.0},
    "reference": {"days": 1561, "period": ["2022-07-01", "2026-10-08"],
                  "accumulation24hMm": {"MODERATE": 15.1, "HIGH": 29.4, "VERY_HIGH": 59.2},
                  "intensityMmPerHour": {"MODERATE": 2.4, "HIGH": 7.4, "VERY_HIGH": 13.6},
                  "wettest24hMm": 100.4, "highestMmPerHour": 16.1},
    "evidence": ["Model rainfall compared with this area's own rainfall record."],
    "limitations": ["This is an estimate from modelled rainfall on a grid of about 11 km. ..."]
  }]
}
```

`POST /api/waterlogging/reports` with `{"lat": 17.385, "lon": 78.487, "note": "Underpass flooded"}` needs supervisor authorization. A report from a verified sign-in is stored with `"verified": true` and marks its cell Confirmed for 6 hours. On a server running with open access (local development), reports are stored as unverified and change no risk level.

`GET /api/waterlogging/sources` returns the source registry, live status, and the active configuration.

## Data sources

Facts below were checked on 11 October 2026 by reading each provider's pages and calling each API. Limits change; re-check before relying on them.

### Integrated

| Source | Endpoint and parameters | Key | Free limits | Licence and conditions | Coverage, resolution, freshness | Observes flooding? |
|---|---|---|---|---|---|---|
| [Open-Meteo forecast](https://open-meteo.com/en/docs) | `api.open-meteo.com/v1/forecast?latitude=a,b&longitude=a,b&hourly=precipitation&past_days=2&forecast_days=2&timezone=GMT&models=best_match,ecmwf_ifs025,gfs_seamless,icon_seamless` | No | 600/minute, 5,000/hour, 10,000/day, 300,000/month ([pricing](https://open-meteo.com/en/pricing), [terms](https://open-meteo.com/en/terms)) | CC BY 4.0, attribution required. Free tier is **non-commercial only**: no ads, no subscriptions. No uptime guarantee. | Global. Model grid from about 1 km (parts of Europe) to 25 km; about 11 km for global models. Hourly; 15-minute data is native only in Central Europe and North America. | No. Model rainfall. |
| [Open-Meteo historical forecast](https://open-meteo.com/en/docs/historical-forecast-api) | `historical-forecast-api.open-meteo.com/v1/forecast?...&start_date=2022-07-01&end_date=…&hourly=precipitation` | No | Shares the limits above. Whether a multi-year request counts as more than one call was not verified. | Same as above. The docs page does not state whether it is in the free tier; requests without a key succeed. | From about 2022. Same models as the forecast API. The provider warns it is unsuitable for long time series because model versions change. | No. Model rainfall. |
| [Aviation Weather Center METAR](https://aviationweather.gov/data/api/) | `aviationweather.gov/api/data/metar?bbox=lat0,lon0,lat1,lon1&format=json&hours=2` | No | The provider's published rate limit was not re-checked. This app asks at most once every 10 minutes per area. | US government (NOAA/NWS) public data. | Airports worldwide; reports every 30 to 60 minutes. Many areas have no station within 30 km. | No. Observes rain at one airport, usually without an amount. |
| [GDACS](https://www.gdacs.org/gdacsapi/swagger/index.html) | `gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=FL&alertlevel=Green;Orange;Red&fromDate=…&toDate=…` | No | None published. This app asks at most once every 30 minutes. | Provided "as is"; [not a substitute for official local warnings](https://www.gdacs.org/About/termofuse.aspx). No licence text on the terms page. | Global, significant events only. One reference point per event (83 flood events in the last 30 days when tested). | Event-level only. Says nothing about a street. |

**Failure behaviour.** Each request has a 20-second timeout and up to two retries with exponential backoff on timeouts and on HTTP 429/5xx. If rainfall cannot be read, the cell shows Insufficient data; if GDACS cannot be read, cells say so and continue; if station reports cannot be read, the rainfall is shown as unchecked. Source status is shown under the map.

### Investigated, not integrated in this prototype

| Source | Finding | Why not yet |
|---|---|---|
| [RainViewer radar](https://www.rainviewer.com/api.html) | Works without a key. When tested it listed 13 past radar frames, the latest about 9 minutes old, and no nowcast frames; tiles requested above zoom 7 all came back as the same small image, so the overlay is capped at zoom 7. The provider states the API is for **personal and educational use only** and may be withdrawn. | Implemented as an overlay but **off by default** because of the usage terms. Enable with `VITE_ENABLE_RAINVIEWER=true` only if your use fits them. Radar coverage also depends on the country. |
| [Open-Meteo Flood API](https://open-meteo.com/en/docs/flood-api) | River discharge forecasts. | Not verified in this pass. River flooding is a different mechanism from street waterlogging; it is the next source to add. |
| [NASA GPM IMERG](https://gpm.nasa.gov/data/imerg) | Satellite rainfall estimates. | Not verified. Needs an Earthdata account and file-based access, which does not fit a small Lambda. |
| [OpenStreetMap / Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) | Could identify tunnels and underpasses. | Not verified. Vulnerable road segments are not in this prototype. |
| [Global Flood Database](https://global-flood-database.cloudtostreet.ai/) | Satellite-derived historical inundation. | Not verified. Intended for validation, which this prototype does not do. |
| [Google Flood Forecasting API](https://developers.google.com/flood-forecasting) | Free, but access is by waitlist and it is documented for river floods. | Could not confirm it covers urban flash floods. |
| Municipal incident data (Hyderabad) | No open dataset or API was found. Counts reported in the news range from 123 to 913 locations and do not reconcile. | Without dated incidents there is nothing to calibrate or validate against. |

## Limitations

- **It cannot detect a flooded street.** Nothing free and global observes street-level water. Only a supervisor's report does that here.
- **Rainfall is modelled.** In one check, Open-Meteo's reanalysis archive gave 80.5 mm for central Hyderabad on 13 October 2020, a day for which news reports quoted 144 to 224 mm at city rain gauges. Short, intense storms are the ones models miss most, and they are the ones that cause waterlogging.
- **The estimate ignores drainage, terrain and tides.** Two streets in the same cell with very different drainage get the same level.
- **The reference record is short** (about four years) and its underlying models have changed, so the highest percentiles rest on a handful of hours.
- **Not validated.** No precision, recall or false-alarm rate has been measured. Do not describe this as accurate.
- **Evidence and limitation sentences are in English** in all three interface languages; labels and headings are translated.
- **No history store.** Only the latest values are cached. Historical queries, charts, export and a validation harness are not built.
- **The outbound request budget is per server process.** On Lambda, many concurrent containers each have their own budget; API-level throttling in `template.yaml` is the real guard.

## Running it

Locally, start the backend and frontend as usual; the panel appears under the worker panels once a site is loaded.

```bash
cd backend && python -m pytest -q        # includes the waterlogging tests; all upstream calls are mocked
cd frontend && npm test
```

Deployed, it needs nothing beyond the existing stack (`sam build --use-container && sam deploy`, then the Amplify frontend). Because Open-Meteo's free tier is non-commercial only, a public deployment must carry no advertising or paid features, and must keep the attribution shown on the map.

## Next steps

1. Collect dated local incident records and calibrate the percentiles for one city.
2. Store observations and reports over time, then measure precision, recall and detection delay against those incidents.
3. Add the Open-Meteo Flood API for sites near rivers.
4. Add underpasses and low road segments from OpenStreetMap as places to watch within a cell.
