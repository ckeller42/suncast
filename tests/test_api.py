import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from suncast.app import create_app
from suncast.config import load
from suncast.models import DailyRatio, ForecastPoint, ForecastSeries, PanelConfig
from suncast.providers.forecast_solar import RateLimited
from suncast.store import Store

CFG = load({"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"})
NOW = datetime(2026, 7, 3, 12, tzinfo=UTC)
S = ForecastSeries([ForecastPoint(NOW, 240.0)], {"2026-07-03": 1000.0}, "forecast.solar", NOW)


class P:
    def forecast(self, lat, lon, panel, days=3):
        return S


class P429:
    def forecast(self, *a, **k):
        raise RateLimited("quota")


class FakeInflux:
    def latest_location(self):
        return (48.77, 9.16, 20.0, 30.0)

    def actual_day_wh(self, day):
        return 800.0


def client(tmp_path, provider=None):
    store = Store(str(tmp_path / "s.db"))
    for i, r in enumerate([0.8, 0.85, 0.9, 0.8, 0.85, 0.9]):
        store.save_ratio(DailyRatio(f"2026-06-{20 + i:02d}", 1000, 1000 * r, r), 0)
    app = create_app(CFG, provider or P(), store, FakeInflux())
    app.state.no_jobs = True
    return TestClient(app)


def test_forecast_endpoint(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/forecast", json={"lat": 48.77, "lon": 9.16, "days": 2})
    assert r.status_code == 200
    j = r.json()
    assert j["factor"]["calibrated"] and 0.8 <= j["factor"]["factor"] <= 0.9
    assert j["daily"]["2026-07-03"]["raw_wh"] == 1000.0
    assert j["daily"]["2026-07-03"]["corrected_wh"] < 1000.0
    assert "2026-07-03" in j["best_windows"]


def test_forecast_rate_limited_is_429(tmp_path):
    r = client(tmp_path, P429()).post("/api/forecast", json={"lat": 1, "lon": 2})
    assert r.status_code == 429


def test_config_roundtrip(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/config").json()["panel_wp"] == 260
    r = c.post("/api/config", json={**PanelConfig().__dict__, "panel_wp": 300})
    assert r.json()["panel_wp"] == 300 and c.get("/api/config").json()["panel_wp"] == 300


def test_history_and_health_and_location(tmp_path):
    c = client(tmp_path)
    h = c.get("/api/history?days=30").json()
    assert len(h["days"]) == 6 and h["days"][0]["day"] == "2026-06-20"
    assert c.get("/api/current-location").json()["lat"] == 48.77
    assert c.get("/api/health").status_code == 200


def test_forecast_bad_panel_is_422(tmp_path):
    c = client(tmp_path)
    r1 = c.post("/api/forecast", json={"lat": 1, "lon": 2, "panel": {"nope": 5}})
    assert r1.status_code == 422
    r2 = c.post("/api/forecast", json={"lat": 1, "lon": 2, "panel": "x"})
    assert r2.status_code == 422
    r3 = c.post("/api/forecast", json={"lat": 1, "lon": 2, "days": "many"})
    assert r3.status_code == 422


def test_config_bad_types_is_422(tmp_path):
    c = client(tmp_path)
    body = {
        "panel_wp": "big",
        "tilt_deg": 0.0,
        "azimuth_deg": 0.0,
        "charger_limit_w": 200,
        "damping": 0.0,
    }
    assert c.post("/api/config", json=body).status_code == 422


def test_lifespan_no_jobs_clean_startup_shutdown(tmp_path):
    c = client(tmp_path)  # helper sets app.state.no_jobs = True
    with c as ctx:
        assert ctx.get("/api/health").status_code == 200


def test_lifespan_runs_daily_tick(tmp_path, monkeypatch):
    calls = []
    from suncast import jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod,
        "daily_tick",
        lambda deps: calls.append(1) or {"snapshotted": False, "ratio_day": None, "skipped": None},
    )
    store = Store(str(tmp_path / "s.db"))
    app = create_app(CFG, P(), store, FakeInflux())  # no_jobs NOT set
    with TestClient(app):
        deadline = time.time() + 2
        while not calls and time.time() < deadline:
            time.sleep(0.05)
    assert calls, "daily_tick was never invoked by the lifespan loop"


def test_history_keeps_newest_days(tmp_path):
    from suncast.models import DailyRatio

    store = Store(str(tmp_path / "h.db"))
    for i in range(1, 10):  # 9 days: 2026-06-01 .. 2026-06-09
        store.save_ratio(DailyRatio(f"2026-06-{i:02d}", 1000, 800, 0.8), 0)
    app = create_app(CFG, P(), store, FakeInflux())
    app.state.no_jobs = True
    c = TestClient(app)
    days = c.get("/api/history?days=3").json()["days"]
    assert [d["day"] for d in days] == ["2026-06-07", "2026-06-08", "2026-06-09"]


def test_forecast_non_numeric_latlon_is_422(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/forecast", json={"lat": "nope", "lon": 9.16}).status_code == 422


def test_geocode_endpoint(tmp_path):
    c = client(tmp_path)
    c.app.state.geocode_fetch = lambda url, headers: (
        200,
        b'[{"display_name": "Stuttgart", "lat": "48.77", "lon": "9.18"}]',
    )
    r = c.get("/api/geocode?q=Stuttgart")
    assert r.status_code == 200
    assert r.json()["results"][0] == {"label": "Stuttgart", "lat": 48.77, "lon": 9.18}
    assert c.get("/api/geocode?q=").status_code == 422


def test_geocode_upstream_error_is_502(tmp_path):
    c = client(tmp_path)
    c.app.state.geocode_fetch = lambda url, headers: (503, b"")
    assert c.get("/api/geocode?q=x").status_code == 502


def test_forecast_includes_secondary_comparison(tmp_path):
    c = client(tmp_path)

    class FakeSecondary:
        def forecast(self, lat, lon, panel, days):
            from datetime import UTC, datetime

            from suncast.models import ForecastPoint, ForecastSeries

            pts = [ForecastPoint(datetime(2026, 7, 5, 12, tzinfo=UTC), 300.0)]
            return ForecastSeries(pts, {"2026-07-05": 300.0}, "open-meteo", datetime.now(UTC))

    c.app.state.provider_secondary = FakeSecondary()
    r = c.post("/api/forecast", json={"lat": 48.77, "lon": 9.16, "days": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["comparison"]["provider"] == "open-meteo"
    assert body["comparison"]["daily"]["2026-07-05"] == 300.0
    assert body["provider"]  # primary provider name present


def test_forecast_secondary_failure_does_not_break(tmp_path):
    c = client(tmp_path)

    class BoomSecondary:
        def forecast(self, *a, **k):
            raise RuntimeError("down")

    c.app.state.provider_secondary = BoomSecondary()
    r = c.post("/api/forecast", json={"lat": 48.77, "lon": 9.16, "days": 2})
    assert r.status_code == 200
    assert r.json()["comparison"] is None
