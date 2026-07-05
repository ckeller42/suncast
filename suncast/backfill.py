"""Backfill historical expected PV into InfluxDB from Open-Meteo's ERA5 archive.

For each past day we know actual PV (Victron) for, fetch the *actual* past
irradiance (ERA5 reanalysis) at the van's location that day, convert to
expected panel watts, and write it to the `solar_forecast` measurement tagged
`provider=open-meteo-era5`. This lets the Grafana "forecast vs absorbed" panel
span the full history, not just today forward.

ERA5 is a reanalysis (best estimate of what the weather actually was), so this
is an *expected-potential* line to compare against absorbed PV — it is NOT a
forecast and is never used for the forward-looking calibration (which stays on
archived snapshots).
"""

import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from suncast.config import Config, load
from suncast.influx import QueryFn, WriteFn, forecast_lines, make_query_fn, make_write_fn
from suncast.models import PanelConfig
from suncast.providers.open_meteo import archive_url, default_fetch, parse_estimate

logger = logging.getLogger(__name__)

FetchFn = Callable[[str], tuple[int, bytes]]

ERA5_PROVIDER = "open-meteo-era5"


def pv_first_day(query: QueryFn, cfg: Config) -> date | None:
    """UTC date of the earliest pv_power sample, or None."""
    flux = (
        f'from(bucket: "{cfg.victron_bucket}")\n'
        f"  |> range(start: -120d)\n"
        f'  |> filter(fn: (r) => r._measurement == "{cfg.victron_measurement}")\n'
        f'  |> filter(fn: (r) => r._field == "{cfg.pv_power_field}")\n'
        f"  |> first()\n"
    )
    rows = query(flux)
    return rows[0][0].date() if rows and rows[0][0] else None


def day_location(
    query: QueryFn, cfg: Config, day: str, home: tuple[float, float]
) -> tuple[float, float]:
    """Mean van location on `day` from the geo track, or `home` if none."""
    start = f"{day}T00:00:00Z"
    nxt = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    end = f"{nxt}T00:00:00Z"

    def mean_field(field: str) -> float | None:
        flux = (
            f'from(bucket: "{cfg.geo_bucket}")\n'
            f"  |> range(start: {start}, stop: {end})\n"
            f'  |> filter(fn: (r) => r._measurement == "{cfg.geo_measurement}")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")\n'
            f"  |> group()\n"
            f"  |> mean()\n"
        )
        rows = query(flux)
        return rows[0][1] if rows else None

    lat, lon = mean_field("lat"), mean_field("lon")
    if lat is None or lon is None:
        return home
    return (lat, lon)


def backfill(
    cfg: Config,
    query: QueryFn,
    write: WriteFn,
    fetch: FetchFn,
    panel: PanelConfig,
    home: tuple[float, float],
    end_day: date,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    """Write ERA5 expected PV for every day [pv_first_day .. end_day].

    Returns a summary {days, written_points, skipped: [(day, reason)]}.
    """
    start = pv_first_day(query, cfg)
    if start is None:
        return {"days": 0, "written_points": 0, "skipped": [("all", "no pv_power data")]}

    written = 0
    processed = 0
    skipped: list[tuple[str, str]] = []
    d = start
    while d <= end_day:
        day = d.isoformat()
        d += timedelta(days=1)
        processed += 1
        try:
            lat, lon = day_location(query, cfg, day, home)
            status, body = fetch(archive_url(lat, lon, day, day, panel))
            series = parse_estimate(status, body, panel, now())
            hourly = [[p.ts.isoformat(), p.watts, p.watts] for p in series.points]
            lines = forecast_lines(cfg.forecast_measurement, ERA5_PROVIDER, hourly, 1.0)
            if lines:
                write(lines)
                written += len(lines)
            else:
                skipped.append((day, "no irradiance data (ERA5 lag?)"))
        except Exception as e:  # noqa: BLE001 - one bad day must not abort the run
            logger.exception("backfill failed for %s", day)
            skipped.append((day, str(e)))

    return {"days": processed, "written_points": written, "skipped": skipped}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load(os.environ)
    query = make_query_fn(cfg)
    write = make_write_fn(cfg)
    home = (
        float(os.environ.get("HOME_LAT", "48.77")),
        float(os.environ.get("HOME_LON", "9.16")),
    )
    from suncast.store import Store

    panel = Store(cfg.db_path).get_panel()
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    summary = backfill(cfg, query, write, default_fetch, panel, home, yesterday)
    print(
        f"backfill: {summary['days']} days, {summary['written_points']} points written, "
        f"{len(summary['skipped'])} days skipped"
    )
    for day, reason in summary["skipped"][:10]:
        print(f"  skip {day}: {reason}")


if __name__ == "__main__":
    main()
