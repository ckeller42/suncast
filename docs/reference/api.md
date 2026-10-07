# HTTP API

Source: [`suncast/app.py`](https://github.com/ckeller42/suncast/blob/main/suncast/app.py). All
API requests and responses are JSON. There is no authentication, the service is meant for the
local network (it binds `0.0.0.0` on `SUNCAST_PORT`, default `8090`). Two HTML pages are served
as well: `/` (map and forecast) and `/history` (calibration history), plus static files under
`/static`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/forecast` | POST | Calibrated forecast for a location |
| `/api/history` | GET | Stored forecast-vs-actual days, error metrics, current factor |
| `/api/config` | GET | Stored panel configuration |
| `/api/config` | POST | Store the panel configuration |
| `/api/current-location` | GET | Freshest location fix from InfluxDB |
| `/api/geocode` | GET | Address search through Nominatim |
| `/api/health` | GET | Status of InfluxDB, snapshots and ratios |

Errors use FastAPI's shape `{"detail": "..."}` with the status code listed per endpoint.

## POST /api/forecast

Request body:

| Field | Type | Default | Notes |
|---|---|---|---|
| `lat`, `lon` | number | required | Booleans are rejected |
| `days` | integer | `3` | 1 to 16 |
| `panel` | object | stored panel | `panel_wp`, `tilt_deg`, `azimuth_deg`, `charger_limit_w`, `damping`. An empty or missing object means the stored panel is used |

Panel defaults: `panel_wp` 260, `tilt_deg` 0, `azimuth_deg` 0 (0 is south), `charger_limit_w`
200, `damping` 0.

Response fields:

| Field | Content |
|---|---|
| `location` | `{lat, lon}` as sent |
| `provider` | `open-meteo` or `forecast.solar`, the primary provider's series name |
| `factor` | `{factor, p25, p75, samples, calibrated}`, see [](calibration.md) |
| `hourly` | `[[iso_ts_utc, raw_w, corrected_w], ...]` |
| `daily` | per UTC day `{raw_wh, corrected_wh, lower_wh, upper_wh}` (lower and upper use P25 and P75) |
| `best_windows` | per UTC day the best consecutive 4 hour block `{start, end, wh}` of the raw series |
| `comparison` | `null`, or `{provider, hourly: [[iso_ts, watts], ...], daily}` from the secondary provider, raw and uncalibrated |

The secondary provider is best effort: if it fails the error is logged and `comparison` is
`null`, the primary forecast still returns.

| Status | Cause |
|---|---|
| 422 | `lat` or `lon` missing or not numbers, `days` not an integer in 1 to 16, invalid `panel` object |
| 429 | The primary provider is rate limited |
| 502 | The primary provider failed |

## GET /api/history

Query: `days` (default `30`). Returns the newest `days` rows of stored calibration days in
chronological order:

- `days`: list of `{day, forecast_wh, actual_wh, ratio}`
- `metrics_raw`: error metrics over those `(forecast_wh, actual_wh)` pairs, see
  [](calibration.md#metrics)
- `factor`: the current calibration, same shape as in `/api/forecast`

## GET and POST /api/config

`GET` returns the stored panel (the defaults are created on first read). `POST` takes a panel
object with the same fields and stores it, replacing the previous one, and echoes it back.
`panel_wp` and `charger_limit_w` must be integers, `tilt_deg`, `azimuth_deg` and `damping`
numbers, none of them booleans. Unknown fields or wrong types give 422.

## GET /api/current-location

Returns `{lat, lon, range_m, age_s}` for the newest `geo` fix of the last 30 days, taken across
all tag series (a stale cell fix does not shadow a fresh WiFi fix). `range_m` is `0.0` when
absent. 404 when no latitude or longitude exists.

## GET /api/geocode

Query: `q` (required, trimmed). Returns `{"results": [{label, lat, lon}, ...]}` with at most five
entries from OSM Nominatim. 422 for an empty `q`, 502 when Nominatim fails.

## GET /api/health

Returns `{influx_ok, last_snapshot_age_s, ratios}`. `influx_ok` is true when a location fix could
be read from InfluxDB. `last_snapshot_age_s` is the age of the newest snapshot in seconds or
`null`. `ratios` is the number of stored calibration days, `null` if the database cannot be
read. The endpoint itself always answers 200.
