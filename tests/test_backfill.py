from datetime import UTC, date, datetime

from suncast.backfill import ERA5_PROVIDER, backfill, day_location, pv_first_day
from suncast.config import load
from suncast.models import PanelConfig
from suncast.providers.open_meteo import archive_url

CFG = load({"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"})
PANEL = PanelConfig()
HOME = (48.77, 9.16)


def test_archive_url_shape():
    u = archive_url(48.83, 8.28, "2026-06-01", "2026-06-01", PANEL)
    assert "archive-api.open-meteo.com" in u
    assert "start_date=2026-06-01" in u and "end_date=2026-06-01" in u
    assert "global_tilted_irradiance" in u and "timezone=UTC" in u


def test_day_location_falls_back_to_home_without_geo():
    assert day_location(lambda f: [], CFG, "2026-06-01", HOME) == HOME


def test_day_location_uses_geo_mean():
    def q(flux):
        if '"lat"' in flux:
            return [(None, 48.9)]
        if '"lon"' in flux:
            return [(None, 9.1)]
        return []

    assert day_location(q, CFG, "2026-07-02", HOME) == (48.9, 9.1)


def test_pv_first_day():
    def q(flux):
        return [(datetime(2026, 5, 31, 18, tzinfo=UTC), 0.0)]

    assert pv_first_day(q, CFG) == date(2026, 5, 31)


def test_backfill_writes_era5_lines_per_day():
    # pv starts 2026-06-01; backfill through 2026-06-02 -> 2 days.
    ARCHIVE_BODY = (
        b'{"hourly": {"time": ["DAYT11:00", "DAYT12:00"],'
        b' "global_tilted_irradiance": [500.0, 1000.0]}}'
    )
    written = []

    def query(flux):
        if "first()" in flux:  # pv_first_day
            return [(datetime(2026, 6, 1, 18, tzinfo=UTC), 0.0)]
        return []  # no geo -> home

    def fetch(url):
        # url carries start_date=YYYY-MM-DD; echo it into the body's timestamps
        day = url.split("start_date=")[1][:10]
        return 200, ARCHIVE_BODY.replace(b"DAY", day.encode())

    summary = backfill(
        CFG,
        query,
        written.extend,
        fetch,
        PANEL,
        HOME,
        date(2026, 6, 2),
        now=lambda: datetime.now(UTC),
    )
    assert summary["days"] == 2
    assert summary["written_points"] == 4  # 2 hours x 2 days
    assert all(f"{CFG.forecast_measurement},provider={ERA5_PROVIDER}" in ln for ln in written)
    # 500 W/m^2 -> 130 W, 1000 -> capped 200
    assert any("raw_w=130.0" in ln for ln in written)
    assert any("raw_w=200.0" in ln for ln in written)
