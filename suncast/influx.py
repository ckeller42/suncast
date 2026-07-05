from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from suncast.config import Config

QueryFn = Callable[[str], list[tuple[datetime, float]]]
WriteFn = Callable[[list[str]], None]  # line-protocol lines -> write to the bucket


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in km."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def flux_geo_track(cfg: Config, day: str, field: str) -> str:
    """All values of a geo field over one UTC day (hourly-sampled)."""
    start = f"{day}T00:00:00Z"
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    end = f"{next_day}T00:00:00Z"
    return (
        f'from(bucket: "{cfg.geo_bucket}")\n'
        f"  |> range(start: {start}, stop: {end})\n"
        f'  |> filter(fn: (r) => r._measurement == "{cfg.geo_measurement}")\n'
        f'  |> filter(fn: (r) => r._field == "{field}")\n'
        f"  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n"
    )


def forecast_lines(measurement: str, provider: str, hourly: list, factor: float) -> list[str]:
    """Line-protocol points for a forecast: one per hour, raw + corrected W.

    hourly is [[iso_ts, raw_w, corrected_w], ...] (apply_factor shape); factor
    is recorded for traceability. Timestamps in ns.
    """
    lines = []
    for iso, raw_w, corr_w in hourly:
        ts_ns = int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)
        lines.append(
            f"{measurement},provider={provider} "
            f"raw_w={float(raw_w)},corrected_w={float(corr_w)},factor={float(factor)} {ts_ns}"
        )
    return lines


def flux_hourly_field(cfg: Config, day: str, field: str) -> str:
    """Build a flux query for hourly means of one Victron field on a given day.

    Windows are labeled by their START (timeSrc: "_start") so hour keys line up
    with the forecast's hourly points.
    """
    start = f"{day}T00:00:00Z"
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    end = f"{next_day}T00:00:00Z"

    return (
        f'from(bucket: "{cfg.victron_bucket}")\n'
        f"  |> range(start: {start}, stop: {end})\n"
        f'  |> filter(fn: (r) => r._measurement == "{cfg.victron_measurement}")\n'
        f'  |> filter(fn: (r) => r._field == "{field}")\n'
        f'  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")\n'
    )


class InfluxReader:
    """Read Victron PV power and location data from InfluxDB."""

    def __init__(self, cfg: Config, query: QueryFn):
        """Initialize with config and query function.

        Args:
            cfg: Config instance
            query: QueryFn that takes flux string, returns [(time, value), ...]
        """
        self.cfg = cfg
        self.query = query

    def actual_bulk_hourly(self, day: str) -> dict[str, float]:
        """Hourly absorbed Wh for hours the charger ran UNTHROTTLED (bulk).

        pv_power measures what the battery accepted; in absorption/float the
        MPPT throttles and the value says nothing about panel potential. Only
        bulk hours (charge_state mean in [2.5, 3.5)) are a valid comparison
        against the forecast.

        Returns:
            {iso_hour_start_utc: wh} for bulk hours with power data. Empty dict
            when no bulk hours (or no data) exist for the day.
        """
        power = dict(self.query(flux_hourly_field(self.cfg, day, self.cfg.pv_power_field)))
        state = dict(self.query(flux_hourly_field(self.cfg, day, self.cfg.charge_state_field)))

        out: dict[str, float] = {}
        for t, wh in power.items():
            cs = state.get(t)
            if cs is None or not (2.5 <= cs < 3.5):
                continue
            out[t.astimezone(UTC).isoformat()] = wh
        return out

    def max_drift_km(self, day: str, ref_lat: float, ref_lon: float) -> float | None:
        """Furthest the van roamed from (ref_lat, ref_lon) during `day`, in km.

        Uses the geo track (celloc). None when no track exists for the day —
        caller decides whether that means "assume parked" or "skip".
        """
        lats = dict(self.query(flux_geo_track(self.cfg, day, "lat")))
        lons = dict(self.query(flux_geo_track(self.cfg, day, "lon")))
        common = sorted(set(lats) & set(lons))
        if not common:
            return None
        return max(haversine_km(ref_lat, ref_lon, lats[t], lons[t]) for t in common)

    def latest_location(self) -> tuple[float, float, float, float] | None:
        """Get latest lat, lon, range_m and age from geo bucket.

        Returns:
            (lat, lon, range_m, age_seconds) or None if lat or lon is empty.
            range_m falls back to 0.0 when absent.
        """
        lat = self._last("lat")
        if not lat:
            return None

        lon = self._last("lon")
        if not lon:
            return None

        rng = self._last("range_m")

        lat_time, lat_val = lat
        lon_time, lon_val = lon

        # Age from lat record timestamp
        age_s = (datetime.now(UTC) - lat_time).total_seconds()

        # range_m falls back to 0.0 if not present
        rng_val = rng[1] if rng else 0.0

        return (lat_val, lon_val, rng_val, age_s)

    def _last(self, field: str) -> tuple[datetime, float] | None:
        """Query last value for a field from geo bucket.

        Args:
            field: Field name (lat, lon, range_m)

        Returns:
            (timestamp, value) or None if empty.
        """
        flux = (
            f'from(bucket: "{self.cfg.geo_bucket}")\n'
            f"  |> range(start: -30d)\n"
            f'  |> filter(fn: (r) => r._measurement == "{self.cfg.geo_measurement}")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")\n'
            f"  |> last()\n"
        )
        rows = self.query(flux)
        if not rows:
            return None
        return rows[0]


def make_write_fn(cfg: Config) -> WriteFn:
    """Create a write function using influxdb_client (synchronous writes)."""
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS

    client = InfluxDBClient(url=cfg.influx_url, org=cfg.influx_org, token=cfg.influx_token)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    bucket = cfg.victron_bucket  # shared buspi bucket

    def write(lines: list[str]) -> None:
        write_api.write(bucket=bucket, record=lines)

    return write


def make_query_fn(cfg: Config) -> QueryFn:
    """Create a query function using influxdb_client.

    Args:
        cfg: Config with influx_url, influx_org, influx_token

    Returns:
        QueryFn that executes flux queries and returns results.
    """
    from influxdb_client import InfluxDBClient

    client = InfluxDBClient(url=cfg.influx_url, org=cfg.influx_org, token=cfg.influx_token)
    query_api = client.query_api()

    def query(flux: str) -> list[tuple[datetime, float]]:
        records = query_api.query(flux)
        result = []
        for table in records:
            for record in table.records:
                result.append((record.get_time(), record.get_value()))
        return result

    return query
