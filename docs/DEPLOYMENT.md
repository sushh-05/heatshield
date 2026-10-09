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

## Amplify

Connect the GitHub repository, choose the `main` branch, set the app root to `frontend`, and add `VITE_API_URL` as a public environment variable. `frontend/amplify.yml` contains the build settings.
