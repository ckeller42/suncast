# Design: suncast — camper solar forecast with self-calibration

**Status:** approved (2026-07-02) · **Repo:** github.com/ckeller42/suncast (MIT) ·
**Runs on:** buspi Raspberry Pi, LAN-only

## Problem

Before driving to a destination, the user wants to know how much solar the bus
(VW T7 California Ocean) will harvest there over the next days. Raw provider
forecasts are generic; the bus has a known panel, a hard charger cap, and months
of real production data in InfluxDB — so the forecast should be corrected by the
observed forecast-vs-actual ratio of *this* system.

KISS explicitly requested: this is the MVP cut of a larger pasted spec. Cut:
admin/diagnostics page, retrain endpoint, geohash bucketing, phase-2 ML, Docker,
Influx-mapping UI, SPA, CSV export, heatmaps, route preview.

## Hardware facts (defaults)

- Panel: **Califaktur 260 Wp** (2× 130 Wp semi-flexible, C-rail mount on the
  pop-up roof) → **tilt 0°, azimuth irrelevant (0°)**.
- Charge controller: **Victron MPPT 75/15** → 15 A ≈ **200 W effective cap**
  (used as the provider's inverter-limit parameter; dominates the 260 Wp).
- Actual production: InfluxDB bucket `victron`, measurement fields `pv_power`
  (W, ~10 s cadence) and `yield_today_kwh` (daily counter, cross-check).
- Location history: InfluxDB bucket `buspi`, measurement `geo` (`lat`, `lon`,
  `range_m`; written by celloc/geoinflux).

## User story

Open `http://buspi:8090` → map shows current bus position (from `geo`) → click a
destination → see next-*n*-days forecast: **raw** (Forecast.Solar) vs
**corrected** (× learned factor), P25–P75 band, daily Wh totals, and the best
4-hour charging window per day. Panel params adjustable in a small form.
Second page shows history: actual vs forecast, error metrics, current factor.

## Architecture

Single Python service (FastAPI, server-rendered Jinja2 + vanilla JS). No build
step. Leaflet vendored, OSM tiles (internet is required for the forecast API
anyway). Charts are hand-rolled inline SVG.

```text
Leaflet click ─▶ POST /api/forecast ─▶ provider (Forecast.Solar) ─▶ raw series
                                            │                        │
celloc geo (Influx) ─▶ current location     │        calibrate: × rolling median ratio
victron pv_power (Influx) ─▶ actual Wh ─────┴──▶ SQLite snapshots ──▶ corrected + band
```

### Modules

| Module | Kind | Responsibility |
|---|---|---|
| `suncast/config.py` | pure + env | env → Config (Influx conn + name mappings, port, defaults) |
| `suncast/models.py` | pure | PanelConfig, ForecastSeries/Point, DailyRatio, CorrectedSeries |
| `suncast/providers/base.py` | pure | `Provider` protocol: `forecast(lat, lon, panel, days) -> ForecastSeries` |
| `suncast/providers/forecast_solar.py` | pure parse + IO client | Forecast.Solar `/estimate/{lat}/{lon}/{tilt}/{az}/{kwp}`; hourly watts + daily Wh; rate-limit/error classification |
| `suncast/influx.py` | IO | read actual hourly/daily Wh from `victron.pv_power`; read latest + historical `geo` location |
| `suncast/store.py` | IO (SQLite) | forecast snapshots, daily ratios, panel config; schema + migrations |
| `suncast/calibrate.py` | pure | rolling-median factor (window 30 d, min 5 samples, clamp 0.3–1.3), P25/P75 band, best-window finder, MAE/MAPE/bias |
| `suncast/jobs.py` | IO | daily async task: snapshot forecast @ current location; compute yesterday's ratio |
| `suncast/app.py` | wiring | FastAPI routes (pages + API), startup task, template rendering |

### Honest backtesting rule

Ratios are computed **only** from archived snapshots (what the provider said at
the time) vs actuals — never from a forecast re-fetched later for a past day.

### Calibration (phase 1 only, transparent)

- Daily job stores: snapshot (raw hourly+daily forecast JSON, location, panel
  params, provider metadata) and yesterday's `ratio = actual_wh / forecast_wh`.
- Factor = rolling **median** ratio over the last 30 days with ≥ 5 samples,
  clamped to [0.3, 1.3] (all configurable). Below 5 samples → factor 1.0,
  band widened, UI labels it "uncalibrated".
- Band: corrected × (P25 ratio, P75 ratio).
- Global factor (no location buckets) — the van is at one place for days at a
  time; per-location bucketing is a v2 item, listed under Later.
- Actual Wh: hourly mean of `pv_power` × 1 h, summed per day; discard hours with
  < 50 % samples; sanity cross-check against `yield_today_kwh` (warn in log if
  > 15 % apart). Timestamps UTC in storage, Europe/Berlin in UI.

## API

| Endpoint | In | Out |
|---|---|---|
| `POST /api/forecast` | `{lat, lon, days=3, panel?}` | location, raw series, corrected series, daily totals, band, factor + sample size, best windows |
| `GET /api/history?days=30` | — | per-day forecast vs actual, ratios, MAE/MAPE/bias, current factor |
| `GET/POST /api/config` | panel params | persisted PanelConfig (SQLite) |
| `GET /api/current-location` | — | latest `geo` point (lat, lon, range_m, age) |
| `GET /api/health` | — | provider reachable, Influx reachable, last snapshot age |

Pages: `/` (map + forecast), `/history`. Forecast.Solar public tier (12 req/h):
per-location (3-decimal-rounded) response cache, 30 min TTL.

## Config

- Env (`/etc/buspi/suncast.env` + `secrets.env`): `INFLUX_URL`, `INFLUX_ORG`,
  `INFLUXDB_TOKEN`, `VICTRON_BUCKET=victron`, `GEO_BUCKET=buspi`, field/measurement
  names, `PORT=8090`, `TZ=Europe/Berlin`, clamp/window overrides.
- PanelConfig (SQLite, editable in UI): `panel_wp=260`, `tilt_deg=0`,
  `azimuth_deg=0`, `charger_limit_w=200`, `damping=0`.

## Deploy

systemd + venv, matching every buspi reader: `suncast.service` (User=pi,
`EnvironmentFile=/etc/buspi/secrets.env` + `/etc/buspi/suncast.env`,
Restart=always), venv `/home/pi/suncast-env`, SQLite at
`/var/lib/suncast/suncast.db` (dir owned by pi). Port **:8090**, LAN/Tailscale
only, no auth (LAN-trusted, like gpsd/Grafana local). Influx access read-only
token if available; never in argv. Documented in README + a short section in
buspi-config after first deploy.

## Testing / CI

TDD. Pure modules (provider parsing from fixture JSON, calibrate math, config
parsing) table-tested; IO behind injected HTTP/Influx fakes; API via FastAPI
TestClient with a seeded in-memory SQLite + mocked provider (doubles as demo
mode). Target ≥ 85 % on pure modules. CI (SHA-pinned): pytest, ruff (lint +
format), markdownlint, gitleaks. README badges: CI, release, license.

## Acceptance

- Click → forecast rendered < 5 s on LAN.
- Raw vs corrected always shown separately, factor + sample size visible.
- Backtest works on ≥ 30 days of Victron history.
- Survives missing geo points, missing pv_power intervals, provider 429/outage
  (serves raw-only with a notice when calibration data is unavailable).
- Timezone-safe; restarts cleanly on boot (systemd).

## Later (explicitly out of MVP)

Location-bucketed factors · per-hour factor segmentation · second provider
(PVGIS/Open-Meteo) behind the existing Provider protocol · phase-2 regression
model · route preview · CSV export · Grafana links.
