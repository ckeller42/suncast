import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from suncast.influx import InfluxReader
from suncast.models import DailyRatio
from suncast.providers.forecast_solar import ForecastSolar
from suncast.store import Store

logger = logging.getLogger(__name__)


@dataclass
class Deps:
    """Dependencies for daily_tick job."""

    provider: ForecastSolar
    store: Store
    influx: InfluxReader
    now: Callable[[], datetime]


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
        except Exception as e:
            logger.exception("daily_tick phase failed")
            result["skipped"] = str(e)

    # Phase 2: Ratio for yesterday
    try:
        if not d.store.has_ratio(yesterday_str):
            forecast_wh = d.store.snapshot_forecast_wh(yesterday_str)
            actual_wh = d.influx.actual_day_wh(yesterday_str)

            if forecast_wh is not None and actual_wh is not None and forecast_wh > 0:
                ratio = actual_wh / forecast_wh
                daily_ratio = DailyRatio(
                    day=yesterday_str,
                    forecast_wh=forecast_wh,
                    actual_wh=actual_wh,
                    ratio=ratio,
                )

                snapshot_id = d.store.snapshot_id_for_day(yesterday_str) or 0
                d.store.save_ratio(daily_ratio, snapshot_id)
                result["ratio_day"] = yesterday_str
    except Exception as e:
        logger.exception("daily_tick phase failed")
        if result["skipped"] is None:
            result["skipped"] = str(e)

    return result
