# HeatShield plan contract

The backend emits a canonical plan with `status` values `DRAFT`, `APPROVED`, and `SENT`.

Safety invariants:

- `forecast_heat_risk` is an estimate from forecast weather and site factors.
- `measured_wbgt` is always `false`.
- Drafts cannot be sent until a supervisor approves them.
- Work intervals, risk levels, rest intervals, and water intervals come from the deterministic planner.
- AI wording is optional and cannot replace canonical values.

The user-facing warning is always:

> Forecast-based guidance only. Not measured WBGT or medical advice.

