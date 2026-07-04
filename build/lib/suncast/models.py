from dataclasses import dataclass
from datetime import datetime


@dataclass
class PanelConfig:
    panel_wp: int = 260
    tilt_deg: float = 0.0
    azimuth_deg: float = 0.0  # 0 = south (Forecast.Solar convention)
    charger_limit_w: int = 200  # Victron MPPT 75/15 ~15 A cap
    damping: float = 0.0


@dataclass
class ForecastPoint:
    ts: datetime  # tz-aware UTC
    watts: float


@dataclass
class ForecastSeries:
    points: list[ForecastPoint]
    daily_wh: dict[str, float]  # "YYYY-MM-DD" (UTC date) -> Wh
    provider: str
    fetched_at: datetime


@dataclass
class DailyRatio:
    day: str  # "YYYY-MM-DD"
    forecast_wh: float
    actual_wh: float
    ratio: float


@dataclass
class Calibration:
    factor: float
    p25: float
    p75: float
    samples: int
    calibrated: bool
