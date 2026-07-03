from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from suncast.influx import InfluxReader
from suncast.models import DailyRatio
from suncast.providers.forecast_solar import ForecastSolar
from suncast.store import Store


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

    # Get today's date in UTC
    today_str = d.now().date().isoformat()

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
            result["skipped"] = str(e)

    # Phase 2: Ratio for yesterday
    yesterday_str = (d.now().date() - timedelta(days=1)).isoformat()

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

                # Find the snapshot ID for yesterday (earliest snapshot)
                # We need to query for it since save_snapshot returns the ID
                # Actually, looking at the test, it seems we just need to save it
                # Let's check what snapshot_id we should use...
                # The store.save_ratio needs a snapshot_id
                # We need to find the ID of the earliest snapshot for yesterday

                # Query the database to get the snapshot ID
                cursor = d.store.conn.execute(
                    "SELECT id FROM snapshots WHERE day = ? ORDER BY created_at ASC LIMIT 1",
                    (yesterday_str,),
                )
                row = cursor.fetchone()
                if row is not None:
                    snapshot_id = row[0]
                    d.store.save_ratio(daily_ratio, snapshot_id)
                    result["ratio_day"] = yesterday_str
    except Exception as e:
        if result["skipped"] is None:
            result["skipped"] = str(e)

    return result
