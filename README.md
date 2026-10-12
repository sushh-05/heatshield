# HeatShield

> A morning heat-safety plan and waterlogging watch for outdoor workers, built on public weather data.

## 1. Problem Statement

Outdoor workers (construction crews, delivery riders, farm and sanitation workers) are exposed to dangerous heat and, in monsoon season, to flooded streets and underpasses. Supervisors usually decide on the day with little more than a phone forecast, and safety guidance rarely reaches workers in their own language.

Heat stress is a leading workplace risk in South Asia, and extreme rainfall is turning waterlogging into a recurring hazard for city workers. Both are predictable hours ahead, but the forecast is rarely translated into a concrete plan a supervisor can approve and a worker can read.

## 2. Our Solution

HeatShield turns a weather forecast into a reviewed, localized work plan:

- A **risk model** scores heat, humidity, wind and apparent temperature for a site's working hours and finds the most dangerous window.
- A **morning plan** (rest breaks, water, shifted hours) is generated as a draft. A supervisor must approve it before it is sent to workers by Telegram, Discord, Slack or email.
- A **waterlogging watch** estimates rainfall risk on a map for the area around a site, cross-checked against airport weather-station reports, and keeps supervisor-confirmed reports separate from estimates.

It never presents a forecast as a measurement, never reads missing data as "safe", and labels demo data as demo data.

## 3. Key Features

- Forecast heat-risk scoring (not measured WBGT) with an hourly danger window
- Draft, approve, send workflow: nothing reaches workers without supervisor approval, and a send only retries the people and channels it missed
- Delivery over Telegram, Discord, Slack and Gmail, with one-tap worker check-ins
- Automatic follow-ups every five minutes: an "unwell" alert nobody has handled is escalated to the supervisor, the worker gets first-aid steps and nearby co-workers are asked to help; workers who have not checked in are reminded once
- Waterlogging watch on a Leaflet map: per-cell rainfall percentiles, METAR station check, multi-model spread, GDACS flood alerts, supervisor reports
- English, Hindi and Telugu interface and messages
- Clerk sign-in for supervisors, with each site scoped to the organisation (or person) that created it; scheduled 06:00 Asia/Kolkata plan staging

## 4. AWS Technology Used

- **AWS Lambda + API Gateway (HTTP API)**
  - **Purpose:** Serves the whole API.
  - **Implementation:** [backend/app.py](backend/app.py) is a single Lambda handler behind an HTTP API defined in [template.yaml](template.yaml).
  - **Why we chose it:** Bursty, low-volume traffic with no servers to run.
- **Amazon DynamoDB**
  - **Purpose:** Persists sites, plans, workers, check-ins, alerts, waterlogging reports and each grid cell's rainfall record.
  - **Implementation:** One table keyed by `pk`/`sk`; state reloads on cold start.
  - **Why we chose it:** Pay-per-request, no capacity planning.
- **Amazon EventBridge Scheduler**
  - **Purpose:** Stages draft plans at 06:00 Asia/Kolkata, and runs the follow-up check every five minutes.
  - **Implementation:** One schedule invokes `POST /api/plan`, which never sends to workers; a second invokes `POST /api/follow-ups/run` ([backend/core/followup.py](backend/core/followup.py)).
  - **Why we chose it:** Native timezone-aware cron.
- **Amazon Bedrock (optional)**
  - **Purpose:** Rewords plan messages in the worker's language.
  - **Implementation:** [backend/core/messages.py](backend/core/messages.py); output falls back to templates if unavailable.
  - **Why we chose it:** Managed models with no separate key to store.
- **AWS Systems Manager Parameter Store**
  - **Purpose:** Holds bot tokens, webhook secrets and the Clerk key as SecureString parameters.
  - **Why we chose it:** Keeps secrets out of the template and the repo.
- **AWS Amplify** hosts the built frontend ([frontend/amplify.yml](frontend/amplify.yml)).

## 5. Tech Stack

- **Frontend:** React, Vite, TypeScript, Leaflet, Recharts, Vitest
- **Backend:** Python 3 on AWS Lambda, no web framework
- **Database:** Amazon DynamoDB
- **AWS:** Lambda, API Gateway, DynamoDB, EventBridge Scheduler, Bedrock, SSM, Amplify, SAM
- **Other APIs/tools:** Open-Meteo (forecast and historical), Aviation Weather Center METAR, GDACS, Clerk (auth), Telegram Bot API, Discord, Slack, Gmail SMTP, GitHub Actions

## 6. Architecture

```text
React/Vite (Amplify) ──► API Gateway HTTP API ──► Lambda ──► Open-Meteo (forecast, history)
                                                        ├──► Aviation Weather METAR
                                                        ├──► GDACS flood events
                                                        ├──► DynamoDB
                                                        ├──► Bedrock        (optional)
                                                        ├──► Telegram       (optional)
                                                        ├──► Discord        (optional)
                                                        ├──► Slack          (optional)
                                                        └──► Gmail SMTP     (optional)
EventBridge Scheduler (06:00 Asia/Kolkata) ──► Lambda  (draft plans only)
EventBridge Scheduler (every 5 minutes)     ──► Lambda  (alert escalation, check-in reminders)
```

1. A supervisor signs in with Clerk and defines a site (location, work hours, workload).
2. The backend fetches the forecast, scores risk and stores a `DRAFT` plan.
3. The supervisor approves it; only then can it be sent to Telegram, Discord, Slack or email.
4. Workers acknowledge or raise an alert; alerts show on the dashboard.
5. The waterlogging panel compares modelled rain with each map cell's own rainfall record and the nearest airport station, and shows supervisor reports separately. See [docs/WATERLOGGING.md](docs/WATERLOGGING.md).

## 7. Getting Started

### Prerequisites

- Python 3.12+
- Node.js 22.12+ and npm
- AWS SAM CLI and Docker (deployment only)

### Installation

```bash
git clone https://github.com/sushh-05/heatshield.git
cd heatshield
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
cd ../frontend && npm install
```

### Environment Variables

Copy `.env.example` (repo root) and `frontend/.env.example` and add your own credentials. Nothing is required to run in demo mode. Main variables:

| Variable | Purpose |
|---|---|
| `HEATSHIELD_DEMO` | `true` serves labelled offline demo weather |
| `HEATSHIELD_OPEN_ACCESS` | `true` skips supervisor auth (local development only) |
| `SUPERVISOR_KEY` | Shared key that authorizes supervisor actions without Clerk |
| `CLERK_SECRET_KEY` / `CLERK_JWT_KEY` | Verify Clerk supervisor tokens |
| `CLERK_AUTHORIZED_PARTIES` | Comma-separated dashboard origins accepted in a token's `azp` claim |
| `VITE_CLERK_PUBLISHABLE_KEY` | Frontend Clerk key |
| `VITE_API_URL` | API base URL for the dashboard (builds fail without it) |
| `PUBLIC_API_URL` | Public API URL used in email check-in links, if not the API Gateway domain |
| `ALLOWED_ORIGIN` | CORS origin for the dashboard; defaults to `*` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET` | Telegram delivery and webhook check |
| `DISCORD_PUBLIC_KEY` / `DISCORD_WEBHOOK_SECRET`, `DISCORD_WEBHOOK_URL`, `DISCORD_INVITE_URL` | Discord request verification and delivery |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook for approved plans and worker alerts |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `GMAIL_RECIPIENTS` | Email briefings and signed check-in links |
| `CHECKIN_LINK_SECRET` | Signs email check-in links; falls back to the Gmail app password |
| `USE_BEDROCK`, `BEDROCK_MODEL_ID` | Optional AI rewording of plan messages |
| `TABLE_NAME` | DynamoDB table (in-memory when unset) |

The waterlogging watch has its own tunables (`WL_*`); see [docs/WATERLOGGING.md](docs/WATERLOGGING.md).

### Run Locally

```bash
# API on http://127.0.0.1:3000
cd backend && python server.py

# Dashboard
cd frontend && npm run dev

# Tests
cd backend && python -m pytest -q
cd frontend && npm test
```

The dashboard calls `http://127.0.0.1:3000` by default; override with `VITE_API_URL`.

### Deploy

```bash
sam build --use-container
sam deploy --guided
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Open-Meteo's free tier is non-commercial only, so a public deployment must carry no advertising or paid features.

## 8. Screenshots / Demo

- **Demo video:** [Watch on YouTube](https://www.youtube.com/watch?v=3pHELcuhmTE) — A 3-minute walkthrough covering:
  - Setting up a work site with location and crew details
  - Generating a heat-risk plan from live weather forecast
  - Approving and sending the plan to workers on Telegram
  - Workers checking in with one-tap OK / Not OK buttons
  - Supervisor dashboard showing crew status and alerts
  - Waterlogging watch on the map around the site
- Screenshots: _to be added_

## 9. Environmental Impact

HeatShield aims to reduce heat illness and flood exposure among outdoor workers by moving decisions earlier in the day and into the worker's language.

No impact has been measured. The risk model and the waterlogging levels are **not validated** against real incidents, and no precision, recall or false-alarm rate exists yet. Possible measures once piloted: share of plans approved before work start, worker check-in rate, heat-illness incidents per 1,000 work-hours before and after, and waterlogging reports matched against flagged cells.

## 10. Limitations and Future Improvements

**Limitations**

- Heat risk is forecast-based, not measured WBGT.
- Rainfall is modelled on a grid of about 11 km. It cannot detect a flooded street; only a supervisor report does.
- Waterlogging thresholds are percentile-based defaults, not calibrated to local incidents.
- The METAR check covers one airport station, often without a rain amount, and only within 30 km.
- Evidence sentences are English-only in all three languages.

**Planned**

- Calibrate waterlogging percentiles against dated local incident records
- Store observations over time and measure precision and recall
- Add the Open-Meteo Flood API for sites near rivers
- Add underpasses and low road segments from OpenStreetMap

## 11. Team

Contributions below are from the git history of this repository.

**Thadi Sushmitha Mani** ([sushh-05](https://github.com/sushh-05)) — started the project: the React and Vite app, the Python API, and the SAM template. Built the heat-plan workflow (forecast, risk, messages, planner) and the persistence and deployment hooks, then hardened the backend so the dashboard could run that workflow. Merged the local-dev and frontend i18n pull requests.

**Sumit Mishra** ([Sumit884-byte](https://github.com/Sumit884-byte)) — most of the product after that foundation. Waterlogging watch (map, METAR check, translations), Gmail delivery, dashboard redesign, theme, onboarding, and the location picker. Follow-up checks and alert escalation. Access control, delivery retries, CI, and moving secrets into parameters. README, API docs, and the safety claims.

**AwesomeSno** — Clerk sign-in, supervisor profiles, and OSHA credentials. Discord briefings and worker replies. Hardening for security, cold start, accessibility, and the frontend bundle.

**Ravi Nithish Kumar** (ravinithishkumar2001) — Slack delivery and its status on the dashboard, plus timezone data so the backend runs on Windows.

## 12. License

MIT License. See [LICENSE](LICENSE) for details.

Copyright (c) 2026 Sumit Mishra, Sushmitha Mani Thadi, and contributors.

---

## Reference

### API

Every route below also answers under an `/api` prefix (`/api/sites`, `/api/plan`, …); the frontend and the docs use that form.

```text
GET  /health                              GET  /auth/me
POST /sites                               GET  /sites
PUT  /sites/{siteId}                      GET  /sites/{siteId}/status
POST /sites/{siteId}/plans/generate       GET  /sites/{siteId}/join
POST /sites/{siteId}/plans/{date}/approve POST /sites/{siteId}/plans/{date}/send
GET  /sites/{siteId}/plans/{date}         POST /plan (scheduler only, or a signed-in supervisor)
POST /follow-ups/run (scheduler only, or a signed-in supervisor)
POST /telegram/webhook                    POST /discord/webhook, /discord/interactions
GET  /email/ack                           POST /email/ack
POST /alerts/{alertId}                    GET  /dashboard
GET  /waterlogging?lat=&lon=              GET  /waterlogging/point?lat=&lon=
GET  /waterlogging/sources                POST /waterlogging/reports
```

Plans are always generated as `DRAFT`. Sending returns `409` until a supervisor approves, and again once every worker and channel has already received that plan. A `PUT` to a site accepts either camelCase or snake_case field names.

### Safety and operations

- Supervisor actions return `401` unless the request carries `SUPERVISOR_KEY` or a Clerk session token whose signature verifies. `HEATSHIELD_OPEN_ACCESS=true` disables this for local development only. The morning schedule's own call to `/plan` is let through by a marker that only a direct Lambda invocation can set; the same route called over the API still needs a supervisor.
- A site created by a signed-in Clerk user belongs to that user's organisation (or to them alone outside one). Another supervisor can see it in listings and on the public dashboard, but cannot edit it, generate or send its plans, or see who joined or who is unwell. The shared `SUPERVISOR_KEY` manages every site.
- Telegram webhooks are validated with `TELEGRAM_WEBHOOK_SECRET`; Discord interactions by Ed25519 signature with a 5-minute freshness window, so a captured request cannot be replayed later.
- If the forecast cannot be fetched the API returns `503` and creates no plan — including when the forecast is missing values for hours inside the site's working day. Canned weather is served only when `HEATSHIELD_DEMO=true`; every message built from a demo plan (Telegram, Discord, Slack, email) carries a fixed demo notice that AI rewording cannot remove.
- The public dashboard never returns a site's Discord webhook URL, email recipients, or any worker's name, chat ID, or alert status — only counts. Full crew detail is included only for a supervisor who can manage that site.
- A worker's "I feel unwell" check-in pushes an alert to the site's configured Slack or Discord channel; the worker is only told a supervisor was notified when that push actually succeeds.
- Gmail check-in links are HMAC-signed to one recipient, plan and action; opening one shows a confirm button, and only the confirming `POST` records the check-in. A button from an earlier day's plan is rejected rather than recorded against today.
- Follow-ups use fixed rules and fixed wording in English, Hindi and Telugu; no model writes advice to an unwell worker. Each escalation step, reminder and summary happens once, and none of them sends or approves a plan.
- A partially delivered plan can be resent: only the workers and channels that did not receive it are retried, and nobody gets the message twice.
- A failed database write is reported to the caller as a `503`, never shown as succeeded.
- EventBridge stages draft plans only; it never sends to workers. A scheduled run that stages no plan at all raises so Lambda retries it, instead of completing "successfully" with nothing done.
