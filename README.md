# suncast

[![CI](https://github.com/ckeller42/suncast/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/suncast/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ckeller42/suncast?label=release)](https://github.com/ckeller42/suncast/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**suncast** is a camper solar forecast service that displays hourly and daily power output from a rooftop PV array, combined with a web map showing current location. The forecast uses [Forecast.Solar](https://forecast.solar/) (public tier) and self-calibrates against your actual PV history.

Why self-calibration? Weather forecasts contain systematic error. Forecast.Solar's irradiance models are good, but they don't know your panel tilt, soiling, or temperature coefficient. Over time—especially over 30 days—your system's generation pattern reveals the local bias. suncast computes a rolling 30-day median ratio of forecast to actual, clamped 0.3–1.3 to ignore outlier days, then applies it to all future forecasts. You always see both the raw (gray) and corrected (blue) curves, so you know what the provider said and what your history says.

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
| `SUNCAST_CACHE_TTL_S` | `1800` | Forecast.Solar cache lifetime (seconds) |

## API

All requests/responses are JSON. POST `/api/forecast` returns both raw and calibrated hourly/daily series.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/forecast` | POST | Fetch calibrated forecast for lat/lon (body: `{lat, lon, days?: 1-6, panel?: {panel_wp, tilt_deg, azimuth_deg, charger_limit_w, damping}}`) |
| `/api/history` | GET | Last 30 days of forecast–actual pairs and metrics (query: `days?`) |
| `/api/config` | GET | Get stored panel configuration |
| `/api/config` | POST | Store panel configuration (body: panel object) |
| `/api/current-location` | GET | Latest GPS location from InfluxDB |

Health check:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System status: InfluxDB connection, last snapshot age, ratio count |

## Calibration

suncast computes a calibration factor daily by:

1. Querying the last `SUNCAST_WINDOW_DAYS` (default 30) of actual PV generation from Victron MPPT (InfluxDB).
2. Fetching the matching Forecast.Solar forecast for each day.
3. Computing the ratio of forecast to actual for each day.
4. Taking the **median** ratio, clamped to `[SUNCAST_CLAMP_LO, SUNCAST_CLAMP_HI]` (default 0.3–1.3).
5. If fewer than `SUNCAST_MIN_SAMPLES` (default 5) samples exist, the factor is **uncalibrated** (1.0).

The factor is applied to all future hourly points (multiplied by the raw forecast). You always see both curves: gray is raw, blue is calibrated.

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
