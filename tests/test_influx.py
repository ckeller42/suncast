from datetime import UTC, datetime

from suncast.config import load
from suncast.influx import InfluxReader, flux_hourly_field

BASE = {"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"}
CFG = load(BASE)


def t(h):
    return datetime(2026, 7, 2, h, tzinfo=UTC)


def test_flux_contains_day_window_and_names():
    q = flux_hourly_field(CFG, "2026-07-02", CFG.pv_power_field)
    for frag in [
        'from(bucket: "victron")',
        "2026-07-02T00:00:00Z",
        "2026-07-03T00:00:00Z",
        '"pv_power"',
        "aggregateWindow(every: 1h",
        'timeSrc: "_start"',
        'r._measurement == "victron"',
    ]:
        assert frag in q


def _bulk_query(power_rows, state_rows):
    def q(flux):
        if '"pv_power"' in flux:
            return power_rows
        if '"charge_state"' in flux:
            return state_rows
        return []

    return q


def test_actual_bulk_hourly_keeps_only_bulk_hours():
    power = [(t(8), 90.0), (t(9), 120.0), (t(10), 8.0), (t(11), 5.0)]
    state = [(t(8), 3.0), (t(9), 3.2), (t(10), 4.0), (t(11), 5.0)]  # 10/11h throttled
    r = InfluxReader(CFG, _bulk_query(power, state))
    out = r.actual_bulk_hourly("2026-07-02")
    assert out == {t(8).isoformat(): 90.0, t(9).isoformat(): 120.0}


def test_actual_bulk_hourly_empty_when_no_state_data():
    r = InfluxReader(CFG, _bulk_query([(t(8), 90.0)], []))
    assert r.actual_bulk_hourly("2026-07-02") == {}


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


def test_latest_location_survives_missing_range():
    def q(flux):
        now = datetime.now(UTC)
        if '"lat"' in flux:
            return [(now, 48.77)]
        if '"lon"' in flux:
            return [(now, 9.16)]
        return []

    lat, lon, rng, age = InfluxReader(CFG, q).latest_location()
    assert (lat, lon, rng) == (48.77, 9.16, 0.0)


def test_forecast_lines_line_protocol():
    from suncast.influx import forecast_lines

    hourly = [["2026-07-04T12:00:00+00:00", 240.0, 192.0]]
    lines = forecast_lines("solar_forecast", "forecast.solar", hourly, 0.8)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("solar_forecast,provider=forecast.solar ")
    assert "raw_w=240.0" in line and "corrected_w=192.0" in line and "factor=0.8" in line
    assert line.endswith(" 1783166400000000000")  # 2026-07-04T12:00Z in ns


def test_haversine_km_known_distance():
    from suncast.influx import haversine_km

    # Stuttgart -> Munich ~190 km
    d = haversine_km(48.7758, 9.1829, 48.1372, 11.5756)
    assert 185 < d < 200
    assert haversine_km(48.0, 9.0, 48.0, 9.0) == 0.0


def test_max_drift_km_uses_furthest_track_point():
    # Track: stays put at 08h, 137 km away at 14h.
    def q(flux):
        if '"lat"' in flux:
            return [(t(8), 48.77), (t(14), 48.14)]
        if '"lon"' in flux:
            return [(t(8), 9.18), (t(14), 11.58)]
        return []

    drift = InfluxReader(CFG, q).max_drift_km("2026-07-02", 48.77, 9.18)
    assert drift is not None and 130 < drift < 200


def test_max_drift_km_none_without_track():
    assert InfluxReader(CFG, lambda flux: []).max_drift_km("2026-07-02", 48.77, 9.18) is None
