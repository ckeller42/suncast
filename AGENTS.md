# AGENTS.md

Rules for agents and contributors working on suncast. Keep this file short and true.

## What suncast is

A camper solar forecast service for the buspi Raspberry Pi: a FastAPI web app
(map + address search, hourly/daily PV forecast) that fetches Open-Meteo
(primary) and Forecast.Solar (secondary, comparison only), self-calibrates
against Victron MPPT history in InfluxDB (bulk-charge hours only), stores
snapshots and the calibration in SQLite, and mirrors the forecast to InfluxDB
for Grafana. See `README.md` for behaviour, API and calibration details.

## Layout

| Path | What |
|---|---|
| `suncast/app.py` | FastAPI app and the `suncast` entry point |
| `suncast/config.py` | env-var config (`Config`, `load`) |
| `suncast/providers/` | `open_meteo`, `forecast_solar` forecast clients |
| `suncast/calibrate.py` | calibration factor, applying it, metrics |
| `suncast/models.py` | shared dataclasses (panel, forecast series, calibration) |
| `suncast/pvmodel.py` | PV potential models used by the backtest |
| `suncast/influx.py`, `store.py`, `jobs.py`, `geocode.py` | InfluxDB, SQLite, daily calibration tick, Nominatim |
| `suncast/backfill.py`, `backtest.py` | offline tools `suncast-backfill`, `suncast-backtest` |
| `suncast/templates/`, `static/` | Jinja2 pages, vanilla JS/CSS, vendored Leaflet (`static/vendor/`, not linted) |
| `tests/` | pytest suite (fixtures are synthetic) |
| `deploy/` | `suncast.service` (systemd) and `suncast.env.example` |
| `docs/` | Sphinx site (`index.rst`, `architecture.md`, how-tos, `reference/`) |
| `docs/superpowers/` | design specs, plans and backtest results — local-only (gitignored) |

## Commands

```bash
uv run --extra dev pytest -q                    # tests
uv run --extra dev ruff check . && uv run --extra dev ruff format --check .
uv run --extra dev pre-commit install           # once: commit + pre-push hooks
uv run --extra dev pre-commit run --all-files   # every lint/format/secret hook
```

`.pre-commit-config.yaml` is the single source of truth for tool versions
(pre-commit-hooks, gitleaks, markdownlint-cli2, ruff); the `ruff==` pin in
`pyproject.toml` must match its ruff rev. The pytest hook runs on `pre-push`.
The hooks also lint the workflows (`actionlint`, offline `zizmor`); CI's `test` job runs
`uv sync --locked --extra dev`, so regenerate `uv.lock` (`uv lock`) after editing dependencies.
Markdown rules live only in `.markdownlint-cli2.jsonc`. CI
(`.github/workflows/ci.yml`) runs the same pre-commit hooks, a whole-tree
gitleaks scan, and the tests with a coverage gate on Python 3.11–3.13
(buspi runs 3.13).

## Conventions

- **Config is environment variables only.** `suncast/config.py` is the source
  of truth; the README configuration table must stay in sync with it (names
  and defaults). The offline tools read the same variables plus `HOME_LAT`,
  `HOME_LON`, `BACKTEST_OUT_DIR`.
- `suncast-backfill` and `suncast-backtest` are offline tools run on the Pi
  with the service env sourced; they are not part of the service.
- `deploy/` holds the systemd unit and an example env file; real values live
  on the Pi in `/etc/buspi/suncast.env` and `/etc/buspi/secrets.env`.
- `CHANGELOG.md` (Keep a Changelog) records user-facing changes only — not
  CI, lint or agent-doc housekeeping.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:` (optionally
  scoped, e.g. `feat(backtest):`). Work on a
  branch and open a PR into `main`.

## Docs contract

The site under `docs/` (Sphinx, Furo, MyST, Mermaid; extra `docs` in `pyproject.toml`) follows
the shared concept in buspi-config `DOCUMENTATION.md`:

- Four nav groups in `docs/index.rst`: Getting started, How-to guides, Reference, Explanation.
  Each page belongs to exactly one. One source per fact, link instead of copying.
- One architecture page, `docs/architecture.md`, arc42 sections 1 to 12, drawn with C4-styled
  Mermaid (person #08427b, system #1168bd, container #438dd5, external #999). Mermaid lint:
  no `;`, no bare `&`, `<` or `>`, no `:` in a loop or opt label. Diagram text must match the code.
- The config tables live in the README only (between the `config-service` and `config-offline`
  markers); `docs/reference/configuration.md` includes them and `tests/test_docs_config.py`
  keeps them equal to `suncast/config.py`.
- The build must pass as `uv run --extra docs sphinx-build -b html -W docs docs/_build/html`
  (CI job `docs`, not a required check). `uv lock` after any dependency edit.

## Never commit

Secrets or tokens (`INFLUXDB_TOKEN`, real `*.env` files), databases (`*.db`),
or private location/PV data. gitleaks runs on every commit and in CI
(config `.gitleaks.toml`: default rules; `.venv`, caches and the vendored
Leaflet bundle are excluded because `gitleaks dir` ignores `.gitignore`).
