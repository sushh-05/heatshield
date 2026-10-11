# HeatShield

HeatShield helps outdoor workers plan safer workdays during dangerous heat. It combines a weather forecast, a simple risk model, and localized safety guidance into one morning plan.

## What is included

- React + Vite dashboard with Clerk login, supervisor profiles, OSHA credentials, and Telugu, Hindi, and English localization
- Python Lambda API with `GET /api/dashboard`, `POST /api/plan`, `GET /api/health`, and `GET /auth/me`
- Open-Meteo forecast integration with deterministic demo data when offline
- Risk scoring for heat, humidity, wind, and apparent temperature
- Optional DynamoDB persistence, Amazon Bedrock wording, and Telegram / Discord / Gmail delivery
- AWS SAM template for API Gateway, Lambda, DynamoDB, and EventBridge Scheduler
- Waterlogging watch: a rainfall-based risk estimate on a map for the area around a site, with supervisor-confirmed reports kept separate from estimates ([docs/WATERLOGGING.md](docs/WATERLOGGING.md))
- Pytest coverage for the risk engine and API contract

## Run locally

### API

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest
sam local start-api --template-file ..\template.yaml
```

If SAM is not installed, the pure Python handler can still be tested with pytest. Set `HEATSHIELD_DEMO=true` to force offline demo data.

### Dashboard

```powershell
cd frontend
npm install
npm run dev
```

The Vite app uses `http://127.0.0.1:3000` for the API by default. Override it with `VITE_API_URL`.

## Deploy

```powershell
sam build --use-container
sam deploy --guided
```

The template creates a least-privilege Lambda role for DynamoDB, SSM, Bedrock, and CloudWatch. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as secure SSM parameters if you want the scheduled morning plan delivered to Telegram. Add `DISCORD_WEBHOOK_URL` or configure Discord interactions if you want briefings dispatched to Discord. Set `GMAIL_ADDRESS` and a Gmail app password (`GMAIL_APP_PASSWORD` or its SSM parameter) to email briefings to each site's `emailRecipients`.

## Demo flow

1. Open the dashboard and select a worker language.
2. Show the current risk score and the hourly danger window.
3. Press **Generate morning plan** to call the backend.
4. Show the action checklist and the source badge.
5. Explain that EventBridge Scheduler invokes the same endpoint at 06:00 Asia/Kolkata.

## Architecture

```text
React/Vite -> API Gateway HTTP API -> Lambda -> Open-Meteo
                                      |-> DynamoDB
                                      |-> Bedrock (optional)
                                      |-> Telegram (optional)
                                      |-> Discord (optional)
                                      |-> Gmail SMTP (optional)
EventBridge Scheduler (06:00 Asia/Kolkata) -> Lambda
```

## MVP workflow API

```text
POST /sites
GET  /sites
PUT  /sites/{siteId}
POST /sites/{siteId}/plans/generate
POST /sites/{siteId}/plans/{date}/approve
POST /sites/{siteId}/plans/{date}/send
GET  /sites/{siteId}/status
GET  /sites/{siteId}/join
POST /telegram/webhook
POST /discord/webhook
GET  /email/ack
POST /email/ack
POST /alerts/{alertId}
GET  /auth/me
GET  /waterlogging?lat=&lon=
GET  /waterlogging/point?lat=&lon=
GET  /waterlogging/sources
POST /waterlogging/reports
```

Plans are always generated as `DRAFT`. Sending a plan returns `409` until a supervisor approves it. Telegram and Discord callbacks and Gmail check-in links record `ACKNOWLEDGED` or create an `OPEN` alert.

## Backend safety and operations

- Open-Meteo requests include temperature, humidity, wind speed, and direct solar irradiance.
- The engine exposes `forecast_heat_risk`; it never labels forecast data as measured WBGT.
- Supervisor actions (site changes, generate/approve/send, resolving alerts) are rejected with `401` unless the request carries `SUPERVISOR_KEY` (`x-api-key` or `Authorization: Bearer ...`) or a Clerk session token whose signature verifies. `HEATSHIELD_OPEN_ACCESS=true` disables this for local development only; the local dev server sets it automatically.
- Set `TELEGRAM_WEBHOOK_SECRET` (or its SSM parameter) to validate Telegram's `X-Telegram-Bot-Api-Secret-Token` header; without it the webhook is rejected outside open-access mode.
- Set `DISCORD_PUBLIC_KEY` (or its SSM parameter) so Discord interaction requests are verified by their Ed25519 signature; `DISCORD_WEBHOOK_SECRET` is an alternative for a custom relay.
- If the forecast cannot be fetched, the API returns `503` and creates no plan. Canned weather is only ever served when `HEATSHIELD_DEMO=true`, and such plans are labelled as demo data.
- Sites and plans are stored in DynamoDB and reloaded on a cold start. The dashboard never returns a site's Discord webhook URL or email recipients.
- Gmail check-in links are HMAC-signed with the app password; opening one shows a confirm button, and only the confirming `POST` records the check-in.
- Set `CLERK_SECRET_KEY` (or its SSM parameter) or `CLERK_JWT_KEY` to verify Clerk supervisor session tokens.
- EventBridge calls `POST /api/plan` at 06:00 Asia/Kolkata and stages draft plans only; it never sends directly to workers.
