from datetime import UTC, datetime

from suncast.jobs import Deps, daily_tick
from suncast.models import ForecastPoint, ForecastSeries, PanelConfig
from suncast.store import Store

NOW = datetime(2026, 7, 3, 5, 30, tzinfo=UTC)
S = ForecastSeries([ForecastPoint(NOW, 100.0)], {"2026-07-03": 800.0}, "forecast.solar", NOW)


class FakeProvider:
    def __init__(self):
        self.calls = []

    def forecast(self, lat, lon, panel, days=3):
        self.calls.append((lat, lon))
        return S


class FakeInflux:
    def __init__(self, loc, wh):
        self._loc, self._wh = loc, wh

    def latest_location(self):
        return self._loc

    def actual_day_wh(self, day):
        return self._wh


def deps(tmp_path, loc=(48.77, 9.16, 20.0, 60.0), wh=640.0):
    return Deps(FakeProvider(), Store(str(tmp_path / "s.db")), FakeInflux(loc, wh), lambda: NOW)


def test_snapshots_once_per_day(tmp_path):
    d = deps(tmp_path)
    r1 = daily_tick(d)
    r2 = daily_tick(d)
    assert r1["snapshotted"] and not r2["snapshotted"]
    assert len(d.provider.calls) == 1


def test_ratio_for_yesterday_from_archived_snapshot(tmp_path):
    d = deps(tmp_path)
    y = ForecastSeries(
        S.points, {"2026-07-02": 800.0}, "forecast.solar", datetime(2026, 7, 2, 5, tzinfo=UTC)
    )
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    out = daily_tick(d)
    assert out["ratio_day"] == "2026-07-02"
    assert d.store.ratios()[0].ratio == 0.8  # 640/800


def test_no_location_skips_snapshot_gracefully(tmp_path):
    out = daily_tick(deps(tmp_path, loc=None))
    assert not out["snapshotted"] and out["skipped"]
