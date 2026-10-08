"""The README config tables (included by docs/reference/configuration.md) must match config.py."""

import re
from pathlib import Path

from suncast.config import load

README = Path(__file__).resolve().parent.parent / "README.md"
REQUIRED = {"INFLUX_URL", "INFLUX_ORG", "INFLUXDB_TOKEN"}


def _rows(marker: str) -> dict[str, tuple[bool, str]]:
    text = README.read_text()
    block = text.split(f"<!-- {marker}:start -->")[1].split(f"<!-- {marker}:end -->")[0]
    out = {}
    for line in block.splitlines():
        m = re.match(r"\|\s*`([A-Z_]+)`(\s*\*)?\s*\|\s*(.+?)\s*\|", line)
        if m:
            out[m.group(1)] = (bool(m.group(2)), m.group(3).strip("`"))
    return out


def test_service_table_matches_config():
    base = {"INFLUX_URL": "u", "INFLUX_ORG": "o", "INFLUXDB_TOKEN": "t"}
    cfg = load(base)
    env_to_attr = {
        "VICTRON_BUCKET": cfg.victron_bucket,
        "VICTRON_MEASUREMENT": cfg.victron_measurement,
        "CHARGE_STATE_FIELD": cfg.charge_state_field,
        "PV_POWER_FIELD": cfg.pv_power_field,
        "GEO_BUCKET": cfg.geo_bucket,
        "GEO_MEASUREMENT": cfg.geo_measurement,
        "SUNCAST_PORT": cfg.port,
        "SUNCAST_DB": cfg.db_path,
        "SUNCAST_WINDOW_DAYS": cfg.window_days,
        "SUNCAST_MIN_SAMPLES": cfg.min_samples,
        "SUNCAST_CLAMP_LO": cfg.clamp_lo,
        "SUNCAST_CLAMP_HI": cfg.clamp_hi,
        "SUNCAST_CACHE_TTL_S": cfg.cache_ttl_s,
        "PROVIDER": cfg.provider,
        "PROVIDER_SECONDARY": cfg.provider_secondary,
        "FORECAST_MEASUREMENT": cfg.forecast_measurement,
        "FORECAST_BUCKET": cfg.forecast_bucket,
        "DRIFT_KM_MAX": cfg.drift_km_max,
    }
    rows = _rows("config-service")
    assert set(rows) == REQUIRED | set(env_to_attr)
    assert {k for k, (req, _) in rows.items() if req} == REQUIRED
    for name, value in env_to_attr.items():
        documented = rows[name][1]
        if isinstance(value, float):
            assert float(documented) == value, name
        else:
            assert documented == str(value), name


def test_offline_table_lists_the_tool_variables():
    rows = _rows("config-offline")
    assert set(rows) == {"HOME_LAT", "HOME_LON", "BACKTEST_OUT_DIR"}
    assert rows["HOME_LAT"][1] == "48.77" and rows["HOME_LON"][1] == "9.16"
    assert rows["BACKTEST_OUT_DIR"][1] == "docs/superpowers/results"
