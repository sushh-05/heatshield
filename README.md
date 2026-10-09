# HeatShield

HeatShield helps outdoor workers plan safer workdays during dangerous heat. It combines a weather forecast, a simple risk model, and localized safety guidance into one morning plan.

## What is included

- React + Vite dashboard with Telugu, Hindi, and English language switching
- Python Lambda API with `GET /api/dashboard`, `POST /api/plan`, and `GET /api/health`
- Open-Meteo forecast integration with deterministic demo data when offline
- Risk scoring for heat, humidity, wind, and apparent temperature
- Optional DynamoDB persistence, Amazon Bedrock wording, and Telegram delivery
- AWS SAM template for API Gateway, Lambda, DynamoDB, and EventBridge Scheduler
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

The template creates a least-privilege Lambda role for DynamoDB, SSM, Bedrock, and CloudWatch. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as secure SSM parameters if you want the scheduled morning plan delivered to Telegram.

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
EventBridge Scheduler (06:00 Asia/Kolkata) -> Lambda
```

## MVP workflow API

```text
POST /sites
GET  /sites
POST /sites/{siteId}/plans/generate
POST /sites/{siteId}/plans/{date}/approve
POST /sites/{siteId}/plans/{date}/send
GET  /sites/{siteId}/status
GET  /sites/{siteId}/join
POST /telegram/webhook
POST /alerts/{alertId}
```

Plans are always generated as `DRAFT`. Sending a plan returns `409` until a supervisor approves it. Telegram callbacks record `ACKNOWLEDGED` or create an `OPEN` alert.
