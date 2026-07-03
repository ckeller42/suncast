from datetime import UTC, datetime
from pathlib import Path

import pytest

from suncast.providers.forecast_solar import ProviderError, RateLimited, parse_estimate

BODY = (Path(__file__).parent / "fixtures" / "forecast_solar_ok.json").read_bytes()
NOW = datetime(2026, 7, 3, 5, tzinfo=UTC)


def test_parse_caps_watts_and_sums_daily():
    s = parse_estimate(200, BODY, cap_w=200, now=NOW)
    watts = {p.ts.isoformat(): p.watts for p in s.points}
    assert watts["2026-07-03T12:00:00+00:00"] == 200  # 240 capped
    assert s.daily_wh["2026-07-03"] == 0 + 120 + 200 + 90  # capped sum
    assert s.daily_wh["2026-07-04"] == 200  # 260 capped
    assert s.provider == "forecast.solar" and s.fetched_at == NOW
    assert all(p.ts.tzinfo is not None for p in s.points)


def test_429_raises_ratelimited():
    with pytest.raises(RateLimited):
        parse_estimate(429, b"{}", 200, NOW)


def test_bad_json_raises_provider_error():
    with pytest.raises(ProviderError):
        parse_estimate(200, b"not json", 200, NOW)


def test_500_raises_provider_error_with_status():
    with pytest.raises(ProviderError, match="500"):
        parse_estimate(500, b"", 200, NOW)
