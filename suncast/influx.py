from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from suncast.config import Config

QueryFn = Callable[[str], list[tuple[datetime, float]]]


def flux_actual_hourly(cfg: Config, day: str) -> str:
    """Build flux query for hourly PV power on a given day.

    Args:
        cfg: Config with victron_bucket, victron_measurement, pv_power_field
        day: ISO date string (e.g., "2026-07-02")

    Returns:
        Flux query string with range, measurement filter, aggregateWindow.
    """
    # Parse day and calculate next day for range
    start = f"{day}T00:00:00Z"
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    end = f"{next_day}T00:00:00Z"

    return (
        f'from(bucket: "{cfg.victron_bucket}")\n'
        f'  |> range(start: {start}, stop: {end})\n'
        f'  |> filter(fn: (r) => r._measurement == "{cfg.victron_measurement}")\n'
        f'  |> filter(fn: (r) => r._field == "{cfg.pv_power_field}")\n'
        f'  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n'
    )


def flux_latest_location(cfg: Config) -> str:
    """Build flux query for latest location (lat, lon, range_m).

    Note: Returns 3 separate queries (one per field) for use with _last helper.
    This is a placeholder; actual use needs 3 queries via _last().
    """
    # This function signature is provided per the brief, but usage pattern
    # is through the _last helper in InfluxReader.latest_location()
    pass


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

    def actual_day_wh(self, day: str) -> float | None:
        """Sum hourly power means for a day as Wh.

        Args:
            day: ISO date string

        Returns:
            Sum of hourly means (W) for the day, or None if < 4 buckets.
        """
        flux = flux_actual_hourly(self.cfg, day)
        rows = self.query(flux)

        if len(rows) < 4:
            return None

        # Sum all values; each value is already a mean for 1h window
        # so sum is in Wh (W × 1h = Wh)
        return sum(value for _time, value in rows)

    def latest_location(self) -> tuple[float, float, float, float] | None:
        """Get latest lat, lon, range_m and age from geo bucket.

        Returns:
            (lat, lon, range_m, age_seconds) or None if any field is empty.
        """
        lat = self._last("lat")
        if not lat:
            return None

        lon = self._last("lon")
        if not lon:
            return None

        rng = self._last("range_m")
        if not rng:
            return None

        lat_time, lat_val = lat
        lon_time, lon_val = lon
        rng_time, rng_val = rng

        # Age from lat record timestamp
        age_s = (datetime.now(UTC) - lat_time).total_seconds()

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
            f'  |> range(start: -30d)\n'
            f'  |> filter(fn: (r) => r._measurement == "{self.cfg.geo_measurement}")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")\n'
            f'  |> last()\n'
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

    client = InfluxDBClient(
        url=cfg.influx_url, org=cfg.influx_org, token=cfg.influx_token
    )
    query_api = client.query_api()

    def query(flux: str) -> list[tuple[datetime, float]]:
        records = query_api.query(flux)
        result = []
        for table in records:
            for record in table.records:
                result.append((record.get_time(), record.get_value()))
        return result

    return query
