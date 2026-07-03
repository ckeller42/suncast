from datetime import UTC, datetime, timedelta

from suncast.models import DailyRatio, ForecastPoint, ForecastSeries, PanelConfig
from suncast.store import Store

TS = datetime(2026, 7, 3, 5, 0, tzinfo=UTC)
S = ForecastSeries(
    points=[ForecastPoint(TS, 100.0)],
    daily_wh={"2026-07-03": 800.0, "2026-07-04": 900.0},
    provider="forecast.solar",
    fetched_at=TS,
)


def store(tmp_path):
    return Store(str(tmp_path / "s.db"))


def test_snapshot_roundtrip_and_earliest_wins(tmp_path):
    st = store(tmp_path)
    st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    later = ForecastSeries(
        S.points,
        {"2026-07-03": 999.0},
        "forecast.solar",
        TS + timedelta(hours=2),
    )
    st.save_snapshot(later, 48.77, 9.16, PanelConfig())
    assert st.snapshot_forecast_wh("2026-07-03") == 800.0  # earliest on that day
    assert st.snapshot_forecast_wh("2026-06-01") is None
    assert st.has_snapshot_today("2026-07-03") is True


def test_ratios_upsert_and_order(tmp_path):
    st = store(tmp_path)
    sid = st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    st.save_ratio(DailyRatio("2026-07-01", 800, 600, 0.75), sid)
    st.save_ratio(DailyRatio("2026-07-02", 800, 720, 0.90), sid)
    st.save_ratio(DailyRatio("2026-07-01", 800, 640, 0.80), sid)  # replace
    rs = st.ratios()
    assert [r.day for r in rs] == ["2026-07-02", "2026-07-01"]
    assert rs[1].ratio == 0.80
    assert st.has_ratio("2026-07-02") and not st.has_ratio("2026-07-03")


def test_panel_persists(tmp_path):
    st = store(tmp_path)
    assert st.get_panel() == PanelConfig()
    st.set_panel(PanelConfig(panel_wp=300))
    assert st.get_panel().panel_wp == 300


def test_last_snapshot_age(tmp_path):
    st = store(tmp_path)
    assert st.last_snapshot_age_s(TS) is None
    st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    assert st.last_snapshot_age_s(TS + timedelta(seconds=60)) == 60.0


def test_panel_roundtrip_uses_dataclass_fields(tmp_path):
    from dataclasses import asdict

    st = store(tmp_path)
    p = PanelConfig(panel_wp=300, tilt_deg=10.0, azimuth_deg=-5.0, charger_limit_w=180, damping=0.1)
    st.set_panel(p)
    assert asdict(st.get_panel()) == asdict(p)
