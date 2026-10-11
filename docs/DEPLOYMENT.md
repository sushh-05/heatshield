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

## Telegram

Create a bot with `@BotFather`, store its token in SSM or your local environment, set `TELEGRAM_BOT_USERNAME`, then register:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/setWebhook" `
  -ContentType "application/json" `
  -Body (@{url="$env:VITE_API_URL/telegram/webhook"} | ConvertTo-Json)
```

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
1. In the [Discord Developer Portal](https://discord.com/developers/applications): set **Interactions Endpoint URL** to:
   `https://<ApiUrl>/discord/interactions` (or `/discord/webhook`).
2. Discord verification PINGs (`{"type": 1}`) are handled automatically.
3. Configure `DISCORD_INVITE_URL` (e.g. `https://discord.gg/...`) so workers can join the crew channel via dashboard QR code.

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

HeatShield supports Clerk authentication for safety supervisors and site managers with user profiles and OSHA credentials.

### 1. Frontend Configuration
1. Sign in to the [Clerk Dashboard](https://dashboard.clerk.com) and create an application.
2. In `frontend/.env`, set your publishable key:
   ```bash
   VITE_CLERK_PUBLISHABLE_KEY=pk_test_...
   ```
3. If no key is set, HeatShield automatically runs in **Demo Supervisor Mode**, allowing offline demonstrations without external credentials.

### 2. Backend Verification
Store your Clerk Secret Key in AWS SSM Parameter Store to validate supervisor session tokens:
```powershell
aws ssm put-parameter --name "/heatshield/clerk-secret-key" --type "SecureString" --value "sk_test_..." --overwrite
```

## Amplify

Connect the GitHub repository, choose the `main` branch, set the app root to `frontend`, and add `VITE_API_URL` and optional `VITE_CLERK_PUBLISHABLE_KEY` as public environment variables. `frontend/amplify.yml` contains the build settings.
