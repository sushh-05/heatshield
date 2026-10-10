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

## Amplify

Connect the GitHub repository, choose the `main` branch, set the app root to `frontend`, and add `VITE_API_URL` as a public environment variable. `frontend/amplify.yml` contains the build settings.
