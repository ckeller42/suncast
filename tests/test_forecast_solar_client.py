from datetime import UTC, datetime
from pathlib import Path

from suncast.models import PanelConfig
from suncast.providers.forecast_solar import ForecastSolar

BODY = (Path(__file__).parent / "fixtures" / "forecast_solar_ok.json").read_bytes()


def make(now_holder, calls):
    def fetch(url):
        calls.append(url)
        return 200, BODY

    return ForecastSolar(fetch, cache_ttl_s=1800, now=lambda: now_holder[0])


def test_url_and_days_filter():
    calls, now = [], [datetime(2026, 7, 3, 5, tzinfo=UTC)]
    fs = make(now, calls)
    s = fs.forecast(48.7725, 9.1609, PanelConfig(), days=1)
    assert calls[0] == "https://api.forecast.solar/estimate/48.7725/9.1609/0/0/0.26?time=utc"
    assert list(s.daily_wh) == ["2026-07-03"]
    assert all(p.ts.date().isoformat() == "2026-07-03" for p in s.points)


def test_cache_hits_within_ttl_and_expires():
    calls, now = [], [datetime(2026, 7, 3, 5, tzinfo=UTC)]
    fs = make(now, calls)
    fs.forecast(48.7725, 9.1609, PanelConfig(), 2)
    fs.forecast(48.77251, 9.16091, PanelConfig(), 2)  # same 3-decimal bucket
    assert len(calls) == 1
    now[0] = datetime(2026, 7, 3, 6, tzinfo=UTC)  # ttl 1800s passed
    fs.forecast(48.7725, 9.1609, PanelConfig(), 2)
    assert len(calls) == 2
