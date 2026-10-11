from dataclasses import asdict, dataclass, field
from typing import Literal

RiskLevel = Literal["LOW", "MODERATE", "HIGH", "EXTREME"]
PlanStatus = Literal["DRAFT", "APPROVED", "SENT"]

@dataclass
class SiteProfile:
    site_id: str
    name: str
    latitude: float
    longitude: float
    timezone: str = "Asia/Kolkata"
    work_start_hour: int = 6
    work_end_hour: int = 18
    workload: str = "MODERATE"
    acclimatization: str = "ACCLIMATIZED"
    clothing: str = "NORMAL"
    default_language: str = "en"
    discord_webhook_url: str | None = None
    email_recipients: list[str] = field(default_factory=list)
    telegram_bot_username: str | None = None
    discord_invite_url: str | None = None
    # The Clerk organisation (or user) that created the site; None for sites anyone signed in may manage.
    owner_id: str | None = None

@dataclass
class HourlyRisk:
    time: str
    temperature_c: float
    relative_humidity_pct: float
    apparent_temperature_c: float | None
    wind_speed_kmh: float | None
    direct_normal_irradiance_wm2: float | None
    forecast_heat_risk: int
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)

@dataclass
class WorkBlock:
    start: str
    end: str
    level: RiskLevel
    allowed_work: str
    rest_minutes_per_hour: int
    water_every_minutes: int

@dataclass
class HeatPlan:
    schema_version: str
    plan_id: str
    site_id: str
    date: str
    generated_at: str
    mode: str
    weather_provider: str
    method: str
    measured_wbgt: bool
    status: PlanStatus
    hours: list[HourlyRisk]
    blocks: list[WorkBlock]
    approved_by: str | None
    approved_at: str | None
    disclaimer: str

    def to_dict(self):
        return asdict(self)
