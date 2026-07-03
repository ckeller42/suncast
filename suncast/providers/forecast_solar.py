import json
from collections import defaultdict
from datetime import UTC, datetime

from suncast.models import ForecastPoint, ForecastSeries


class ProviderError(Exception):
    """Base exception for provider errors."""

    pass


class RateLimited(ProviderError):
    """Raised when provider returns 429 (rate limit exceeded)."""

    pass


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
