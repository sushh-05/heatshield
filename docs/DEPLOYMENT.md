# Deployment checklist

## Backend

```powershell
aws configure sso
aws sso login --profile heatshield
$env:AWS_PROFILE = "heatshield"
$env:AWS_DEFAULT_REGION = "ap-south-1"
aws sts get-caller-identity
sam validate --lint
sam build --use-container
sam deploy --guided --profile heatshield
```

Use the values from `samconfig.toml.example`. After deployment, copy the `ApiUrl` output into `frontend/.env` as `VITE_API_URL`.

`sam build --use-container` is required: the backend depends on `cryptography` (through `PyJWT[crypto]`), which must be built for the Lambda architecture.

## Access control

A deployed stack is closed by default. Creating or editing sites, generating, approving and sending plans, resolving alerts, and listing sites all return `401` until one of these is configured:

- **Clerk** (recommended): store the Clerk secret key as described under [Clerk Authentication](#clerk-authentication). Session tokens are verified against your instance's signing keys; expired, unsigned or forged tokens are rejected. Any signed-in user of that Clerk instance is treated as a supervisor, so restrict sign-ups in the Clerk dashboard.
- **Supervisor key**: set `SUPERVISOR_KEY` to a long random value and send it as `x-api-key` or `Authorization: Bearer ...`.

The Telegram and Discord webhooks are likewise rejected until their secrets are set (below).

`HEATSHIELD_OPEN_ACCESS=true` turns all of this off. The local dev server (`python app.py`) sets it for you because it only listens on localhost; do not set it on a public stack.

Reading a site's dashboard (`GET /sites/{id}/status`) is public by design, so it can be shown on a site screen. It never includes the Discord webhook URL or the email recipient list.

## Telegram

Create a bot with `@BotFather`, store its token in SSM or your local environment, and set `TELEGRAM_BOT_USERNAME`.

Choose a webhook secret (letters, digits, `_` and `-`), store it, then register the webhook with the same value so Telegram sends it on every update:

```powershell
aws ssm put-parameter --name "/heatshield/telegram-webhook-secret" --type "SecureString" --value "$env:TELEGRAM_WEBHOOK_SECRET" --overwrite
Invoke-RestMethod -Method Post `
  -Uri "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/setWebhook" `
  -ContentType "application/json" `
  -Body (@{url="$env:VITE_API_URL/telegram/webhook"; secret_token="$env:TELEGRAM_WEBHOOK_SECRET"} | ConvertTo-Json)
```

Without the secret, the webhook answers `403` (outside open-access mode), because anyone could otherwise forge worker check-ins and alerts.

Never put the bot token in a Vite variable or Git.

## Discord

### 1. Safety Channel Webhook (Announcements & Embeds)
Create a webhook in your Discord server safety channel (`#heat-safety`):
1. In Discord: Channel Settings &rarr; Integrations &rarr; Webhooks &rarr; New Webhook.
2. Store the URL in AWS SSM Parameter Store:
   ```powershell
   aws ssm put-parameter --name "/heatshield/discord-webhook-url" --type "SecureString" --value "https://discord.com/api/webhooks/..." --overwrite
   ```
   Or set `DISCORD_WEBHOOK_URL` in your backend `.env`.

### 2. Discord Bot & Interactive Check-Ins
For interactive check-in buttons (`✅ OK`, `🚨 I Feel Unwell`) and worker registration:
1. In the [Discord Developer Portal](https://discord.com/developers/applications), open your application and copy its **Public Key** (General Information). Store it first — Discord tests the endpoint's signature check before it will save the URL:
   ```powershell
   aws ssm put-parameter --name "/heatshield/discord-public-key" --type "String" --value "<public key hex>" --overwrite
   ```
   Or set `DISCORD_PUBLIC_KEY` in your backend `.env`.
2. Set **Interactions Endpoint URL** to `https://<ApiUrl>/discord/interactions` (or `/discord/webhook`). Every request's Ed25519 signature is verified against the public key; unsigned or tampered requests get `401`.
3. Discord verification PINGs (`{"type": 1}`) are handled automatically.
4. Configure `DISCORD_INVITE_URL` (e.g. `https://discord.gg/...`) so workers can join the crew channel via dashboard QR code. Per-site invite links must be `https://discord.gg/...` or `https://discord.com/...`.

If you relay interactions through your own service instead of Discord calling the API directly, leave the public key unset and set `DISCORD_WEBHOOK_SECRET`; the relay must then send it as `x-discord-secret-token`.

## Slack

HeatShield sends approved plans to a Slack channel through an incoming webhook.

1. In Slack, open the target channel and create an incoming webhook through the channel's **Integrations** settings.
2. Store the webhook URL in AWS SSM Parameter Store:
   ```powershell
   aws ssm put-parameter --name "/heatshield/slack-webhook-url" --type "SecureString" --value "https://hooks.slack.com/services/..." --overwrite
   ```
   For local development, set `SLACK_WEBHOOK_URL` in the backend environment instead.
3. Approve a plan and press **Send to workers**. The API response reports `channels.slack: 1` when Slack accepts the briefing.

The webhook URL is kept on the backend and must never be placed in a frontend environment variable.

## Gmail

Briefings can also be emailed from a Gmail account over Gmail SMTP.

1. Turn on 2-Step Verification for the sending Google account, then create an [app password](https://myaccount.google.com/apppasswords).
2. Set `GMAIL_ADDRESS` to the sending address (in `template.yaml` or your backend `.env`) and store the app password:
   ```powershell
   aws ssm put-parameter --name "/heatshield/gmail-app-password" --type "SecureString" --value "abcd efgh ijkl mnop" --overwrite
   ```
   Or set `GMAIL_APP_PASSWORD` in your backend `.env`.
3. Add recipients per site with `emailRecipients` on `POST /sites` (a list or comma-separated string), or set `GMAIL_RECIPIENTS` as the fallback for sites without their own list.
4. Each email carries signed **OK** / **I feel unwell** links to `GET /email/ack`. Links use the API Gateway domain of the send request; set `PUBLIC_API_URL` if the API sits behind a custom domain or proxy.

Never put the app password in a Vite variable or Git. Rotating it invalidates check-in links in emails already sent.

## Clerk Authentication

HeatShield supports Clerk authentication for safety supervisors and site managers.

### 1. Frontend Configuration
1. Sign in to the [Clerk Dashboard](https://dashboard.clerk.com) and create an application.
2. In `frontend/.env`, set your publishable key:
   ```bash
   VITE_CLERK_PUBLISHABLE_KEY=pk_test_...
   ```
3. If no key is set, the frontend runs in **Demo Supervisor Mode** with a local sign-in form. That mode only works against a backend running with `HEATSHIELD_OPEN_ACCESS=true` (the local dev server); a deployed backend rejects its requests.

### 2. Backend Verification
Store your Clerk Secret Key in AWS SSM Parameter Store to validate supervisor session tokens:
```powershell
aws ssm put-parameter --name "/heatshield/clerk-secret-key" --type "SecureString" --value "sk_test_..." --overwrite
```
The backend uses it to fetch your instance's signing keys and verifies each token's RS256 signature and expiry. To verify without a network call, set `CLERK_JWT_KEY` to the instance's PEM public key instead. Optionally set `CLERK_AUTHORIZED_PARTIES` to a comma-separated list of allowed frontend origins (checked against the token's `azp` claim).

## Amplify

Connect the GitHub repository, choose the `main` branch, set the app root to `frontend`, and add `VITE_API_URL` and optional `VITE_CLERK_PUBLISHABLE_KEY` as public environment variables. `frontend/amplify.yml` contains the build settings.
