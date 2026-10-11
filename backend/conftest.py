import os

import pytest

# Tests run offline against the canned forecast and without sign-in, unless a test opts out.
os.environ["HEATSHIELD_DEMO"] = "true"
os.environ["HEATSHIELD_OPEN_ACCESS"] = "true"
for name in ("TABLE_NAME", "SUPERVISOR_KEY", "CLERK_SECRET_KEY", "CLERK_JWT_KEY", "TELEGRAM_WEBHOOK_SECRET", "DISCORD_WEBHOOK_SECRET", "DISCORD_PUBLIC_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _clean_state():
    import app

    from core import waterlogging

    for store in (app.SITES, app.PLANS, app.WORKERS, app.ACKS, app.ALERTS, app.WATERLOGGING_REPORTS):
        store.clear()
    waterlogging.reset_state()
    yield
