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
    for point in series.points:
        hourly.append([point.ts.isoformat(), point.watts, point.watts * cal.factor])

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
        # Slice length shrinks to the available points on partial days so a
        # short first/last forecast day still reports its real best block.
        size = min(hours, len(day_points))
        best = None
        for start_idx in range(len(day_points) - size + 1):
            chunk = day_points[start_idx : start_idx + size]
            wh = sum(p.watts for p in chunk)
            if best is None or wh > best[0]:
                best = (wh, chunk)
        wh, chunk = best
        result[day] = {
            "start": chunk[0].ts.isoformat(),
            "end": (chunk[-1].ts + timedelta(hours=1)).isoformat(),
            "wh": wh,
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
        rmse_sum += error**2
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
