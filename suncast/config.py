from collections.abc import Mapping
from dataclasses import dataclass


@dataclass
class Config:
    influx_url: str
    influx_org: str
    influx_token: str
    victron_bucket: str = "victron"
    victron_measurement: str = "victron"
    pv_power_field: str = "pv_power"
    geo_bucket: str = "buspi"
    geo_measurement: str = "geo"
    port: int = 8090
    tz: str = "Europe/Berlin"
    db_path: str = "/var/lib/suncast/suncast.db"
    window_days: int = 30
    min_samples: int = 5
    clamp_lo: float = 0.3
    clamp_hi: float = 1.3
    cache_ttl_s: int = 1800


def load(env: Mapping[str, str]) -> Config:
    """Load Config from environment mapping.

    Raises SystemExit if required fields are missing.
    """
    influx_url = env.get("INFLUX_URL")
    influx_org = env.get("INFLUX_ORG")
    influx_token = env.get("INFLUXDB_TOKEN")

    if not (influx_url and influx_org and influx_token):
        raise SystemExit("suncast: set INFLUX_URL, INFLUX_ORG, INFLUXDB_TOKEN")

    return Config(
        influx_url=influx_url,
        influx_org=influx_org,
        influx_token=influx_token,
        victron_bucket=env.get("VICTRON_BUCKET", "victron"),
        victron_measurement=env.get("VICTRON_MEASUREMENT", "victron"),
        pv_power_field=env.get("PV_POWER_FIELD", "pv_power"),
        geo_bucket=env.get("GEO_BUCKET", "buspi"),
        geo_measurement=env.get("GEO_MEASUREMENT", "geo"),
        port=int(env.get("SUNCAST_PORT", "8090")),
        tz=env.get("SUNCAST_TZ", "Europe/Berlin"),
        db_path=env.get("SUNCAST_DB", "/var/lib/suncast/suncast.db"),
        window_days=int(env.get("SUNCAST_WINDOW_DAYS", "30")),
        min_samples=int(env.get("SUNCAST_MIN_SAMPLES", "5")),
        clamp_lo=float(env.get("SUNCAST_CLAMP_LO", "0.3")),
        clamp_hi=float(env.get("SUNCAST_CLAMP_HI", "1.3")),
        cache_ttl_s=int(env.get("SUNCAST_CACHE_TTL_S", "1800")),
    )
