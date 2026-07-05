"""Open-Meteo provider.

Open-Meteo serves raw tilted irradiance (W/m²) from the best regional weather
model (DWD ICON-D2 at 2 km over Central Europe, MeteoFrance AROME over France),
NOT panel-modeled watts. We convert with the STC relation

    watts = panel_wp * GTI / 1000   (then clamp to the charger cap)

so the series systematically reads high vs a panel model — that optimism is a
constant factor the self-calibration divides back out. Free, no API key,
16-day horizon.
"""

import json
from collections.abc import Callable
from dataclasses import astuple
from datetime import UTC, datetime

import httpx

from suncast.models import ForecastPoint, ForecastSeries, PanelConfig
from suncast.providers.forecast_solar import ProviderError, RateLimited

FetchFn = Callable[[str], tuple[int, bytes]]

STC_IRRADIANCE = 1000.0  # W/m² at standard test conditions

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def _watts_from_gti(gti: float, panel: PanelConfig) -> float:
    """Convert tilted irradiance (W/m²) to capped panel watts."""
    w = panel.panel_wp * (gti or 0.0) / STC_IRRADIANCE
    return min(w, panel.charger_limit_w)


def parse_estimate(status: int, body: bytes, panel: PanelConfig, now: datetime) -> ForecastSeries:
    """Parse an Open-Meteo hourly global_tilted_irradiance response."""
    if status == 429:
        raise RateLimited("Rate limit exceeded")
    if status != 200:
        raise ProviderError(f"HTTP {status}")

    try:
        data = json.loads(body)
        times = data["hourly"]["time"]
        gtis = data["hourly"]["global_tilted_irradiance"]
    except (ValueError, KeyError, TypeError) as e:
        raise ProviderError(f"Invalid response structure: {e}") from e

    points: list[ForecastPoint] = []
    daily_wh: dict[str, float] = {}
    for ts_str, gti in zip(times, gtis, strict=False):
        # Open-Meteo times are naive ISO in the requested tz (we ask for UTC).
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        watts = _watts_from_gti(gti, panel)
        points.append(ForecastPoint(ts=dt, watts=watts))
        day = dt.strftime("%Y-%m-%d")
        daily_wh[day] = daily_wh.get(day, 0.0) + watts

    points.sort(key=lambda p: p.ts)
    return ForecastSeries(points=points, daily_wh=daily_wh, provider="open-meteo", fetched_at=now)


def default_fetch(url: str) -> tuple[int, bytes]:
    """Fetch from URL using httpx with 20s timeout."""
    response = httpx.get(url, timeout=20)
    return response.status_code, response.content


class OpenMeteo:
    """Open-Meteo API client with TTL cache (mirrors ForecastSolar's shape)."""

    def __init__(
        self,
        fetch: FetchFn,
        cache_ttl_s: int = 1800,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.fetch = fetch
        self.cache_ttl_s = cache_ttl_s
        self.now = now
        self._cache: dict[tuple, tuple[datetime, ForecastSeries]] = {}

    def forecast(self, lat: float, lon: float, panel: PanelConfig, days: int = 3) -> ForecastSeries:
        cache_key = (round(lat, 3), round(lon, 3), astuple(panel), days)
        if cache_key in self._cache:
            fetch_time, series = self._cache[cache_key]
            if (self.now() - fetch_time).total_seconds() < self.cache_ttl_s:
                return series

        url = (
            f"{BASE_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
            f"&hourly=global_tilted_irradiance"
            f"&tilt={panel.tilt_deg:g}&azimuth={panel.azimuth_deg:g}"
            f"&forecast_days={days}&timezone=UTC"
        )
        status, body = self.fetch(url)
        series = parse_estimate(status, body, panel, self.now())
        self._cache[cache_key] = (self.now(), series)
        return series
