# suncast

[![CI](https://github.com/ckeller42/suncast/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/suncast/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-site-blue)](https://ckeller42.github.io/suncast/)
[![Release](https://img.shields.io/github/v/release/ckeller42/suncast?label=release)](https://github.com/ckeller42/suncast/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**suncast** is a camper solar forecast service that displays hourly and daily power output from a rooftop PV array, combined with a web map (with address search) showing current location. The forecast uses [Open-Meteo](https://open-meteo.com/) (best regional model for Europe — DWD ICON-D2, 16-day horizon, no key) and self-calibrates against your actual PV history. [Forecast.Solar](https://forecast.solar/) is kept as a secondary provider shown for comparison.

Why self-calibration? Weather forecasts contain systematic error, and an irradiance model doesn't know your panel tilt, soiling, or temperature coefficient (Open-Meteo returns raw irradiance, so it reads structurally high). Over time—especially over 30 days—your system's generation pattern reveals the local bias. suncast computes a rolling 30-day median ratio of forecast to actual — using only **unthrottled (bulk) charge hours**, because once the battery is full the MPPT throttles and absorbed power no longer reflects panel potential — clamped 0.3–1.3 to ignore outlier days, then applies it to all future forecasts. You always see both the raw (gray) and corrected (blue) curves, so you know what the provider said and what your history says.

**Documentation:** <https://ckeller42.github.io/suncast/> covers getting started, deployment, the offline tools, the
configuration / API / calibration reference and the architecture. Build it locally with
`uv run --extra docs sphinx-build -b html -W docs docs/_build/html`.

<!-- screenshot: map + forecast page (add docs/screenshot.png when captured) -->

## Install on Raspberry Pi

Install into a virtual environment (`pip install .` from the repo root), copy
`deploy/suncast.service` and `deploy/suncast.env.example` (to `/etc/buspi/suncast.env`), then
`sudo systemctl enable --now suncast`. The required `INFLUXDB_TOKEN` comes from
`/etc/buspi/secrets.env`. Full steps, checks and upgrade:
[deploy guide](https://ckeller42.github.io/suncast/howto-deploy.html).

## Configuration

All configuration is via environment variables. Required variables are marked with `*`.

<!-- config-service:start -->
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
| `SUNCAST_TZ` | `Europe/Berlin` | Timezone name (parsed into the config, not used by any code path yet) |
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
<!-- config-service:end -->

The offline `suncast-backfill` / `suncast-backtest` scripts read the same
variables plus these:

<!-- config-offline:start -->
| Variable | Default | Used by | Description |
|----------|---------|---------|-------------|
| `HOME_LAT` | `48.77` | backfill, backtest | Fallback latitude for days with no `geo` fix |
| `HOME_LON` | `9.16` | backfill, backtest | Fallback longitude for days with no `geo` fix |
| `BACKTEST_OUT_DIR` | `docs/superpowers/results` | backtest | Directory for the results file (relative paths resolve against the current working directory) |
<!-- config-offline:end -->

See the [offline tools guide](https://ckeller42.github.io/suncast/howto-offline-tools.html).

## API

All requests and responses are JSON. The service exposes `POST /api/forecast`, `GET /api/history`,
`GET`/`POST /api/config`, `GET /api/current-location`, `GET /api/geocode` and `GET /api/health`.
Request fields, validation and error codes are in the
[API reference](https://ckeller42.github.io/suncast/reference/api.html).

## Calibration

suncast corrects the primary forecast with the rolling median of daily actual/forecast ratios,
computed over bulk-charge hours only and clamped to 0.3 to 1.3. The raw (gray) and the corrected
(blue) curve are always both shown. Details, thresholds and the metric definitions are in the
[calibration reference](https://ckeller42.github.io/suncast/reference/calibration.html).

## Development

Dependencies are locked in `uv.lock`; run everything through `uv`:

```bash
uv run --extra dev pytest -q                    # tests
uv run --extra dev ruff check .                 # lint
uv run --extra dev ruff format --check .        # formatter (check only)
uv run --extra docs sphinx-build -b html -W docs docs/_build/html   # docs site, warnings are errors
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
