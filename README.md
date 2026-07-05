# suncast

[![CI](https://github.com/ckeller42/suncast/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/suncast/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ckeller42/suncast?label=release)](https://github.com/ckeller42/suncast/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**suncast** is a camper solar forecast service that displays hourly and daily power output from a rooftop PV array, combined with a web map (with address search) showing current location. The forecast uses [Open-Meteo](https://open-meteo.com/) (best regional model for Europe — DWD ICON-D2, 16-day horizon, no key) and self-calibrates against your actual PV history. [Forecast.Solar](https://forecast.solar/) is kept as a secondary provider shown for comparison.

Why self-calibration? Weather forecasts contain systematic error, and an irradiance model doesn't know your panel tilt, soiling, or temperature coefficient (Open-Meteo returns raw irradiance, so it reads structurally high). Over time—especially over 30 days—your system's generation pattern reveals the local bias. suncast computes a rolling 30-day median ratio of forecast to actual — using only **unthrottled (bulk) charge hours**, because once the battery is full the MPPT throttles and absorbed power no longer reflects panel potential — clamped 0.3–1.3 to ignore outlier days, then applies it to all future forecasts. You always see both the raw (gray) and corrected (blue) curves, so you know what the provider said and what your history says.

<!-- screenshot: map + forecast page (add docs/screenshot.png when captured) -->

## Install on Raspberry Pi

Create a venv and install:

```bash
python3 -m venv /home/pi/suncast-env
/home/pi/suncast-env/bin/pip install .   # run from the suncast repo root
```

Prepare the database directory:

```bash
sudo install -d -o pi -m 755 /var/lib/suncast
```

Copy the systemd unit and environment file:

```bash
sudo cp deploy/suncast.service /etc/systemd/system/
sudo cp deploy/suncast.env.example /etc/buspi/suncast.env
sudo chown root:root /etc/systemd/system/suncast.service /etc/buspi/suncast.env
sudo chmod 644 /etc/systemd/system/suncast.service /etc/buspi/suncast.env
```

Edit `/etc/buspi/suncast.env` to set your InfluxDB bucket names and parameters. The required `INFLUXDB_TOKEN` comes from `/etc/buspi/secrets.env` (sourced by systemd).

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now suncast
```

Check logs:

```bash
journalctl -u suncast -f
```

## Configuration

All configuration is via environment variables. Required variables are marked with `*`.

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUX_URL` * | — | InfluxDB HTTP endpoint, e.g., `http://localhost:8086` |
| `INFLUX_ORG` * | — | InfluxDB organization name |
| `INFLUXDB_TOKEN` * | — | InfluxDB API token (from `secrets.env`) |
| `VICTRON_BUCKET` | `victron` | InfluxDB bucket containing Victron MPPT data |
| `VICTRON_MEASUREMENT` | `victron` | Measurement name for Victron data |
| `CHARGE_STATE_FIELD` | `charge_state` | Victron charge-state field (bulk detection for calibration) |
| `PV_POWER_FIELD` | `pv_power` | Field name for PV power (watts) |
| `GEO_BUCKET` | `buspi` | InfluxDB bucket containing GPS location data |
| `GEO_MEASUREMENT` | `geo` | Measurement name for location data |
| `SUNCAST_PORT` | `8090` | Port for the web server |
| `SUNCAST_TZ` | `Europe/Berlin` | Timezone for sunrise/sunset calculations |
| `SUNCAST_DB` | `/var/lib/suncast/suncast.db` | Path to SQLite calibration database |
| `SUNCAST_WINDOW_DAYS` | `30` | Calibration window (days) |
| `SUNCAST_MIN_SAMPLES` | `5` | Minimum samples required before calibration is applied |
| `SUNCAST_CLAMP_LO` | `0.3` | Minimum calibration factor |
| `SUNCAST_CLAMP_HI` | `1.3` | Maximum calibration factor |
| `SUNCAST_CACHE_TTL_S` | `1800` | Provider forecast cache lifetime (seconds) |
| `PROVIDER` | `open_meteo` | Primary provider (`open_meteo` \| `forecast_solar`) |
| `PROVIDER_SECONDARY` | `forecast_solar` | Secondary provider shown for comparison (empty to disable) |
| `FORECAST_MEASUREMENT` | `solar_forecast` | InfluxDB measurement for the mirrored forecast (Grafana) |
| `DRIFT_KM_MAX` | `20` | Skip a day's calibration if the van roamed more than this many km |

## API

All requests/responses are JSON. POST `/api/forecast` returns both raw and calibrated hourly/daily series.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/forecast` | POST | Fetch calibrated forecast for lat/lon (body: `{lat, lon, days?: 1-16, panel?: {panel_wp, tilt_deg, azimuth_deg, charger_limit_w, damping}}`); response includes a `comparison` block from the secondary provider |
| `/api/history` | GET | Last 30 days of forecast–actual pairs and metrics (query: `days?`) |
| `/api/config` | GET | Get stored panel configuration |
| `/api/config` | POST | Store panel configuration (body: panel object) |
| `/api/current-location` | GET | Freshest location fix from InfluxDB (across geo tag-series) |
| `/api/geocode` | GET | Address search via OSM Nominatim (query: `q`) |

Health check:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System status: InfluxDB connection, last snapshot age, ratio count |

## Calibration

suncast computes a calibration factor daily by:

1. Comparing each day's actual PV against the archived forecast **only over unthrottled (bulk) charge hours** — once the battery is full the MPPT throttles and absorbed power no longer reflects panel potential, so those hours are excluded.
2. Skipping days with fewer than 2 bulk hours, under 50 Wh of bulk-hour forecast, or where the van roamed more than `DRIFT_KM_MAX` from the snapshot location (a travel day compares against the wrong sky).
3. Taking the **median** ratio over `SUNCAST_WINDOW_DAYS` (default 30), clamped to `[SUNCAST_CLAMP_LO, SUNCAST_CLAMP_HI]` (default 0.3–1.3); P25/P75 give the confidence band.
4. If fewer than `SUNCAST_MIN_SAMPLES` (default 5) samples exist, the factor is **uncalibrated** (1.0).

The factor is applied to all future hourly points of the primary provider. You always see both curves: gray is raw, blue is calibrated (plus a dashed amber secondary-provider curve for comparison).

**Important:** calibration uses historical snapshots in the database—not refetched data. If your actual generation changed, or the forecast changes post-hoc, the historical ratio stays as recorded. This is honest: it shows what the system knew at the time.

## Development

Install with dev extras:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Lint:

```bash
ruff check
```

Formatter (check only):

```bash
ruff format --check
```

### Backtest (offline model evaluation)

`suncast-backtest` scores candidate potential-prediction models (flat factor vs
temperature-derate) against Victron history using ERA5 reanalysis, and writes a
results table to `docs/superpowers/results/`. Run it on the Pi with the service
env sourced (`HOME_LAT`/`HOME_LON` set the pre-location-history fallback).
