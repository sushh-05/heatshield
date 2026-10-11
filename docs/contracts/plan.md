# HeatShield plan contract

The backend emits a canonical plan with `status` values `DRAFT`, `APPROVED`, and `SENT`.

Safety invariants:

- `forecast_heat_risk` is an estimate from forecast weather and site factors.
- `measured_wbgt` is always `false`.
- Drafts cannot be sent until a supervisor approves them.
- Work intervals, risk levels, rest intervals, and water intervals come from the deterministic planner.
- AI wording is optional and cannot replace canonical values.

The user-facing warning is always:

> Based on weather data — not a medical test. Always follow your site's safety rules and your supervisor's instructions.

A plan generated while `HEATSHIELD_DEMO=true` carries `weather_provider: "DEMO"`. Every message built from it (Telegram, Discord, Slack, email) is prefixed with a fixed demo notice, so a demo plan can never be mistaken for one built from today's real forecast.

Once a plan is `APPROVED` or `SENT`, generating a new plan for the same site and date is refused (`409`) rather than silently replacing it with a fresh `DRAFT`.

