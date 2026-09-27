# Changelog

All notable changes to suncast are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`suncast-backfill` console tool.** For every past day with Victron
  `pv_power`, fetches Open-Meteo's ERA5 reanalysis (actual past irradiance) at
  the van's location that day (mean `geo` fix, or `HOME_LAT` / `HOME_LON`
  before location history), converts it to expected panel watts and writes it
  to the `solar_forecast` measurement tagged `provider=open-meteo-era5`, so the
  Grafana forecast-vs-absorbed panel spans the full history. Expected
  potential only — never used for the forward calibration. One bad day never
  aborts the run.
- **`suncast-backtest` console tool.** Offline evaluation of candidate
  potential-prediction models (M0 flat factor, M1 fixed temperature derate,
  M2 fitted `k`/`gamma`) against Victron bulk hours using ERA5 irradiance and
  air temperature. Every model and metric (bulk-hour MAE/bias, clean-day MAE)
  is scored leave-one-day-out, i.e. out-of-sample. Reads the stored panel
  config read-only (never creates a row). First verdict (2026-07-05): no
  temperature signal — keep the flat factor.

### Changed

- **Backtest output is date-stamped and configurable** (#3). Results go to
  `{BACKTEST_OUT_DIR}/{run_date}-backtest.md` (default directory
  `docs/superpowers/results`, relative to the working directory) with a header
  recording the run date and the data-through date, so a later re-run never
  overwrites an earlier verdict.

### Fixed

- InfluxDB queries whose aggregate drops the `_time` column (e.g.
  `group() |> mean()`, used for the backfill's per-day location mean) no
  longer raise `KeyError`.

## [0.2.0] - 2026-07-05

Provider, calibration-honesty, and location upgrades from the first week of
live operation on the buspi Pi.

### Added

- **Open-Meteo as the primary forecast provider.** Best regional model for
  Europe (DWD ICON-D2 at 2 km, MeteoFrance AROME), 16-day horizon, no API key
  or rate limit. Irradiance is converted to panel watts via the STC relation;
  the resulting optimism is a constant the self-calibration removes.
- **Forecast.Solar kept as a secondary, raw comparison.** Surfaced on the map
  as a dashed amber curve plus per-day spread %, an honest agreement /
  disagreement signal. Best-effort — a secondary failure never breaks the
  primary forecast. Selectable via `PROVIDER` / `PROVIDER_SECONDARY`.
  (Removal tracked in issue #1 for KISS.)
- **Forecast mirrored to InfluxDB** (`solar_forecast` measurement: `raw_w`,
  `corrected_w`, `factor`; tag `provider`) so Grafana can overlay predicted vs
  absorbed PV. Display-only; SQLite stays the calibration source of truth.
- **Address search on the map** via OSM Nominatim (`/api/geocode`).
- **Location drift guard.** Skips a day's calibration when the van roamed more
  than `DRIFT_KM_MAX` (default 20 km) from the snapshot location — travel days
  no longer poison the ratio.

### Changed

- **Calibration compares only unthrottled (bulk) charge hours.** `pv_power`
  measures energy the battery *accepted*; once full the MPPT throttles and
  actuals collapse while the sun blazes. Ratios now use bulk-hour actuals vs
  the archived forecast for the same hours, skipping days with < 2 bulk hours
  or < 50 Wh bulk forecast.
- Forecast horizon accepted up to 16 days (was 1–6) to match Open-Meteo.

### Fixed

- **Current location now takes the freshest fix across geo tag-series.** The
  `geo` measurement carries parallel `wifi` and `cell` series; the code took an
  arbitrary one and could report a stale cell fix (observed: 5.5 h old, 21 km
  off) while a per-minute wifi fix was available.
- `/api/history` kept the oldest rows once history exceeded the window
  (reverse-then-slice bug); non-numeric lat/lon now return 422 not 500.
- Provider parser accepts ISO-8601 timestamps from the live Forecast.Solar API.
- Wheel ships templates and static assets (service crashed on missing
  StaticFiles dir).
- Chart axes labelled (W / Wh, local time); `days` selector re-forecasts on
  change and explains the free-tier 2-day limit.

## [0.1.0] - 2026-07-04

Initial release: FastAPI + Jinja2 + vanilla JS + vendored Leaflet map, Victron
MPPT self-calibration against archived Forecast.Solar snapshots, SQLite store,
systemd + venv deploy on the buspi Pi, CI (pytest + coverage gate, ruff,
markdownlint, gitleaks).

[0.2.0]: https://github.com/ckeller42/suncast/releases/tag/v0.2.0
[0.1.0]: https://github.com/ckeller42/suncast/releases/tag/v0.1.0
