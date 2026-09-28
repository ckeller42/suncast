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

The offline `suncast-backfill` / `suncast-backtest` scripts read the same
variables plus `HOME_LAT`, `HOME_LON` and `BACKTEST_OUT_DIR`; see
[Offline tools](#offline-tools-run-on-the-pi).

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

Dependencies are locked in `uv.lock`; run everything through `uv`:

```bash
uv run --extra dev pytest -q                    # tests
uv run --extra dev ruff check .                 # lint
uv run --extra dev ruff format --check .        # formatter (check only)
```

Git hooks (pre-commit) run whitespace/YAML checks, gitleaks, markdownlint and
ruff on every commit, and the test suite on push. Install them once:

```bash
uv run --extra dev pre-commit install
uv run --extra dev pre-commit run --all-files   # same checks CI runs
```

`.pre-commit-config.yaml` pins the tool versions; markdownlint rules live in
`.markdownlint-cli2.jsonc`. CI runs these hooks plus the tests (coverage gate)
on Python 3.11, 3.12 and 3.13. Agent/contributor rules: [AGENTS.md](AGENTS.md).

### Offline tools (run on the Pi)

Two console scripts ship alongside the service. Both load the same service
config (`INFLUX_URL`, `INFLUX_ORG`, `INFLUXDB_TOKEN`, bucket/measurement names,
`SUNCAST_DB`), so run them with the service env sourced, e.g.:

```bash
set -a; . /etc/buspi/secrets.env; . /etc/buspi/suncast.env; set +a
/home/pi/suncast-env/bin/suncast-backfill
```

Both walk every day from the first `pv_power` sample in the last 120 days up to
yesterday (UTC). For each day the van's location is the mean of that day's
`geo` track; days with no location history fall back to `HOME_LAT`/`HOME_LON`.
One failing day is logged and skipped, never aborting the run.

| Variable | Default | Used by | Description |
|----------|---------|---------|-------------|
| `HOME_LAT` | `48.77` | backfill, backtest | Fallback latitude for days with no `geo` fix |
| `HOME_LON` | `9.16` | backfill, backtest | Fallback longitude for days with no `geo` fix |
| `BACKTEST_OUT_DIR` | `docs/superpowers/results` | backtest | Directory for the results file (relative paths resolve against the current working directory) |

#### Backfill (historical expected PV)

`suncast-backfill` fetches Open-Meteo's ERA5 reanalysis (the actual past
irradiance) for each day, converts it to expected panel watts with the stored
panel config, and writes it to InfluxDB in the `FORECAST_MEASUREMENT`
measurement (default `solar_forecast`: `raw_w`, `corrected_w`, `factor=1.0`)
tagged `provider=open-meteo-era5`. This lets the Grafana forecast-vs-absorbed
panel span the full PV history, not just from deployment forward. It is an
expected-potential line, not a forecast, and never feeds the live calibration.
Re-running rewrites the same points. Note it reads the panel through the
service's store, which creates `SUNCAST_DB` and a default panel row if absent.

#### Backtest (offline model evaluation)

`suncast-backtest` scores candidate potential-prediction models (flat factor vs
temperature-derate) against Victron history using ERA5 reanalysis, all metrics
leave-one-day-out. It reads the stored panel config read-only (defaults if none)
and prints a results table, then writes it to
`{BACKTEST_OUT_DIR}/{run_date}-backtest.md` (run date in UTC), headed
`# Backtest results ({run_date}, data through {end_day})`. Because the filename
carries the run date, a run on a later day never overwrites an earlier verdict;
a second run on the same day does overwrite that day's file.

The default `BACKTEST_OUT_DIR` is relative to the current working directory, so
either run from the repo root or set it explicitly on the Pi, e.g.
`BACKTEST_OUT_DIR=/home/pi/suncast-results suncast-backtest`.
