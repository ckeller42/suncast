from datetime import UTC, datetime

from suncast.config import load
from suncast.influx import InfluxReader, flux_actual_hourly

CFG = load({"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"})


def t(h):
    return datetime(2026, 7, 2, h, tzinfo=UTC)


def test_flux_contains_day_window_and_names():
    q = flux_actual_hourly(CFG, "2026-07-02")
    for frag in [
        'from(bucket: "victron")',
        "2026-07-02T00:00:00Z",
        "2026-07-03T00:00:00Z",
        '"pv_power"',
        "aggregateWindow(every: 1h",
        'r._measurement == "victron"',
    ]:
        assert frag in q


def test_actual_day_wh_sums_hourly_means():
    rows = [(t(h), 100.0) for h in range(6, 18)]  # 12 h × 100 W
    r = InfluxReader(CFG, lambda q: rows)
    assert r.actual_day_wh("2026-07-02") == 1200.0


def test_actual_day_wh_none_when_too_sparse():
    r = InfluxReader(CFG, lambda q: [(t(6), 100.0)])  # 1 bucket < 4
    assert r.actual_day_wh("2026-07-02") is None


def test_latest_location_combines_fields():
    def q(flux):
        now = datetime.now(UTC)
        if '"lat"' in flux:
            return [(now, 48.77)]
        if '"lon"' in flux:
            return [(now, 9.16)]
        return [(now, 20.0)]

    lat, lon, rng, age = InfluxReader(CFG, q).latest_location()
    assert (lat, lon, rng) == (48.77, 9.16, 20.0) and age < 5


def test_latest_location_none_when_empty():
    assert InfluxReader(CFG, lambda q: []).latest_location() is None
