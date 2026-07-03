from datetime import UTC, datetime

from suncast.models import ForecastPoint, ForecastSeries, PanelConfig


def test_panel_defaults_match_spec():
    p = PanelConfig()
    expected = (260, 0.0, 0.0, 200, 0.0)
    actual = (p.panel_wp, p.tilt_deg, p.azimuth_deg, p.charger_limit_w, p.damping)
    assert actual == expected


def test_forecast_series_holds_points():
    ts = datetime(2026, 7, 3, 12, tzinfo=UTC)
    s = ForecastSeries(
        points=[ForecastPoint(ts, 150.0)],
        daily_wh={"2026-07-03": 900.0},
        provider="forecast.solar",
        fetched_at=ts,
    )
    assert s.points[0].watts == 150.0 and s.daily_wh["2026-07-03"] == 900.0
