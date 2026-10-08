import pytest

from suncast.config import load

BASE = {"INFLUX_URL": "http://localhost:8086", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"}


def test_defaults():
    c = load(BASE)
    assert c.victron_bucket == "victron" and c.port == 8090 and c.clamp_hi == 1.3
    assert c.db_path == "/var/lib/suncast/suncast.db"


def test_overrides():
    c = load(
        BASE
        | {
            "SUNCAST_PORT": "9000",
            "VICTRON_MEASUREMENT": "vedirect",
            "SUNCAST_CLAMP_LO": "0.5",
        }
    )
    assert c.port == 9000 and c.victron_measurement == "vedirect" and c.clamp_lo == 0.5


def test_missing_required_exits():
    with pytest.raises(SystemExit):
        load({"INFLUX_URL": "x"})


def test_forecast_bucket_defaults_to_buspi_and_is_independent_of_victron():
    assert load(BASE).forecast_bucket == "buspi"
    c = load(BASE | {"VICTRON_BUCKET": "mppt", "FORECAST_BUCKET": "forecasts"})
    assert c.victron_bucket == "mppt" and c.forecast_bucket == "forecasts"


def test_tz_setting_is_gone():
    assert not hasattr(load(BASE), "tz")
