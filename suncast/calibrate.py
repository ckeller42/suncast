from collections import defaultdict
from datetime import timedelta
from statistics import median, quantiles

from suncast.models import Calibration, ForecastPoint, ForecastSeries


def calibration(
    ratios: list[float],
    window: int = 30,
    min_samples: int = 5,
    lo: float = 0.3,
    hi: float = 1.3,
) -> Calibration:
    """Compute calibration factor and band from newest-first ratios."""
    ratios_window = ratios[:window]

    if len(ratios_window) < min_samples:
        return Calibration(1.0, 1.0, 1.0, len(ratios_window), False)

    # Compute median
    factor = median(ratios_window)

    # Compute P25 and P75 via quantiles
    if len(ratios_window) < 2:
        p25 = p75 = factor
    else:
        q = quantiles(ratios_window, n=4)  # returns [Q1, Q2, Q3]
        p25 = q[0]
        p75 = q[2]

    # Clamp factor and band to [lo, hi]
    factor = max(lo, min(hi, factor))
    p25 = max(lo, min(hi, p25))
    p75 = max(lo, min(hi, p75))

    return Calibration(factor, p25, p75, len(ratios_window), True)


def apply_factor(series: ForecastSeries, cal: Calibration) -> dict:
    """Apply calibration factor to forecast series."""
    hourly = []
    daily_corrected = defaultdict(
        lambda: {
            "raw_wh": 0.0,
            "corrected_wh": 0.0,
            "lower_wh": 0.0,
            "upper_wh": 0.0,
        }
    )

    # Process hourly points
    for point in series.points:
        iso = point.ts.isoformat()
        day = point.ts.strftime("%Y-%m-%d")
        raw_w = point.watts
        corrected_w = raw_w * cal.factor
        hourly.append([iso, raw_w, corrected_w])

        # Accumulate daily
        daily_corrected[day]["raw_wh"] += raw_w
        daily_corrected[day]["corrected_wh"] += corrected_w
        daily_corrected[day]["lower_wh"] += raw_w * cal.p25
        daily_corrected[day]["upper_wh"] += raw_w * cal.p75

    # Use the aggregated daily_wh from series if available
    daily = {}
    for day, raw_wh in series.daily_wh.items():
        daily[day] = {
            "raw_wh": raw_wh,
            "corrected_wh": raw_wh * cal.factor,
            "lower_wh": raw_wh * cal.p25,
            "upper_wh": raw_wh * cal.p75,
        }

    return {"hourly": hourly, "daily": daily}


def best_window(points: list[ForecastPoint], hours: int = 4) -> dict[str, dict]:
    """Find best rolling window per UTC day maximizing wh sum."""
    # Group by UTC day
    by_day = defaultdict(list)
    for point in points:
        day = point.ts.strftime("%Y-%m-%d")
        by_day[day].append(point)

    result = {}
    for day in sorted(by_day.keys()):
        day_points = sorted(by_day[day], key=lambda p: p.ts)
        max_wh = 0.0
        best_start_idx = 0

        # Try all consecutive windows of `hours` length
        for start_idx in range(len(day_points)):
            # Find points within hours from start
            start_ts = day_points[start_idx].ts
            end_ts = start_ts + timedelta(hours=hours)

            window_sum = 0.0
            point_count = 0
            for point in day_points[start_idx:]:
                if point.ts < end_ts:
                    window_sum += point.watts
                    point_count += 1
                else:
                    break

            # Only count if we have a full window
            if point_count >= hours or (start_idx + hours <= len(day_points)):
                if window_sum > max_wh:
                    max_wh = window_sum
                    best_start_idx = start_idx

        if day_points:
            start_point = day_points[best_start_idx]
            end_ts = start_point.ts + timedelta(hours=hours)
            result[day] = {
                "start": start_point.ts.isoformat(),
                "end": end_ts.isoformat(),
                "wh": max_wh,
            }

    return result


def metrics(pairs: list[tuple[float, float]]) -> dict:
    """Compute error metrics from (forecast_wh, actual_wh) pairs."""
    if not pairs:
        return {"mae": 0.0, "rmse": 0.0, "mape_pct": 0.0, "bias_wh": 0.0, "n": 0}

    mae_sum = 0.0
    rmse_sum = 0.0
    mape_sum = 0.0
    mape_count = 0
    bias_sum = 0.0

    for forecast, actual in pairs:
        error = abs(forecast - actual)
        mae_sum += error
        rmse_sum += error ** 2
        bias_sum += forecast - actual

        if actual >= 50:
            mape_sum += error / actual
            mape_count += 1

    n = len(pairs)
    mae = mae_sum / n if n > 0 else 0.0
    rmse = (rmse_sum / n) ** 0.5 if n > 0 else 0.0
    mape_pct = (mape_sum / mape_count) * 100 if mape_count > 0 else 0.0
    bias_wh = bias_sum / n if n > 0 else 0.0

    return {"mae": mae, "rmse": rmse, "mape_pct": mape_pct, "bias_wh": bias_wh, "n": n}
