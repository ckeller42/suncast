from datetime import UTC, datetime

from suncast.jobs import Deps, daily_tick
from suncast.models import ForecastPoint, ForecastSeries, PanelConfig
from suncast.store import Store

NOW = datetime(2026, 7, 3, 5, 30, tzinfo=UTC)


def hour(day_iso, h):
    d = datetime.fromisoformat(day_iso).replace(tzinfo=UTC)
    return d.replace(hour=h)


def series_for(day_iso, watts_by_hour, fetched_at):
    pts = [ForecastPoint(hour(day_iso, h), w) for h, w in watts_by_hour.items()]
    return ForecastSeries(
        points=pts,
        daily_wh={day_iso: float(sum(watts_by_hour.values()))},
        provider="forecast.solar",
        fetched_at=fetched_at,
    )


TODAY_SERIES = series_for("2026-07-03", {12: 100}, NOW)


class FakeProvider:
    def __init__(self):
        self.calls = []

    def forecast(self, lat, lon, panel, days=3):
        self.calls.append((lat, lon))
        return TODAY_SERIES


class FakeInflux:
    def __init__(self, loc, bulk, drift=0.0):
        self._loc, self._bulk, self._drift = loc, bulk, drift

    def latest_location(self):
        return self._loc

    def actual_bulk_hourly(self, day):
        return self._bulk

    def max_drift_km(self, day, ref_lat, ref_lon):
        return self._drift


def deps(tmp_path, loc=(48.77, 9.16, 20.0, 60.0), bulk=None, drift=0.0):
    return Deps(
        FakeProvider(),
        Store(str(tmp_path / "s.db")),
        FakeInflux(loc, bulk if bulk is not None else {}, drift),
        lambda: NOW,
    )


def test_snapshots_once_per_day(tmp_path):
    d = deps(tmp_path)
    r1 = daily_tick(d)
    r2 = daily_tick(d)
    assert r1["snapshotted"] and not r2["snapshotted"]
    assert len(d.provider.calls) == 1


def test_ratio_from_bulk_hours_only(tmp_path):
    # Yesterday's archived forecast: 100 W @8h, 200 W @9h, 500 W @12h.
    bulk = {
        hour("2026-07-02", 8).isoformat(): 80.0,
        hour("2026-07-02", 9).isoformat(): 160.0,
    }
    d = deps(tmp_path, bulk=bulk)
    y = series_for("2026-07-02", {8: 100, 9: 200, 12: 500}, datetime(2026, 7, 2, 5, tzinfo=UTC))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    out = daily_tick(d)
    assert out["ratio_day"] == "2026-07-02"
    r = d.store.ratios()[0]
    # Only the two bulk hours count: forecast 300, actual 240 -> 0.8.
    # The throttled 12h (500 W forecast) must NOT contaminate the ratio.
    assert (r.forecast_wh, r.actual_wh, r.ratio) == (300.0, 240.0, 0.8)


def test_too_few_bulk_hours_skips_ratio(tmp_path):
    bulk = {hour("2026-07-02", 9).isoformat(): 160.0}  # 1 < MIN_BULK_HOURS
    d = deps(tmp_path, bulk=bulk)
    y = series_for("2026-07-02", {9: 200}, datetime(2026, 7, 2, 5, tzinfo=UTC))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    out = daily_tick(d)
    assert out["ratio_day"] is None
    assert "bulk hour" in (out["skipped"] or "")
    assert d.store.ratios() == []


def test_low_bulk_forecast_skips_ratio(tmp_path):
    bulk = {
        hour("2026-07-02", 7).isoformat(): 10.0,
        hour("2026-07-02", 8).isoformat(): 12.0,
    }
    d = deps(tmp_path, bulk=bulk)
    y = series_for("2026-07-02", {7: 15, 8: 20}, datetime(2026, 7, 2, 5, tzinfo=UTC))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())  # 35 Wh < 50 threshold
    out = daily_tick(d)
    assert out["ratio_day"] is None
    assert d.store.ratios() == []


def test_no_location_skips_snapshot_gracefully(tmp_path):
    out = daily_tick(deps(tmp_path, loc=None))
    assert not out["snapshotted"] and out["skipped"]


def test_snapshot_mirrors_forecast_to_influx_when_write_set(tmp_path):
    captured = []
    d = deps(tmp_path)
    d.write = captured.extend
    daily_tick(d)
    assert captured, "expected forecast lines written"
    assert all(line.startswith("solar_forecast,provider=forecast.solar ") for line in captured)
    assert any("raw_w=100.0" in line for line in captured)


def test_write_failure_does_not_break_tick(tmp_path):
    def boom(lines):
        raise RuntimeError("influx down")

    d = deps(tmp_path)
    d.write = boom
    out = daily_tick(d)
    assert out["snapshotted"] is True  # snapshot survives a failed mirror


def test_travel_day_skips_ratio(tmp_path):
    # Enough bulk hours + forecast, but the van roamed 137 km -> skip.
    bulk = {
        hour("2026-07-02", 8).isoformat(): 80.0,
        hour("2026-07-02", 9).isoformat(): 160.0,
    }
    d = deps(tmp_path, bulk=bulk, drift=137.0)
    y = series_for("2026-07-02", {8: 100, 9: 200}, datetime(2026, 7, 2, 5, tzinfo=UTC))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    out = daily_tick(d)
    assert out["ratio_day"] is None
    assert "location mismatch" in (out["skipped"] or "")
    assert d.store.ratios() == []


def test_parked_day_still_calibrates(tmp_path):
    bulk = {
        hour("2026-07-02", 8).isoformat(): 80.0,
        hour("2026-07-02", 9).isoformat(): 160.0,
    }
    d = deps(tmp_path, bulk=bulk, drift=2.0)  # within threshold
    y = series_for("2026-07-02", {8: 100, 9: 200}, datetime(2026, 7, 2, 5, tzinfo=UTC))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    assert daily_tick(d)["ratio_day"] == "2026-07-02"
