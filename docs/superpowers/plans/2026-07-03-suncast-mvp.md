# suncast MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local web app on the buspi Pi: click a map destination → Forecast.Solar
forecast for the bus panel → corrected by the observed forecast-vs-actual ratio
from Victron history, with honest snapshot-based backtesting.

**Architecture:** One FastAPI service, server-rendered Jinja2 + vanilla JS +
vendored Leaflet, inline-SVG charts. Pure modules (parsing, calibration) behind
injected IO (HTTP fetch fn, Influx query fn, SQLite store). Daily background
task snapshots the forecast and records yesterday's ratio.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, Jinja2, httpx, influxdb-client,
sqlite3 (stdlib), pytest, ruff. No JS build step.

## Global Constraints

- Repo: `~/src/suncast`, module `suncast`, MIT. Spec: `docs/superpowers/specs/2026-07-02-suncast-design.md`.
- Panel defaults exactly: `panel_wp=260`, `tilt_deg=0`, `azimuth_deg=0` (0 = south, Forecast.Solar convention), `charger_limit_w=200`, `damping=0.0`.
- Calibration exactly: rolling **median** over last `window_days=30` ratios, `min_samples=5`, clamp `[0.3, 1.3]`; band = P25/P75; factor `1.0` + "uncalibrated" flag below min samples.
- Charger cap applied **client-side**: hourly watts clamped to `charger_limit_w`; daily Wh recomputed from capped hourly (1 point ≈ 1 h).
- Honest backtests: ratios only from **archived snapshots** (same-day), never re-fetched.
- Storage: SQLite (path from env, default `/var/lib/suncast/suncast.db`; tests use tmp path). All stored timestamps UTC; UI tz from env (`Europe/Berlin`).
- Port `8090`. Influx read-only. Secrets only via env. Provider cache TTL 1800 s keyed on 3-decimal lat/lon + panel.
- TDD; pure-module coverage target ≥ 85 %. `ruff check` + `ruff format --check` clean.
- Every commit ends with the two standard trailers used in this repo's history (Co-Authored-By Claude Fable 5 + Claude-Session line — copy from `git log -1`).

## File Structure

```text
suncast/
├── pyproject.toml            # project + deps + ruff config + console script
├── suncast/
│   ├── __init__.py
│   ├── models.py             # dataclasses (pure)
│   ├── config.py             # env -> Config (pure-ish)
│   ├── providers/__init__.py
│   ├── providers/forecast_solar.py  # parse (pure) + client (injected fetch)
│   ├── store.py              # SQLite snapshots/ratios/panel
│   ├── influx.py             # flux builders (pure) + reader (injected query fn)
│   ├── calibrate.py          # factor/band/best-window/metrics (pure)
│   ├── jobs.py               # daily snapshot+ratio tick
│   ├── app.py                # FastAPI wiring + routes
│   ├── templates/{index.html,history.html}
│   └── static/{app.js,style.css,vendor/leaflet/...}
├── tests/                    # mirrors modules
├── deploy/{suncast.service,suncast.env.example}
├── .github/workflows/ci.yml  # pytest+ruff+markdownlint+gitleaks (SHA-pinned)
├── .markdownlint.json .gitignore README.md LICENSE
```

---

### Task 1: Scaffold + models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `LICENSE` (MIT, Christoph Keller), `suncast/__init__.py`, `suncast/models.py`
- Test: `tests/test_models.py`

**Interfaces (produced, used by all later tasks):**

```python
# suncast/models.py
from dataclasses import dataclass, field, asdict
from datetime import datetime

@dataclass
class PanelConfig:
    panel_wp: int = 260
    tilt_deg: float = 0.0
    azimuth_deg: float = 0.0        # 0 = south (Forecast.Solar convention)
    charger_limit_w: int = 200      # Victron MPPT 75/15 ~15 A cap
    damping: float = 0.0

@dataclass
class ForecastPoint:
    ts: datetime                    # tz-aware UTC
    watts: float

@dataclass
class ForecastSeries:
    points: list[ForecastPoint]
    daily_wh: dict[str, float]      # "YYYY-MM-DD" (UTC date) -> Wh
    provider: str
    fetched_at: datetime

@dataclass
class DailyRatio:
    day: str                        # "YYYY-MM-DD"
    forecast_wh: float
    actual_wh: float
    ratio: float

@dataclass
class Calibration:
    factor: float
    p25: float
    p75: float
    samples: int
    calibrated: bool
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import datetime, timezone
from suncast.models import PanelConfig, ForecastPoint, ForecastSeries

def test_panel_defaults_match_spec():
    p = PanelConfig()
    assert (p.panel_wp, p.tilt_deg, p.azimuth_deg, p.charger_limit_w, p.damping) == (260, 0.0, 0.0, 200, 0.0)

def test_forecast_series_holds_points():
    ts = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
    s = ForecastSeries(points=[ForecastPoint(ts, 150.0)], daily_wh={"2026-07-03": 900.0},
                       provider="forecast.solar", fetched_at=ts)
    assert s.points[0].watts == 150.0 and s.daily_wh["2026-07-03"] == 900.0
```

- [ ] **Step 2: Run to verify it fails** — `cd ~/src/suncast && python3 -m venv .venv && .venv/bin/pip install -q pytest && .venv/bin/pytest tests/ -q` → import error.
- [ ] **Step 3: Implement** — write `suncast/models.py` exactly as the Interfaces block; empty `suncast/__init__.py`; `pyproject.toml`:

```toml
[project]
name = "suncast"
version = "0.1.0"
description = "Camper solar forecast with self-calibration (buspi)"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1", "httpx>=0.27", "influxdb-client>=1.44"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.6"]

[project.scripts]
suncast = "suncast.app:main"

[tool.ruff]
line-length = 100
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["suncast*"]
```

`.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `dist/`, `.pytest_cache/`, `.ruff_cache/`, `*.db`, `.superpowers/`.

- [ ] **Step 4: Verify pass** — `.venv/bin/pip install -q -e ".[dev]" && .venv/bin/pytest -q` → 2 passed; `.venv/bin/ruff check . && .venv/bin/ruff format .`
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: scaffold + domain models"` (+trailers).

---

### Task 2: Config from env

**Files:** Create `suncast/config.py` · Test `tests/test_config.py`

**Interfaces (produces):**

```python
@dataclass
class Config:
    influx_url: str; influx_org: str; influx_token: str
    victron_bucket: str = "victron"; victron_measurement: str = "victron"; pv_power_field: str = "pv_power"
    geo_bucket: str = "buspi"; geo_measurement: str = "geo"
    port: int = 8090; tz: str = "Europe/Berlin"
    db_path: str = "/var/lib/suncast/suncast.db"
    window_days: int = 30; min_samples: int = 5; clamp_lo: float = 0.3; clamp_hi: float = 1.3
    cache_ttl_s: int = 1800

def load(env: Mapping[str, str]) -> Config   # raises SystemExit with clear msg if INFLUX_URL/INFLUXDB_TOKEN/INFLUX_ORG missing
```

Env names: `INFLUX_URL`, `INFLUX_ORG`, `INFLUXDB_TOKEN`, `VICTRON_BUCKET`,
`VICTRON_MEASUREMENT`, `PV_POWER_FIELD`, `GEO_BUCKET`, `GEO_MEASUREMENT`,
`SUNCAST_PORT`, `SUNCAST_TZ`, `SUNCAST_DB`, `SUNCAST_WINDOW_DAYS`,
`SUNCAST_MIN_SAMPLES`, `SUNCAST_CLAMP_LO`, `SUNCAST_CLAMP_HI`, `SUNCAST_CACHE_TTL_S`.

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py
import pytest
from suncast.config import load

BASE = {"INFLUX_URL": "http://localhost:8086", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"}

def test_defaults():
    c = load(BASE)
    assert c.victron_bucket == "victron" and c.port == 8090 and c.clamp_hi == 1.3
    assert c.db_path == "/var/lib/suncast/suncast.db"

def test_overrides():
    c = load(BASE | {"SUNCAST_PORT": "9000", "VICTRON_MEASUREMENT": "vedirect", "SUNCAST_CLAMP_LO": "0.5"})
    assert c.port == 9000 and c.victron_measurement == "vedirect" and c.clamp_lo == 0.5

def test_missing_required_exits():
    with pytest.raises(SystemExit):
        load({"INFLUX_URL": "x"})
```

- [ ] **Step 2:** `pytest tests/test_config.py -q` → FAIL (no module).
- [ ] **Step 3: Implement** `load()` reading the mapping with int/float coercion; missing required → `raise SystemExit("suncast: set INFLUX_URL, INFLUX_ORG, INFLUXDB_TOKEN")`.
- [ ] **Step 4:** pytest + ruff pass.
- [ ] **Step 5: Commit** `feat: env config loader`.

---

### Task 3: Forecast.Solar parsing (pure)

**Files:** Create `suncast/providers/__init__.py`, `suncast/providers/forecast_solar.py` (parse half), `tests/fixtures/forecast_solar_ok.json` · Test `tests/test_forecast_solar_parse.py`

**Interfaces (produces):**

```python
class ProviderError(Exception): ...
class RateLimited(ProviderError): ...

def parse_estimate(status: int, body: bytes, cap_w: float, now: datetime) -> ForecastSeries
```

Rules: 429 → `RateLimited`; other non-200 or malformed JSON → `ProviderError`
with status in message. Body shape (`?time=utc`): `{"result": {"watts": {"2026-07-03 05:00:00": 12, ...}}}`.
Each entry = one `ForecastPoint` (parsed as UTC), watts clamped to `cap_w`;
`daily_wh[date] = sum(capped watts that UTC date)` (1 point ≈ 1 h).

Fixture `tests/fixtures/forecast_solar_ok.json` (real shape, trimmed):

```json
{"result": {"watts": {
  "2026-07-03 04:00:00": 0, "2026-07-03 08:00:00": 120,
  "2026-07-03 12:00:00": 240, "2026-07-03 16:00:00": 90,
  "2026-07-04 12:00:00": 260}},
 "message": {"type": "success", "ratelimit": {"remaining": 10}}}
```

- [ ] **Step 1: Failing test**

```python
# tests/test_forecast_solar_parse.py
from datetime import datetime, timezone
from pathlib import Path
import pytest
from suncast.providers.forecast_solar import parse_estimate, ProviderError, RateLimited

BODY = (Path(__file__).parent / "fixtures" / "forecast_solar_ok.json").read_bytes()
NOW = datetime(2026, 7, 3, 5, tzinfo=timezone.utc)

def test_parse_caps_watts_and_sums_daily():
    s = parse_estimate(200, BODY, cap_w=200, now=NOW)
    watts = {p.ts.isoformat(): p.watts for p in s.points}
    assert watts["2026-07-03T12:00:00+00:00"] == 200          # 240 capped
    assert s.daily_wh["2026-07-03"] == 0 + 120 + 200 + 90     # capped sum
    assert s.daily_wh["2026-07-04"] == 200                    # 260 capped
    assert s.provider == "forecast.solar" and s.fetched_at == NOW
    assert all(p.ts.tzinfo is not None for p in s.points)

def test_429_raises_ratelimited():
    with pytest.raises(RateLimited):
        parse_estimate(429, b"{}", 200, NOW)

def test_bad_json_raises_provider_error():
    with pytest.raises(ProviderError):
        parse_estimate(200, b"not json", 200, NOW)

def test_500_raises_provider_error_with_status():
    with pytest.raises(ProviderError, match="500"):
        parse_estimate(500, b"", 200, NOW)
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement (`json.loads`, `datetime.strptime(k, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)`, `min(w, cap_w)`, sorted points). **Step 4:** pass + ruff. **Step 5: Commit** `feat(provider): forecast.solar response parsing with charger cap`.

---

### Task 4: Forecast.Solar client (injected fetch + cache)

**Files:** Extend `suncast/providers/forecast_solar.py` · Test `tests/test_forecast_solar_client.py`

**Interfaces (produces):**

```python
FetchFn = Callable[[str], tuple[int, bytes]]   # url -> (status, body)

class ForecastSolar:
    def __init__(self, fetch: FetchFn, cache_ttl_s: int = 1800,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)): ...
    def forecast(self, lat: float, lon: float, panel: PanelConfig, days: int = 3) -> ForecastSeries

def default_fetch(url: str) -> tuple[int, bytes]   # httpx.get(url, timeout=20)
```

URL: `https://api.forecast.solar/estimate/{lat:.4f}/{lon:.4f}/{tilt:g}/{az:g}/{kwp:g}?time=utc`
with `kwp = panel_wp / 1000` (`0.26`). Cache key `(round(lat,3), round(lon,3),
astuple(panel))`, TTL `cache_ttl_s`; `days` filters `points`/`daily_wh` to the
first `days` UTC dates present.

- [ ] **Step 1: Failing test**

```python
# tests/test_forecast_solar_client.py
from datetime import datetime, timezone
from pathlib import Path
from suncast.models import PanelConfig
from suncast.providers.forecast_solar import ForecastSolar

BODY = (Path(__file__).parent / "fixtures" / "forecast_solar_ok.json").read_bytes()

def make(now_holder, calls):
    def fetch(url):
        calls.append(url)
        return 200, BODY
    return ForecastSolar(fetch, cache_ttl_s=1800, now=lambda: now_holder[0])

def test_url_and_days_filter():
    calls, now = [], [datetime(2026, 7, 3, 5, tzinfo=timezone.utc)]
    fs = make(now, calls)
    s = fs.forecast(48.7725, 9.1609, PanelConfig(), days=1)
    assert calls[0] == "https://api.forecast.solar/estimate/48.7725/9.1609/0/0/0.26?time=utc"
    assert list(s.daily_wh) == ["2026-07-03"]
    assert all(p.ts.date().isoformat() == "2026-07-03" for p in s.points)

def test_cache_hits_within_ttl_and_expires():
    calls, now = [], [datetime(2026, 7, 3, 5, tzinfo=timezone.utc)]
    fs = make(now, calls)
    fs.forecast(48.7725, 9.1609, PanelConfig(), 2)
    fs.forecast(48.77251, 9.16091, PanelConfig(), 2)   # same 3-decimal bucket
    assert len(calls) == 1
    now[0] = datetime(2026, 7, 3, 6, tzinfo=timezone.utc)  # ttl 1800s passed
    fs.forecast(48.7725, 9.1609, PanelConfig(), 2)
    assert len(calls) == 2
```

- [ ] **Step 2:** FAIL. **Step 3:** implement client + `default_fetch`. **Step 4:** pass + ruff. **Step 5: Commit** `feat(provider): forecast.solar client with TTL cache`.

---

### Task 5: SQLite store

**Files:** Create `suncast/store.py` · Test `tests/test_store.py`

**Interfaces (produces):**

```python
class Store:
    def __init__(self, path: str): ...          # creates parent dir + schema, WAL
    def save_snapshot(self, s: ForecastSeries, lat: float, lon: float, panel: PanelConfig) -> int
    def snapshot_forecast_wh(self, day: str) -> float | None   # daily_wh[day] from EARLIEST snapshot created on `day` (UTC); None if absent
    def has_snapshot_today(self, day: str) -> bool
    def save_ratio(self, r: DailyRatio, snapshot_id: int) -> None   # INSERT OR REPLACE on day
    def ratios(self, limit: int = 90) -> list[DailyRatio]           # newest first
    def has_ratio(self, day: str) -> bool
    def get_panel(self) -> PanelConfig                              # defaults row auto-created
    def set_panel(self, p: PanelConfig) -> None
    def last_snapshot_age_s(self, now: datetime) -> float | None
```

Schema: `snapshots(id INTEGER PK, created_at TEXT, day TEXT, lat REAL, lon REAL,
panel TEXT, provider TEXT, hourly TEXT, daily TEXT)`;
`ratios(day TEXT PK, forecast_wh REAL, actual_wh REAL, ratio REAL, snapshot_id INT)`;
`panel(id INTEGER PK CHECK(id=1), json TEXT)`. Hourly stored as
`[["<iso ts>", watts], ...]`, daily as JSON object.

- [ ] **Step 1: Failing test**

```python
# tests/test_store.py
from datetime import datetime, timezone, timedelta
from suncast.models import DailyRatio, ForecastPoint, ForecastSeries, PanelConfig
from suncast.store import Store

TS = datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
S = ForecastSeries(points=[ForecastPoint(TS, 100.0)], daily_wh={"2026-07-03": 800.0, "2026-07-04": 900.0},
                   provider="forecast.solar", fetched_at=TS)

def store(tmp_path):
    return Store(str(tmp_path / "s.db"))

def test_snapshot_roundtrip_and_earliest_wins(tmp_path):
    st = store(tmp_path)
    st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    later = ForecastSeries(S.points, {"2026-07-03": 999.0}, "forecast.solar", TS + timedelta(hours=2))
    st.save_snapshot(later, 48.77, 9.16, PanelConfig())
    assert st.snapshot_forecast_wh("2026-07-03") == 800.0     # earliest snapshot of that day
    assert st.snapshot_forecast_wh("2026-06-01") is None
    assert st.has_snapshot_today("2026-07-03") is True

def test_ratios_upsert_and_order(tmp_path):
    st = store(tmp_path)
    sid = st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    st.save_ratio(DailyRatio("2026-07-01", 800, 600, 0.75), sid)
    st.save_ratio(DailyRatio("2026-07-02", 800, 720, 0.90), sid)
    st.save_ratio(DailyRatio("2026-07-01", 800, 640, 0.80), sid)   # replace
    rs = st.ratios()
    assert [r.day for r in rs] == ["2026-07-02", "2026-07-01"]
    assert rs[1].ratio == 0.80
    assert st.has_ratio("2026-07-02") and not st.has_ratio("2026-07-03")

def test_panel_persists(tmp_path):
    st = store(tmp_path)
    assert st.get_panel() == PanelConfig()
    st.set_panel(PanelConfig(panel_wp=300))
    assert st.get_panel().panel_wp == 300

def test_last_snapshot_age(tmp_path):
    st = store(tmp_path)
    assert st.last_snapshot_age_s(TS) is None
    st.save_snapshot(S, 48.77, 9.16, PanelConfig())
    assert st.last_snapshot_age_s(TS + timedelta(seconds=60)) == 60.0
```

- [ ] **Step 2:** FAIL. **Step 3:** implement with stdlib `sqlite3` (`check_same_thread=False`, one connection, `json` for blobs; snapshot `day = fetched_at UTC date`). **Step 4:** pass + ruff. **Step 5: Commit** `feat(store): sqlite snapshots, ratios, panel config`.

---

### Task 6: Influx reader

**Files:** Create `suncast/influx.py` · Test `tests/test_influx.py`

**Interfaces (produces):**

```python
QueryFn = Callable[[str], list[tuple[datetime, float]]]   # flux -> [(time, value)]

def flux_actual_hourly(cfg: Config, day: str) -> str       # pure builder
def flux_latest_location(cfg: Config) -> str               # pure builder (pivots lat/lon/range_m -> 3 rows keyed by field via 3 queries? NO: single query per field set below)

class InfluxReader:
    def __init__(self, cfg: Config, query: QueryFn): ...
    def actual_day_wh(self, day: str) -> float | None       # sum of hourly means (W)·1h; None if < 4 hourly buckets returned
    def latest_location(self) -> tuple[float, float, float, float] | None   # lat, lon, range_m, age_s

def make_query_fn(cfg: Config) -> QueryFn                   # wraps influxdb_client, returns (record.get_time(), record.get_value())
```

`flux_actual_hourly`: range = `day`T00:00Z → +24 h, filter measurement
`cfg.victron_measurement` field `cfg.pv_power_field`,
`aggregateWindow(every: 1h, fn: mean, createEmpty: false)`.
`latest_location`: three small last() queries (lat, lon, range_m) via a shared
helper `._last(field)`; age from the lat record's timestamp vs `datetime.now(UTC)`.

- [ ] **Step 1: Failing test**

```python
# tests/test_influx.py
from datetime import datetime, timezone
from suncast.config import load
from suncast.influx import InfluxReader, flux_actual_hourly

CFG = load({"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"})

def t(h): return datetime(2026, 7, 2, h, tzinfo=timezone.utc)

def test_flux_contains_day_window_and_names():
    q = flux_actual_hourly(CFG, "2026-07-02")
    for frag in ['from(bucket: "victron")', "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z",
                 '"pv_power"', "aggregateWindow(every: 1h", 'r._measurement == "victron"']:
        assert frag in q

def test_actual_day_wh_sums_hourly_means():
    rows = [(t(h), 100.0) for h in range(6, 18)]           # 12 h × 100 W
    r = InfluxReader(CFG, lambda q: rows)
    assert r.actual_day_wh("2026-07-02") == 1200.0

def test_actual_day_wh_none_when_too_sparse():
    r = InfluxReader(CFG, lambda q: [(t(6), 100.0)])       # 1 bucket < 4
    assert r.actual_day_wh("2026-07-02") is None

def test_latest_location_combines_fields():
    def q(flux):
        now = datetime.now(timezone.utc)
        if '"lat"' in flux: return [(now, 48.77)]
        if '"lon"' in flux: return [(now, 9.16)]
        return [(now, 20.0)]
    lat, lon, rng, age = InfluxReader(CFG, q).latest_location()
    assert (lat, lon, rng) == (48.77, 9.16, 20.0) and age < 5

def test_latest_location_none_when_empty():
    assert InfluxReader(CFG, lambda q: []).latest_location() is None
```

- [ ] **Step 2:** FAIL. **Step 3:** implement; `make_query_fn` is 6 lines wrapping `InfluxDBClient(...).query_api().query(...)` flattening records (not unit-tested; exercised on the Pi). **Step 4:** pass + ruff. **Step 5: Commit** `feat(influx): actual Wh + latest location readers`.

---

### Task 7: Calibration math (pure)

**Files:** Create `suncast/calibrate.py` · Test `tests/test_calibrate.py`

**Interfaces (produces):**

```python
def calibration(ratios: list[float], window: int = 30, min_samples: int = 5,
                lo: float = 0.3, hi: float = 1.3) -> Calibration
    # uses ratios[:window] (caller passes newest-first); median/P25/P75 via statistics.quantiles(n=4)
    # < min_samples -> Calibration(1.0, 1.0, 1.0, len, calibrated=False); factor+band clamped to [lo, hi]

def apply_factor(series: ForecastSeries, cal: Calibration) -> dict
    # {"hourly": [[iso, raw_w, corrected_w], ...], "daily": {day: {"raw_wh", "corrected_wh", "lower_wh", "upper_wh"}}}

def best_window(points: list[ForecastPoint], hours: int = 4) -> dict[str, dict]
    # per UTC day: {"start": iso, "end": iso, "wh": float} maximizing rolling `hours`-sum of watts

def metrics(pairs: list[tuple[float, float]]) -> dict
    # pairs = (forecast_wh, actual_wh); {"mae", "rmse", "mape_pct", "bias_wh", "n"};
    # MAPE skips days with actual < 50 Wh; empty -> zeros with n=0
```

- [ ] **Step 1: Failing test**

```python
# tests/test_calibrate.py
from datetime import datetime, timezone
from suncast.calibrate import apply_factor, best_window, calibration, metrics
from suncast.models import ForecastPoint, ForecastSeries

def test_calibration_median_and_band():
    c = calibration([0.8, 0.9, 1.0, 0.85, 0.95, 0.7], window=30, min_samples=5)
    assert c.calibrated and c.samples == 6
    assert 0.84 <= c.factor <= 0.88            # median of the six
    assert c.p25 <= c.factor <= c.p75

def test_calibration_uncalibrated_below_min():
    c = calibration([0.5, 0.6], min_samples=5)
    assert not c.calibrated and c.factor == 1.0 and c.samples == 2

def test_calibration_clamps():
    c = calibration([0.05] * 10)
    assert c.factor == 0.3
    c = calibration([9.0] * 10)
    assert c.factor == 1.3

def test_calibration_uses_window_newest_first():
    ratios = [1.0] * 5 + [0.2] * 50           # newest five are 1.0
    assert calibration(ratios, window=5).factor == 1.0

def _series():
    ts = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
    return ForecastSeries([ForecastPoint(ts, 100.0)], {"2026-07-03": 1000.0}, "p", ts)

def test_apply_factor():
    c = calibration([0.8] * 6)
    out = apply_factor(_series(), c)
    d = out["daily"]["2026-07-03"]
    assert d["raw_wh"] == 1000.0 and round(d["corrected_wh"]) == 800
    assert d["lower_wh"] <= d["corrected_wh"] <= d["upper_wh"]
    assert out["hourly"][0][1] == 100.0 and round(out["hourly"][0][2]) == 80

def test_best_window_picks_peak_block():
    base = datetime(2026, 7, 3, 6, tzinfo=timezone.utc)
    pts = [ForecastPoint(base.replace(hour=h), w) for h, w in
           [(6, 10), (7, 20), (8, 50), (9, 200), (10, 200), (11, 200), (12, 200), (13, 50)]]
    w = best_window(pts, hours=4)["2026-07-03"]
    assert w["start"].startswith("2026-07-03T09") and w["wh"] == 800

def test_metrics_safe_mape():
    m = metrics([(1000, 800), (500, 30)])      # 2nd actual < 50 Wh -> excluded from MAPE
    assert m["n"] == 2 and m["mae"] == (200 + 470) / 2
    assert m["mape_pct"] == 25.0               # only first pair: 200/800
    assert metrics([])["n"] == 0
```

- [ ] **Step 2:** FAIL. **Step 3:** implement with `statistics.median` / `statistics.quantiles(data, n=4)` (guard len<2 → p25=p75=median). Bias = mean(forecast−actual). **Step 4:** pass + ruff. **Step 5: Commit** `feat(calibrate): median-ratio factor, band, best window, metrics`.

---

### Task 8: Daily job

**Files:** Create `suncast/jobs.py` · Test `tests/test_jobs.py`

**Interfaces (produces):**

```python
@dataclass
class Deps:
    provider: ForecastSolar          # anything with .forecast(lat, lon, panel, days)
    store: Store
    influx: InfluxReader
    now: Callable[[], datetime]      # tz-aware UTC

def daily_tick(d: Deps) -> dict     # idempotent; returns {"snapshotted": bool, "ratio_day": str|None, "skipped": str|None}
```

Logic: `today = now().date`; if no `has_snapshot_today(today)`: get
`influx.latest_location()` → if present, `provider.forecast(lat, lon, store.get_panel(), days=3)`
→ `save_snapshot`. Then `yesterday = today − 1 d`: if not `has_ratio(yesterday)`
and `snapshot_forecast_wh(yesterday)` and `influx.actual_day_wh(yesterday)` are
both present and forecast > 0 → save `DailyRatio(yesterday, f, a, a / f)`.
All exceptions caught per-phase and reported in the return dict (`"skipped"`),
never raised (job must not kill the app loop).

- [ ] **Step 1: Failing test**

```python
# tests/test_jobs.py
from datetime import datetime, timezone
from suncast.jobs import Deps, daily_tick
from suncast.models import ForecastPoint, ForecastSeries, PanelConfig
from suncast.store import Store

NOW = datetime(2026, 7, 3, 5, 30, tzinfo=timezone.utc)
S = ForecastSeries([ForecastPoint(NOW, 100.0)], {"2026-07-03": 800.0}, "forecast.solar", NOW)

class FakeProvider:
    def __init__(self): self.calls = []
    def forecast(self, lat, lon, panel, days=3):
        self.calls.append((lat, lon)); return S

class FakeInflux:
    def __init__(self, loc, wh): self._loc, self._wh = loc, wh
    def latest_location(self): return self._loc
    def actual_day_wh(self, day): return self._wh

def deps(tmp_path, loc=(48.77, 9.16, 20.0, 60.0), wh=640.0):
    return Deps(FakeProvider(), Store(str(tmp_path / "s.db")), FakeInflux(loc, wh), lambda: NOW)

def test_snapshots_once_per_day(tmp_path):
    d = deps(tmp_path)
    r1 = daily_tick(d); r2 = daily_tick(d)
    assert r1["snapshotted"] and not r2["snapshotted"]
    assert len(d.provider.calls) == 1

def test_ratio_for_yesterday_from_archived_snapshot(tmp_path):
    d = deps(tmp_path)
    y = ForecastSeries(S.points, {"2026-07-02": 800.0}, "forecast.solar",
                       datetime(2026, 7, 2, 5, tzinfo=timezone.utc))
    d.store.save_snapshot(y, 48.77, 9.16, PanelConfig())
    out = daily_tick(d)
    assert out["ratio_day"] == "2026-07-02"
    assert d.store.ratios()[0].ratio == 0.8               # 640/800

def test_no_location_skips_snapshot_gracefully(tmp_path):
    out = daily_tick(deps(tmp_path, loc=None))
    assert not out["snapshotted"] and out["skipped"]
```

- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** pass + ruff. **Step 5: Commit** `feat(jobs): idempotent daily snapshot + ratio tick`.

---

### Task 9: FastAPI app + JSON API

**Files:** Create `suncast/app.py` · Test `tests/test_api.py`

**Interfaces (produces):**

```python
def create_app(cfg: Config, provider, store: Store, influx) -> FastAPI
def main() -> None    # load(os.environ), build real deps (make_query_fn, default_fetch), uvicorn.run(app, host="0.0.0.0", port=cfg.port)
```

Routes:
- `POST /api/forecast` body `{lat, lon, days=3, panel?}` → `{location: {lat, lon}, factor: {factor, p25, p75, samples, calibrated}, hourly, daily, best_windows}` — provider errors → 502 `{detail}`; RateLimited → 429. `panel` override is per-request only (not persisted).
- `GET /api/history?days=30` → `{days: [{day, forecast_wh, actual_wh, ratio}...] (oldest first), metrics_raw, factor}`.
- `GET /api/config` → PanelConfig json; `POST /api/config` (full PanelConfig json) → persists via `store.set_panel`, returns saved.
- `GET /api/current-location` → `{lat, lon, range_m, age_s}` or 404.
- `GET /api/health` → `{influx_ok, last_snapshot_age_s, ratios}` (never 500; fields None on failure).
- Startup: `asyncio.create_task` loop → `daily_tick` every 3600 s (skip via `app.state.no_jobs = True` in tests).
- Pages `/` and `/history` are added in Task 10; in this task they may 404.

- [ ] **Step 1: Failing test**

```python
# tests/test_api.py
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from suncast.app import create_app
from suncast.config import load
from suncast.models import DailyRatio, ForecastPoint, ForecastSeries, PanelConfig
from suncast.providers.forecast_solar import RateLimited
from suncast.store import Store

CFG = load({"INFLUX_URL": "http://x", "INFLUX_ORG": "home", "INFLUXDB_TOKEN": "t"})
NOW = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
S = ForecastSeries([ForecastPoint(NOW, 240.0)], {"2026-07-03": 1000.0}, "forecast.solar", NOW)

class P:
    def forecast(self, lat, lon, panel, days=3): return S
class P429:
    def forecast(self, *a, **k): raise RateLimited("quota")
class FakeInflux:
    def latest_location(self): return (48.77, 9.16, 20.0, 30.0)
    def actual_day_wh(self, day): return 800.0

def client(tmp_path, provider=None):
    store = Store(str(tmp_path / "s.db"))
    for i, r in enumerate([0.8, 0.85, 0.9, 0.8, 0.85, 0.9]):
        store.save_ratio(DailyRatio(f"2026-06-{20 + i:02d}", 1000, 1000 * r, r), 0)
    app = create_app(CFG, provider or P(), store, FakeInflux())
    app.state.no_jobs = True
    return TestClient(app)

def test_forecast_endpoint(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/forecast", json={"lat": 48.77, "lon": 9.16, "days": 2})
    assert r.status_code == 200
    j = r.json()
    assert j["factor"]["calibrated"] and 0.8 <= j["factor"]["factor"] <= 0.9
    assert j["daily"]["2026-07-03"]["raw_wh"] == 1000.0
    assert j["daily"]["2026-07-03"]["corrected_wh"] < 1000.0
    assert "2026-07-03" in j["best_windows"]

def test_forecast_rate_limited_is_429(tmp_path):
    assert client(tmp_path, P429()).post("/api/forecast", json={"lat": 1, "lon": 2}).status_code == 429

def test_config_roundtrip(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/config").json()["panel_wp"] == 260
    r = c.post("/api/config", json={**PanelConfig().__dict__, "panel_wp": 300})
    assert r.json()["panel_wp"] == 300 and c.get("/api/config").json()["panel_wp"] == 300

def test_history_and_health_and_location(tmp_path):
    c = client(tmp_path)
    h = c.get("/api/history?days=30").json()
    assert len(h["days"]) == 6 and h["days"][0]["day"] == "2026-06-20"
    assert c.get("/api/current-location").json()["lat"] == 48.77
    assert c.get("/api/health").status_code == 200
```

- [ ] **Step 2:** FAIL. **Step 3:** implement `create_app` (deps on `app.state`; pydantic request models inline or plain dict validation — keep plain dicts + explicit checks, 422 on missing lat/lon). **Step 4:** pass + ruff. **Step 5: Commit** `feat(api): forecast/history/config/location/health endpoints`.

---

### Task 10: UI (map + forecast page, history page)

**Files:** Create `suncast/templates/index.html`, `suncast/templates/history.html`, `suncast/static/style.css`, `suncast/static/app.js`; vendor Leaflet 1.9.4 into `suncast/static/vendor/leaflet/` (`leaflet.js`, `leaflet.css`, `images/`) · Modify `suncast/app.py` (mount static, add page routes) · Test `tests/test_pages.py`

Vendoring step:

```bash
cd ~/src/suncast/suncast/static && mkdir -p vendor/leaflet/images
curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o vendor/leaflet/leaflet.js
curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o vendor/leaflet/leaflet.css
for f in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
  curl -sL "https://unpkg.com/leaflet@1.9.4/dist/images/$f" -o "vendor/leaflet/images/$f"; done
```

`index.html`: Leaflet map (OSM tile URL, attribution), marker for current
location (fetched from `/api/current-location`, with `range_m` circle), click
sets target marker, side panel: panel-config form (5 fields, loads/saves via
`/api/config`), days selector (1–6), "Forecast here" button → `POST
/api/forecast` → renders: per-day cards (raw Wh vs corrected Wh + band +
best-window time span) and an hourly SVG chart (two polylines: raw, corrected;
built by `app.js` `svgChart(hourly)` with viewBox scaling, no library).
Factor + sample count + "uncalibrated" badge shown. Errors shown in a status line.

`history.html`: fetches `/api/history?days=30`; table (day, forecast Wh, actual
Wh, ratio) + summary line (MAE, MAPE, bias, n, current factor) + inline SVG bar
pairs (raw vs actual) reusing `svgBars(days)` from `app.js`.

`app.js` exposes `initMap()`, `svgChart(hourly)`, `svgBars(days)`; plain ES6,
no modules, ~150 lines. `style.css`: mobile-first single column, panel cards.

- [ ] **Step 1: Failing test**

```python
# tests/test_pages.py
def test_index_serves_map(tmp_path):
    from tests.test_api import client
    c = client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "vendor/leaflet/leaflet.js" in r.text and 'id="map"' in r.text

def test_history_page(tmp_path):
    from tests.test_api import client
    c = client(tmp_path)
    assert c.get("/history").status_code == 200

def test_leaflet_vendored():
    from pathlib import Path
    v = Path("suncast/static/vendor/leaflet")
    assert (v / "leaflet.js").stat().st_size > 100_000
    assert (v / "leaflet.css").exists()
```

- [ ] **Step 2:** FAIL. **Step 3:** vendor Leaflet, write templates/static, mount `StaticFiles`, add `Jinja2Templates` page routes. **Step 4:** pass + ruff; manual smoke `uvicorn suncast.app:… --reload` optional. **Step 5: Commit** `feat(ui): map+forecast and history pages (vendored leaflet, svg charts)`.

---

### Task 11: Deploy assets + README

**Files:** Create `deploy/suncast.service`, `deploy/suncast.env.example`, `README.md`

`deploy/suncast.service`:

```ini
[Unit]
Description=suncast — camper solar forecast (map + calibrated Forecast.Solar)
After=network-online.target influxdb.service
Wants=network-online.target

[Service]
ExecStart=/home/pi/suncast-env/bin/suncast
EnvironmentFile=/etc/buspi/secrets.env
EnvironmentFile=/etc/buspi/suncast.env
Restart=always
RestartSec=15
User=pi

[Install]
WantedBy=multi-user.target
```

`deploy/suncast.env.example` (non-secret; token comes from secrets.env):

```bash
INFLUX_URL=http://localhost:8086
INFLUX_ORG=home
VICTRON_BUCKET=victron
# Confirm on the Pi: influx measurement name used by vedirect/victron reader
VICTRON_MEASUREMENT=victron
PV_POWER_FIELD=pv_power
GEO_BUCKET=buspi
GEO_MEASUREMENT=geo
SUNCAST_PORT=8090
SUNCAST_TZ=Europe/Berlin
SUNCAST_DB=/var/lib/suncast/suncast.db
```

`README.md`: badges (CI, release, MIT), what/why (2 paragraphs, honest-forecast
pitch), screenshot placeholder comment, install on Pi (venv `/home/pi/suncast-env`,
`pip install .`, `sudo install -d -o pi /var/lib/suncast`, copy unit + env,
`systemctl enable --now suncast`), config table (all env vars), API table,
calibration explanation (median ratio, clamp, uncalibrated state), dev section
(`pip install -e ".[dev]"`, `pytest`, `ruff check`). Passes markdownlint.

- [ ] **Steps:** write files → `npx --yes markdownlint-cli@0.42.0 --config .markdownlint.json '**/*.md'` clean → commit `docs+deploy: systemd unit, env example, README`.

---

### Task 12: CI + GitHub repo + push

**Files:** Create `.github/workflows/ci.yml`, `.markdownlint.json`

`.markdownlint.json`: `{"default": true, "MD013": false, "MD033": false, "MD041": false}`.

`ci.yml` (SHA-pinned, mirror celloc style):

```yaml
name: CI
on:
  push: {branches: [main]}
  pull_request:
permissions: {contents: read}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - uses: actions/setup-python@f677139bbe7f9c59b41e40162b753c062f5d49a3 # v5
        with: {python-version: "3.11"}
      - run: pip install -e ".[dev]"
      - run: ruff check . && ruff format --check .
      - name: Tests + coverage gate (pure modules >= 85%)
        run: |
          pip install pytest-cov
          pytest -q --cov=suncast.calibrate --cov=suncast.config \
            --cov=suncast.providers.forecast_solar --cov=suncast.models --cov-fail-under=85
  markdownlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - run: npx --yes markdownlint-cli@0.42.0 --config .markdownlint.json '**/*.md'
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - run: |
          curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz | tar -xz gitleaks
          ./gitleaks detect --source . --no-git --redact -v
```

- [ ] **Steps:** add files → local `pytest` + ruff + markdownlint all green →
commit `ci: pytest+coverage gate, ruff, markdownlint, gitleaks` →
`gh repo create ckeller42/suncast --public --source . --push` → verify Actions
run green (`gh run watch`). Branch protection + CodeRabbit can be added after
first green like celloc.

---

### Task 13: Deploy to the Pi + live verify (controller-run, needs sudo)

Not a subagent task. Steps: build venv on Pi (`python3 -m venv /home/pi/suncast-env && pip install git+https://github.com/ckeller42/suncast`), `sudo install -d -o pi /var/lib/suncast`, write `/etc/buspi/suncast.env` (confirm real `VICTRON_MEASUREMENT` via `influx` query first), install unit, `systemctl enable --now suncast`, open `http://buspi:8090`, click a destination, confirm raw+uncalibrated forecast renders; confirm first snapshot row lands in SQLite. Document in buspi-config README (Services tree + new section) via the normal PR flow.

## Self-Review

- **Spec coverage:** models→T1, config→T2, provider parse/client→T3/4, store→T5, influx→T6, calibrate→T7, jobs→T8, API→T9, UI/map/charts→T10, deploy/README→T11, CI/repo→T12, Pi verify + buspi-config docs→T13. Acceptance criteria all land (speed via cache+local render; raw-vs-corrected separation in API+UI; ≥30 d backtest via history endpoint; provider-outage → 429/502 handled, raw-only impossible to confuse; tz-safe via UTC storage).
- **Placeholders:** none; every code step has full code. T10 templates described precisely with function names (`initMap`, `svgChart`, `svgBars`) — implementer writes markup but all contracts are fixed.
- **Type consistency:** `ForecastSeries(points, daily_wh, provider, fetched_at)`, `Store` methods, `Calibration` fields, `Deps`, endpoint shapes cross-checked across T3–T10.
