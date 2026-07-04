import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from suncast.calibrate import apply_factor, calibration
from suncast.influx import InfluxReader, WriteFn, forecast_lines
from suncast.models import DailyRatio
from suncast.providers.forecast_solar import ForecastSolar
from suncast.store import Store

logger = logging.getLogger(__name__)

# Bulk-only calibration guards: need at least this many unthrottled hours and
# this much forecast energy over them for an honest ratio sample.
MIN_BULK_HOURS = 2
MIN_BULK_FORECAST_WH = 50.0


@dataclass
class Deps:
    """Dependencies for daily_tick job."""

    provider: ForecastSolar
    store: Store
    influx: InfluxReader
    now: Callable[[], datetime]
    write: WriteFn | None = None  # optional: mirror forecasts to InfluxDB (display-only)
    forecast_measurement: str = "solar_forecast"


def daily_tick(d: Deps) -> dict[str, Any]:
    """Run daily job: snapshot once per day, then calculate ratio for yesterday.

    Returns:
        {
            "snapshotted": bool,      # True if snapshot was taken today
            "ratio_day": str|None,    # "YYYY-MM-DD" if ratio was saved
            "skipped": str|None       # Error message if any phase failed gracefully
        }
    """
    result = {
        "snapshotted": False,
        "ratio_day": None,
        "skipped": None,
    }

    now = d.now()
    today_str = now.date().isoformat()
    yesterday_str = (now.date() - timedelta(days=1)).isoformat()

    # Phase 1: Snapshot
    if not d.store.has_snapshot_today(today_str):
        try:
            # Get location
            loc = d.influx.latest_location()
            if loc is None:
                result["skipped"] = "no_location"
            else:
                lat, lon, _, _ = loc

                # Fetch forecast
                panel = d.store.get_panel()
                series = d.provider.forecast(lat, lon, panel, days=3)

                # Save snapshot
                d.store.save_snapshot(series, lat, lon, panel)
                result["snapshotted"] = True

                # Optional display-only mirror to InfluxDB (SQLite stays the
                # source of truth for calibration).
                if d.write is not None:
                    try:
                        cal = calibration([r.ratio for r in d.store.ratios()])
                        out = apply_factor(series, cal)
                        d.write(
                            forecast_lines(
                                d.forecast_measurement, series.provider, out["hourly"], cal.factor
                            )
                        )
                    except Exception:
                        logger.exception("forecast influx write failed")
        except Exception as e:
            logger.exception("daily_tick phase failed")
            result["skipped"] = str(e)

    # Phase 2: Ratio for yesterday — BULK HOURS ONLY.
    # pv_power measures what the battery accepted; with a full battery the MPPT
    # throttles (absorption/float) and actuals say nothing about panel potential.
    # Compare only unthrottled (bulk) hours against the archived forecast for
    # the SAME hours.
    try:
        if not d.store.has_ratio(yesterday_str):
            hourly_fc = d.store.snapshot_hourly_for_day(yesterday_str)
            bulk = d.influx.actual_bulk_hourly(yesterday_str)

            if hourly_fc is None:
                pass  # no archived snapshot for yesterday
            elif len(bulk) < MIN_BULK_HOURS:
                if result["skipped"] is None:
                    result["skipped"] = f"only {len(bulk)} bulk hour(s) — battery full, no signal"
            else:
                forecast_wh = sum(hourly_fc.get(h, 0.0) for h in bulk)
                actual_wh = sum(bulk.values())
                if forecast_wh >= MIN_BULK_FORECAST_WH:
                    daily_ratio = DailyRatio(
                        day=yesterday_str,
                        forecast_wh=forecast_wh,
                        actual_wh=actual_wh,
                        ratio=actual_wh / forecast_wh,
                    )
                    snapshot_id = d.store.snapshot_id_for_day(yesterday_str) or 0
                    d.store.save_ratio(daily_ratio, snapshot_id)
                    result["ratio_day"] = yesterday_str
                elif result["skipped"] is None:
                    result["skipped"] = "bulk-hour forecast below threshold — no signal"
    except Exception as e:
        logger.exception("daily_tick phase failed")
        if result["skipped"] is None:
            result["skipped"] = str(e)

    return result
