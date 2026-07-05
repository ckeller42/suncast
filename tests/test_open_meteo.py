from datetime import UTC, datetime

import pytest

from suncast.models import PanelConfig
from suncast.providers.forecast_solar import ProviderError, RateLimited
from suncast.providers.open_meteo import OpenMeteo, parse_estimate

NOW = datetime(2026, 7, 5, 5, tzinfo=UTC)
PANEL = PanelConfig()  # 260 Wp, cap 200 W

BODY = (
    b'{"hourly": {"time": ["2026-07-05T11:00", "2026-07-05T12:00", "2026-07-06T12:00"],'
    b' "global_tilted_irradiance": [500.0, 1000.0, 200.0]}}'
)


def test_parse_converts_irradiance_and_caps():
    s = parse_estimate(200, BODY, PANEL, NOW)
    w = {p.ts.isoformat(): p.watts for p in s.points}
    # 500 W/m² -> 260*0.5 = 130 W
    assert w["2026-07-05T11:00:00+00:00"] == 130.0
    # 1000 W/m² -> 260 W, capped to 200
    assert w["2026-07-05T12:00:00+00:00"] == 200.0
    assert s.daily_wh["2026-07-05"] == 130.0 + 200.0
    assert s.provider == "open-meteo"


def test_parse_errors():
    with pytest.raises(RateLimited):
        parse_estimate(429, b"", PANEL, NOW)
    with pytest.raises(ProviderError):
        parse_estimate(500, b"", PANEL, NOW)
    with pytest.raises(ProviderError):
        parse_estimate(200, b"{}", PANEL, NOW)


def test_client_caches_and_builds_url():
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, BODY

    c = OpenMeteo(fetch, cache_ttl_s=1800, now=lambda: NOW)
    c.forecast(48.7711, 9.1622, PANEL, days=3)
    c.forecast(48.7711, 9.1622, PANEL, days=3)
    assert len(calls) == 1  # second call served from cache
    assert "global_tilted_irradiance" in calls[0]
    assert "forecast_days=3" in calls[0] and "timezone=UTC" in calls[0]
