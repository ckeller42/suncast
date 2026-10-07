# Calibration and metrics

Code: [`suncast/calibrate.py`](https://github.com/ckeller42/suncast/blob/main/suncast/calibrate.py)
(factor, metrics), [`suncast/jobs.py`](https://github.com/ckeller42/suncast/blob/main/suncast/jobs.py)
(daily sample), [`suncast/influx.py`](https://github.com/ckeller42/suncast/blob/main/suncast/influx.py)
(bulk hours, drift). Configuration names are in [](configuration.md).

## Why

Open-Meteo returns raw tilted irradiance, converted to watts as `panel_wp * GTI / 1000` and
capped at `charger_limit_w`. That series reads structurally high, and weather forecasts carry
systematic error. The factor learned from the charger's own history divides this back out.

## Bulk hours only

`pv_power` is what the battery accepted. Once the battery is full the MPPT throttles
(absorption or float) and the value no longer reflects panel potential. An hour counts as bulk
when the mean of the `charge_state` field over that hour is at least 2.5 and below 3.5 (the
Victron bulk state is 3). Only bulk hours of a day take part in its ratio. Hours are UTC hour
starts, the same keys as the forecast's hourly points.

## Daily sample

The daily job computes the sample for yesterday (UTC):

1. Take the hourly forecast of the earliest snapshot stored on that day, and the bulk hours with
   their actual Wh from InfluxDB.
2. Skip the day if there is no snapshot.
3. Skip the day if the van moved more than `DRIFT_KM_MAX` (default 20 km) from the snapshot
   location. The distance is the maximum haversine distance of the day's hourly `lat`/`lon`
   track from the snapshot position. Without a track the guard does not apply.
4. Skip the day if fewer than 2 bulk hours exist.
5. Compute `forecast_wh` (sum of the forecast over the bulk hours) and `actual_wh` (sum over the
   bulk hours). Skip the day if `forecast_wh` is below 50 Wh.
6. Otherwise store `ratio = actual_wh / forecast_wh`.

The thresholds 2 hours and 50 Wh are constants in `jobs.py` (`MIN_BULK_HOURS`,
`MIN_BULK_FORECAST_WH`). A skipped day stores nothing and the job does not retry it.

## Factor

`calibration()` works on the stored ratios, newest first:

1. Take the newest `SUNCAST_WINDOW_DAYS` ratios (default 30).
2. With fewer than `SUNCAST_MIN_SAMPLES` (default 5), the result is uncalibrated: factor, P25
   and P75 are all `1.0` and `calibrated` is `false`.
3. Otherwise the factor is the median, P25 and P75 the first and third quartile of the
   window.
4. Factor, P25 and P75 are each clamped to `[SUNCAST_CLAMP_LO, SUNCAST_CLAMP_HI]` (default 0.3
   to 1.3).

At most 90 ratios are read from the database, so a window longer than 90 days is capped.

## Applying the factor

`apply_factor()` multiplies each hourly raw watt value by the factor and each daily raw Wh by
the factor (`corrected_wh`), P25 (`lower_wh`) and P75 (`upper_wh`). Only the primary provider's
series is corrected. The secondary provider's series is shown raw.

The calibration uses the stored snapshots, not refetched data. If the forecast or the actual
generation changes after the fact, the stored ratio stays as recorded.

## Best window

`best_window()` finds, per UTC day, the consecutive 4 hours with the largest sum of raw watts.
On a partial day the block shrinks to the available hours. The end is the start of the last hour
plus one hour.

(metrics)=

## Metrics

`metrics()` takes `(forecast_wh, actual_wh)` pairs, here the stored bulk-hour sums per day, and
returns:

| Metric | Definition |
|---|---|
| `mae` | mean of the absolute errors `abs(forecast - actual)`, in Wh |
| `rmse` | square root of the mean squared error, in Wh |
| `mape_pct` | mean of `abs(forecast - actual) / actual` times 100, over pairs with `actual >= 50` Wh only |
| `bias_wh` | mean of `forecast - actual`, in Wh, positive means the forecast is too high |
| `n` | number of pairs |

An empty input gives zeros. The metrics describe the raw forecast, before the factor.
