import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import astuple
from datetime import UTC, datetime

import httpx

from suncast.models import ForecastPoint, ForecastSeries, PanelConfig


class ProviderError(Exception):
    """Base exception for provider errors."""

    pass


class RateLimited(ProviderError):
    """Raised when provider returns 429 (rate limit exceeded)."""

    pass


FetchFn = Callable[[str], tuple[int, bytes]]


def parse_estimate(
    status: int, body: bytes, cap_w: float, now: datetime
) -> ForecastSeries:
    """Parse Forecast.Solar response.

    Args:
        status: HTTP status code
        body: Response body as bytes
        cap_w: Charger cap in watts
        now: Current time (datetime with UTC timezone)

    Returns:
        ForecastSeries with parsed data

    Raises:
        RateLimited: If status is 429
        ProviderError: If status is not 200 or JSON is malformed
    """
    if status == 429:
        raise RateLimited("Rate limit exceeded")

    if status != 200:
        raise ProviderError(f"HTTP {status}")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise ProviderError(f"Invalid JSON: {e}") from e

    try:
        watts_data = data["result"]["watts"]
    except (KeyError, TypeError) as e:
        raise ProviderError(f"Invalid response structure: {e}") from e

    points = []
    daily_wh: dict[str, float] = defaultdict(float)

    for ts_str, watts in watts_data.items():
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            )
        except (ValueError, TypeError) as e:
            raise ProviderError(f"Invalid timestamp: {e}") from e

        capped_watts = min(watts, cap_w)
        points.append(ForecastPoint(ts=dt, watts=capped_watts))

        # Group by UTC date
        date_key = dt.strftime("%Y-%m-%d")
        daily_wh[date_key] += capped_watts

    # Sort points by timestamp
    points.sort(key=lambda p: p.ts)

    return ForecastSeries(
        points=points,
        daily_wh=dict(daily_wh),
        provider="forecast.solar",
        fetched_at=now,
    )


def default_fetch(url: str) -> tuple[int, bytes]:
    """Fetch from URL using httpx with 20s timeout."""
    response = httpx.get(url, timeout=20)
    return response.status_code, response.content


class ForecastSolar:
    """Forecast.Solar API client with TTL cache."""

    def __init__(
        self,
        fetch: FetchFn,
        cache_ttl_s: int = 1800,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        """Initialize client.

        Args:
            fetch: Function (url) -> (status, body)
            cache_ttl_s: Cache TTL in seconds
            now: Function returning current datetime (UTC)
        """
        self.fetch = fetch
        self.cache_ttl_s = cache_ttl_s
        self.now = now
        self._cache: dict[tuple, tuple[datetime, ForecastSeries]] = {}

    def forecast(
        self, lat: float, lon: float, panel: PanelConfig, days: int = 3
    ) -> ForecastSeries:
        """Forecast solar generation.

        Args:
            lat: Latitude
            lon: Longitude
            panel: Panel configuration
            days: Number of days to keep (filter to first N UTC dates)

        Returns:
            ForecastSeries with filtered points/daily_wh

        Raises:
            RateLimited: If provider returns 429
            ProviderError: If provider returns error
        """
        # Cache key: (rounded lat/lon, panel tuple)
        cache_key = (round(lat, 3), round(lon, 3), astuple(panel))

        # Check cache
        if cache_key in self._cache:
            fetch_time, series = self._cache[cache_key]
            elapsed = (self.now() - fetch_time).total_seconds()
            if elapsed < self.cache_ttl_s:
                # Cache hit, filter by days
                return self._filter_days(series, days)

        # Cache miss or expired
        kwp = panel.panel_wp / 1000
        url = (
            f"https://api.forecast.solar/estimate/{lat:.4f}/{lon:.4f}/"
            f"{panel.tilt_deg:g}/{panel.azimuth_deg:g}/{kwp:g}?time=utc"
        )
        status, body = self.fetch(url)

        # Parse
        series = parse_estimate(status, body, panel.charger_limit_w, self.now())

        # Store in cache
        self._cache[cache_key] = (self.now(), series)

        # Filter by days
        return self._filter_days(series, days)

    def _filter_days(self, series: ForecastSeries, days: int) -> ForecastSeries:
        """Filter series to first N UTC dates.

        Args:
            series: Full series
            days: Number of days to keep

        Returns:
            Filtered series
        """
        # Collect unique UTC dates in order of appearance
        unique_dates = []
        seen = set()
        for point in series.points:
            date_str = point.ts.strftime("%Y-%m-%d")
            if date_str not in seen:
                unique_dates.append(date_str)
                seen.add(date_str)
                if len(unique_dates) >= days:
                    break

        # Filter points and daily_wh
        dates_to_keep = set(unique_dates)
        filtered_points = [
            p for p in series.points if p.ts.strftime("%Y-%m-%d") in dates_to_keep
        ]
        filtered_daily = {
            k: v for k, v in series.daily_wh.items() if k in dates_to_keep
        }

        return ForecastSeries(
            points=filtered_points,
            daily_wh=filtered_daily,
            provider=series.provider,
            fetched_at=series.fetched_at,
        )
