from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from suncast.config import Config

QueryFn = Callable[[str], list[tuple[datetime, float]]]


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
