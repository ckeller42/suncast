# suncast prediction backtest harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline `suncast-backtest` CLI that scores candidate PV
potential-prediction models against real Victron history and reports, out-of-sample,
whether a temperature term beats the flat calibration factor.

**Architecture:** A pure model module (`pvmodel.py`) holds one temperature-aware
`expected_w` function driven by a `Params` set (M0 = no temp term, M1 = fixed
coefficients, M2 = fitted) plus a stdlib closed-form 2-variable least-squares fit.
A harness module (`backtest.py`) assembles hourly ERA5 (irradiance + temperature)
and Victron (`pv_power`, `charge_state`) rows, selects bulk hours / clean days,
scores models (leave-one-day-out for the fitted model), and renders a table + a
results doc. No change to the live service.

**Tech Stack:** Python 3.11, stdlib only for the maths (`statistics`, `math` —
no numpy/scipy), the existing `QueryFn`/`make_query_fn` for InfluxDB reads and the
Open-Meteo archive HTTP fetch, pytest + ruff.

## Global Constraints

- stdlib only for computation — no numpy, scipy, or pandas.
- Pure/IO split: `pvmodel.py` has zero IO; all model functions take scalars and a
  `Params`. `backtest.py` does IO through injected `query`/`fetch` callables.
- ruff clean (`ruff check --fix` + `ruff format`) and full pytest suite green before
  every commit; report the FULL-suite count.
- Every commit message ends with these trailers verbatim:

  ```text
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01WPAdcXLcTryDXT8pZyLe8W
  ```

- Existing signatures this plan builds on (do not redefine):
  - `QueryFn = Callable[[str], list[tuple[datetime, float]]]` (`suncast/influx.py`)
  - `flux_hourly_field(cfg, day, field) -> str` (`suncast/influx.py`)
  - `make_query_fn(cfg) -> QueryFn` (`suncast/influx.py`)
  - `pv_first_day(query, cfg) -> date | None` and
    `day_location(query, cfg, day, home) -> tuple[float, float]` (`suncast/backfill.py`)
  - `default_fetch(url) -> tuple[int, bytes]` (`suncast/providers/open_meteo.py`)
  - `PanelConfig(panel_wp=260, tilt_deg=0.0, azimuth_deg=0.0, charger_limit_w=200, damping=0.0)`
    (`suncast/models.py`)
  - `Config` fields: `victron_bucket, victron_measurement, pv_power_field,
    charge_state_field, geo_bucket, geo_measurement, db_path` (`suncast/config.py`)

---

## File Structure

- `suncast/pvmodel.py` (new) — `Params` dataclass, `cell_temp`, `expected_w`,
  `fit_two_var`, `fit_m2`. Pure.
- `suncast/backtest.py` (new) — `HourRow`, selection (`is_bulk`, `bulk_pairs`,
  `clean_days`), metric (`mae_bias`), `flat_k`, scoring (`score_fixed`,
  `score_lodo`), data assembly (`archive_gti_temp`, `assemble`), `render_table`,
  `run`, `main`.
- `tests/test_pvmodel.py` (new), `tests/test_backtest.py` (new).
- `pyproject.toml` (modify) — add `suncast-backtest` console script.
- `README.md` (modify) — short "Backtest" dev section.
- `docs/superpowers/results/2026-07-05-backtest.md` — written by the tool at run time
  (not committed by the plan).

---

### Task 1: pvmodel — Params + temperature-aware expected_w

**Files:**

- Create: `suncast/pvmodel.py`
- Test: `tests/test_pvmodel.py`

**Interfaces:**

- Consumes: `PanelConfig` from `suncast.models`.
- Produces:
  - `Params(k: float, gamma: float = 0.0, noct: float = 45.0)`
  - `cell_temp(gti: float, t_air: float, noct: float = 45.0) -> float`
  - `expected_w(gti: float, t_air: float, panel: PanelConfig, params: Params) -> float`

One function serves all three models: M0 = `Params(k, gamma=0.0)`,
M1 = `Params(k, gamma=-0.004)`, M2 = `Params(k_fit, gamma_fit)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pvmodel.py
from suncast.models import PanelConfig
from suncast.pvmodel import Params, cell_temp, expected_w

PANEL = PanelConfig()  # 260 Wp, 200 W cap


def test_cell_temp_adds_irradiance_heating():
    # NOCT 45 -> coefficient (45-20)/800 = 0.03125 per W/m^2
    assert cell_temp(0.0, 20.0) == 20.0
    assert cell_temp(800.0, 20.0) == 20.0 + 800.0 * 0.03125  # 45.0


def test_expected_w_m0_is_flat_base_times_k():
    # gamma 0 -> no temperature term: 500 W/m^2 -> 260*0.5*0.47 = 61.1
    p = Params(k=0.47, gamma=0.0)
    assert abs(expected_w(500.0, 20.0, PANEL, p) - 61.1) < 1e-9


def test_expected_w_m1_equals_m0_at_25C_and_derates_when_hot():
    base = Params(k=0.47, gamma=0.0)
    temp = Params(k=0.47, gamma=-0.004)
    # Find a (gti, t_air) giving cell_temp == 25 -> both models agree.
    # cell_temp = t_air + gti*0.03125 = 25 with gti=0 -> t_air=25.
    assert expected_w(0.0, 25.0, PANEL, base) == expected_w(0.0, 25.0, PANEL, temp)
    # Hot cell (gti 800, air 30 -> cell 55) derates the temp model below flat.
    assert expected_w(800.0, 30.0, PANEL, temp) < expected_w(800.0, 30.0, PANEL, base)


def test_expected_w_caps_and_floors():
    p = Params(k=1.0, gamma=0.0)
    # 1000 W/m^2 -> 260 W, capped to 200.
    assert expected_w(1000.0, 20.0, PANEL, p) == 200.0
    # Negative never happens, but a huge negative gamma must floor at 0.
    assert expected_w(500.0, 90.0, PANEL, Params(k=0.47, gamma=-1.0)) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pvmodel.py -q`
Expected: FAIL (`ModuleNotFoundError: suncast.pvmodel`).

- [ ] **Step 3: Write minimal implementation**

```python
# suncast/pvmodel.py
"""Pure PV potential-prediction models for the backtest harness.

One function, three parameterizations:
  M0 flat        -> Params(k, gamma=0.0)
  M1 temp fixed  -> Params(k, gamma=-0.004)
  M2 temp fitted -> Params(k_fit, gamma_fit)
"""

from dataclasses import dataclass

from suncast.models import PanelConfig


@dataclass
class Params:
    k: float
    gamma: float = 0.0  # power temperature coefficient, per degC
    noct: float = 45.0  # nominal operating cell temperature


def cell_temp(gti: float, t_air: float, noct: float = 45.0) -> float:
    """Estimated cell temperature (degC): air + irradiance heating (NOCT model)."""
    return t_air + gti * (noct - 20.0) / 800.0


def expected_w(gti: float, t_air: float, panel: PanelConfig, params: Params) -> float:
    """Predicted panel watts from tilted irradiance and air temperature, capped."""
    base = gti / 1000.0 * panel.panel_wp
    tcell = cell_temp(gti, t_air, params.noct)
    w = base * params.k * (1.0 + params.gamma * (tcell - 25.0))
    return min(max(w, 0.0), float(panel.charger_limit_w))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pvmodel.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/pvmodel.py tests/test_pvmodel.py
git commit -m "feat(pvmodel): temperature-aware expected_w with Params (M0/M1)"
```

---

### Task 2: pvmodel — closed-form 2-variable least-squares fit (M2)

**Files:**

- Modify: `suncast/pvmodel.py`
- Test: `tests/test_pvmodel.py`

**Interfaces:**

- Consumes: `Params`, `cell_temp` (Task 1); `PanelConfig`.
- Produces:
  - `fit_two_var(pairs: list[tuple[tuple[float, float], float]]) -> tuple[float, float] | None`
    — solves `y ≈ a·x1 + b·x2`; returns `(a, b)`, or `None` if singular.
  - `fit_m2(rows: list[tuple[float, float, float]], panel: PanelConfig) -> Params | None`
    — each row is `(gti, t_air, actual_w)`; builds features `x1=base`,
    `x2=base·(cell_temp-25)`, fits, returns `Params(k=a, gamma=b/a)` (or `None`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pvmodel.py
from suncast.pvmodel import fit_m2, fit_two_var


def test_fit_two_var_recovers_linear_coeffs():
    # y = 2*x1 + 3*x2 exactly.
    pairs = [((1.0, 0.0), 2.0), ((0.0, 1.0), 3.0), ((1.0, 1.0), 5.0), ((2.0, 1.0), 7.0)]
    a, b = fit_two_var(pairs)
    assert abs(a - 2.0) < 1e-9 and abs(b - 3.0) < 1e-9


def test_fit_two_var_singular_returns_none():
    # All x2 == 0 -> normal-equations determinant is 0.
    pairs = [((1.0, 0.0), 1.0), ((2.0, 0.0), 2.0)]
    assert fit_two_var(pairs) is None


def test_fit_m2_recovers_known_k_and_gamma():
    from suncast.models import PanelConfig
    from suncast.pvmodel import Params, cell_temp, expected_w

    panel = PanelConfig()
    true = Params(k=0.5, gamma=-0.004)
    # Synthesize rows from the true model over varied conditions (avoid the cap:
    # keep base*k well under 200 by using modest irradiance).
    rows = []
    for gti in (100.0, 250.0, 400.0, 550.0, 700.0):
        for t_air in (5.0, 20.0, 35.0):
            w = expected_w(gti, t_air, panel, true)
            rows.append((gti, t_air, w))
    fitted = fit_m2(rows, panel)
    assert abs(fitted.k - 0.5) < 1e-6
    assert abs(fitted.gamma - (-0.004)) < 1e-6
    _ = cell_temp  # imported for clarity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pvmodel.py -q`
Expected: FAIL (`ImportError: cannot import name 'fit_m2'`).

- [ ] **Step 3: Write minimal implementation**

```python
# append to suncast/pvmodel.py

def fit_two_var(pairs):
    """Ordinary least squares for y ~ a*x1 + b*x2 via the 2x2 normal equations."""
    s11 = s12 = s22 = s1y = s2y = 0.0
    for (x1, x2), y in pairs:
        s11 += x1 * x1
        s12 += x1 * x2
        s22 += x2 * x2
        s1y += x1 * y
        s2y += x2 * y
    det = s11 * s22 - s12 * s12
    if det == 0.0:
        return None
    a = (s22 * s1y - s12 * s2y) / det
    b = (s11 * s2y - s12 * s1y) / det
    return (a, b)


def fit_m2(rows, panel: PanelConfig) -> "Params | None":
    """Fit k and gamma from (gti, t_air, actual_w) rows. None if unfittable."""
    pairs = []
    for gti, t_air, actual in rows:
        base = gti / 1000.0 * panel.panel_wp
        x2 = base * (cell_temp(gti, t_air) - 25.0)
        pairs.append(((base, x2), actual))
    ab = fit_two_var(pairs)
    if ab is None or ab[0] == 0.0:
        return None
    a, b = ab
    return Params(k=a, gamma=b / a)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pvmodel.py -q`
Expected: PASS (7 tests total).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/pvmodel.py tests/test_pvmodel.py
git commit -m "feat(pvmodel): closed-form 2-var OLS fit for M2 (k, gamma)"
```

---

### Task 3: backtest — HourRow, bulk/clean-day selection, MAE/bias metric

**Files:**

- Create: `suncast/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**

- Consumes: nothing from earlier tasks yet (pure data helpers).
- Produces:
  - `HourRow(ts: str, day: str, gti: float, t_air: float, pv: float, cs: float)` (dataclass)
  - `is_bulk(cs: float) -> bool` — `2.5 <= cs < 3.5`
  - `bulk_rows(rows: list[HourRow]) -> list[HourRow]`
  - `clean_days(rows: list[HourRow], min_pv: float = 5.0, frac: float = 0.8) -> set[str]`
    — days where ≥`frac` of daylight (`pv > min_pv`) hours are bulk
  - `mae_bias(pairs: list[tuple[float, float]]) -> tuple[float, float]` — `(pred, actual)`
    → `(mae, bias)` where `bias = mean(pred - actual)`; `(0.0, 0.0)` if empty

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest.py
from suncast.backtest import HourRow, bulk_rows, clean_days, is_bulk, mae_bias


def _row(day, h, cs, pv=100.0):
    return HourRow(ts=f"{day}T{h:02d}:00", day=day, gti=500.0, t_air=20.0, pv=pv, cs=cs)


def test_is_bulk_range():
    assert is_bulk(3.0) and is_bulk(2.5)
    assert not is_bulk(2.4) and not is_bulk(3.5) and not is_bulk(4.0)


def test_bulk_rows_filters():
    rows = [_row("2026-06-01", 8, 3.0), _row("2026-06-01", 9, 4.0)]
    assert [r.ts for r in bulk_rows(rows)] == ["2026-06-01T08:00"]


def test_clean_days_threshold():
    # Day A: 4 daylight hours, 4 bulk -> clean. Day B: 4 daylight, 1 bulk -> not.
    a = [_row("A", h, 3.0) for h in range(4)]
    b = [_row("B", 0, 3.0)] + [_row("B", h, 4.0) for h in range(1, 4)]
    # Non-daylight (pv<=5) hours are ignored by the fraction.
    a.append(_row("A", 20, 5.0, pv=0.0))
    assert clean_days(a + b) == {"A"}


def test_mae_bias():
    # preds 110,90 vs actual 100,100 -> errors 10,10 mae=10; bias mean(10,-10)=0
    assert mae_bias([(110.0, 100.0), (90.0, 100.0)]) == (10.0, 0.0)
    assert mae_bias([]) == (0.0, 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: FAIL (`ModuleNotFoundError: suncast.backtest`).

- [ ] **Step 3: Write minimal implementation**

```python
# suncast/backtest.py
"""Offline harness: score PV potential models against Victron history."""

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class HourRow:
    ts: str
    day: str
    gti: float
    t_air: float
    pv: float
    cs: float


def is_bulk(cs: float) -> bool:
    """True for the unthrottled MPPT bulk stage."""
    return 2.5 <= cs < 3.5


def bulk_rows(rows: list[HourRow]) -> list[HourRow]:
    return [r for r in rows if is_bulk(r.cs)]


def clean_days(rows: list[HourRow], min_pv: float = 5.0, frac: float = 0.8) -> set[str]:
    """Days where >= frac of daylight (pv > min_pv) hours are bulk hours."""
    daylight: dict[str, int] = defaultdict(int)
    bulk: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.pv > min_pv:
            daylight[r.day] += 1
            if is_bulk(r.cs):
                bulk[r.day] += 1
    return {d for d, n in daylight.items() if n > 0 and bulk[d] / n >= frac}


def mae_bias(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """(pred, actual) pairs -> (mean absolute error, mean signed pred-actual)."""
    if not pairs:
        return (0.0, 0.0)
    n = len(pairs)
    mae = sum(abs(p - a) for p, a in pairs) / n
    bias = sum(p - a for p, a in pairs) / n
    return (mae, bias)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): HourRow, bulk/clean-day selection, MAE/bias metric"
```

---

### Task 4: backtest — flat_k, fixed scoring, leave-one-day-out scoring

**Files:**

- Modify: `suncast/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**

- Consumes: `HourRow`, `bulk_rows`, `mae_bias` (Task 3); `Params`, `expected_w`,
  `fit_m2`, `cell_temp` (Tasks 1-2); `PanelConfig`.
- Produces:
  - `flat_k(rows: list[HourRow], panel: PanelConfig) -> float` — median of daily
    `sum(pv)/sum(base)` over bulk hours (`base = gti/1000*Wp`); `1.0` if none
  - `score_fixed(rows: list[HourRow], panel: PanelConfig, params: Params) -> tuple[float, float]`
    — `mae_bias` over bulk hours using `expected_w`
  - `score_lodo(rows: list[HourRow], panel: PanelConfig) -> tuple[float, float]`
    — leave-one-day-out: fit M2 on other days' bulk rows, predict held-out day's
    bulk rows, aggregate `mae_bias`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_backtest.py
from suncast.backtest import flat_k, score_fixed, score_lodo
from suncast.models import PanelConfig
from suncast.pvmodel import Params, expected_w

PANEL = PanelConfig()


def _bulk_row(day, h, gti, t_air, pv):
    return HourRow(ts=f"{day}T{h:02d}:00", day=day, gti=gti, t_air=t_air, pv=pv, cs=3.0)


def test_flat_k_is_median_daily_ratio():
    # Day1: base = 260*0.5 = 130; pv 65 -> ratio 0.5.
    # Day2: base = 130; pv 39 -> ratio 0.3. Median of {0.5,0.3} = 0.4.
    rows = [_bulk_row("D1", 8, 500.0, 20.0, 65.0), _bulk_row("D2", 8, 500.0, 20.0, 39.0)]
    assert abs(flat_k(rows, PANEL) - 0.4) < 1e-9


def test_score_fixed_zero_error_on_self_consistent_data():
    p = Params(k=0.5, gamma=-0.004)
    rows = [_bulk_row("D1", h, 300.0, 20.0, expected_w(300.0, 20.0, PANEL, p)) for h in range(3)]
    mae, bias = score_fixed(rows, PANEL, p)
    assert mae < 1e-9 and abs(bias) < 1e-9


def test_score_lodo_predicts_held_out_day():
    # Data generated by a true M2; LODO fit on other days should predict the
    # held-out day near-perfectly since all days share the same law.
    true = Params(k=0.5, gamma=-0.004)
    rows = []
    for day, t_air in (("D1", 5.0), ("D2", 20.0), ("D3", 35.0)):
        for gti in (200.0, 400.0, 600.0):
            rows.append(_bulk_row(day, int(gti // 100), gti, t_air, expected_w(gti, t_air, PANEL, true)))
    mae, _bias = score_lodo(rows, PANEL)
    assert mae < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: FAIL (`ImportError: cannot import name 'flat_k'`).

- [ ] **Step 3: Write minimal implementation**

```python
# append to suncast/backtest.py
from statistics import median

from suncast.models import PanelConfig
from suncast.pvmodel import Params, expected_w, fit_m2


def flat_k(rows: list[HourRow], panel: PanelConfig) -> float:
    """Median daily bulk-hour ratio sum(pv)/sum(base); 1.0 if no data."""
    act: dict[str, float] = defaultdict(float)
    base: dict[str, float] = defaultdict(float)
    for r in bulk_rows(rows):
        act[r.day] += r.pv
        base[r.day] += r.gti / 1000.0 * panel.panel_wp
    ratios = [act[d] / base[d] for d in base if base[d] > 0]
    return median(ratios) if ratios else 1.0


def score_fixed(rows: list[HourRow], panel: PanelConfig, params: Params) -> tuple[float, float]:
    pairs = [(expected_w(r.gti, r.t_air, panel, params), r.pv) for r in bulk_rows(rows)]
    return mae_bias(pairs)


def score_lodo(rows: list[HourRow], panel: PanelConfig) -> tuple[float, float]:
    """Leave-one-day-out MAE/bias for the fitted M2 model."""
    bulk = bulk_rows(rows)
    days = sorted({r.day for r in bulk})
    pairs: list[tuple[float, float]] = []
    for held in days:
        train = [(r.gti, r.t_air, r.pv) for r in bulk if r.day != held]
        params = fit_m2(train, panel)
        if params is None:
            continue
        for r in bulk:
            if r.day == held:
                pairs.append((expected_w(r.gti, r.t_air, panel, params), r.pv))
    return mae_bias(pairs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: PASS (7 tests total in this file).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): flat_k, fixed scoring, leave-one-day-out scoring"
```

---

### Task 5: backtest — ERA5 archive (gti + temperature) and row assembly

**Files:**

- Modify: `suncast/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**

- Consumes: `HourRow` (Task 3); `QueryFn`, `flux_hourly_field` (`suncast/influx.py`);
  `pv_first_day`, `day_location` (`suncast/backfill.py`); `Config`.
- Produces:
  - `archive_url_temp(lat: float, lon: float, day: str) -> str` — archive URL with
    `hourly=global_tilted_irradiance,temperature_2m&timezone=UTC`
  - `parse_archive(status: int, body: bytes) -> dict[str, tuple[float, float]]` —
    `{iso_hour_utc: (gti, t_air)}`; raises `ValueError` on non-200 or bad JSON
  - `assemble(cfg: Config, query: QueryFn, fetch, home: tuple[float, float],
    end_day: date, days_back: int = 60) -> list[HourRow]`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_backtest.py
from suncast.backtest import archive_url_temp, parse_archive


def test_archive_url_has_both_hourly_vars():
    u = archive_url_temp(48.83, 8.28, "2026-06-01")
    assert "archive-api.open-meteo.com" in u
    assert "global_tilted_irradiance" in u and "temperature_2m" in u
    assert "start_date=2026-06-01" in u and "end_date=2026-06-01" in u
    assert "timezone=UTC" in u


def test_parse_archive_maps_hour_to_gti_and_temp():
    body = (
        b'{"hourly": {"time": ["2026-06-01T11:00", "2026-06-01T12:00"],'
        b' "global_tilted_irradiance": [500.0, 800.0],'
        b' "temperature_2m": [18.0, 24.0]}}'
    )
    out = parse_archive(200, body)
    assert out["2026-06-01T11:00:00+00:00"] == (500.0, 18.0)
    assert out["2026-06-01T12:00:00+00:00"] == (800.0, 24.0)


def test_parse_archive_errors():
    import pytest

    with pytest.raises(ValueError):
        parse_archive(500, b"")
    with pytest.raises(ValueError):
        parse_archive(200, b"not json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: FAIL (`ImportError: cannot import name 'archive_url_temp'`).

- [ ] **Step 3: Write minimal implementation**

```python
# append to suncast/backtest.py
import json
from datetime import UTC, date, datetime, timedelta

from suncast.backfill import day_location, pv_first_day
from suncast.config import Config
from suncast.influx import QueryFn, flux_hourly_field

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def archive_url_temp(lat: float, lon: float, day: str) -> str:
    return (
        f"{ARCHIVE_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={day}&end_date={day}"
        f"&hourly=global_tilted_irradiance,temperature_2m&timezone=UTC"
    )


def parse_archive(status: int, body: bytes) -> dict[str, tuple[float, float]]:
    if status != 200:
        raise ValueError(f"HTTP {status}")
    try:
        data = json.loads(body)
        times = data["hourly"]["time"]
        gtis = data["hourly"]["global_tilted_irradiance"]
        temps = data["hourly"]["temperature_2m"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"bad archive body: {e}") from e
    out: dict[str, tuple[float, float]] = {}
    for t, g, temp in zip(times, gtis, temps, strict=False):
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        out[dt.isoformat()] = (float(g or 0.0), float(temp or 0.0))
    return out


def _hourly(query: QueryFn, cfg: Config, day: str, field: str) -> dict[str, float]:
    rows = query(flux_hourly_field(cfg, day, field))
    return {t.astimezone(UTC).isoformat(): v for t, v in rows if t is not None}


def assemble(
    cfg: Config,
    query: QueryFn,
    fetch,
    home: tuple[float, float],
    end_day: date,
    days_back: int = 60,
) -> list[HourRow]:
    """Hourly ERA5 (gti, temp) + Victron (pv, cs) rows over the pv history."""
    start = pv_first_day(query, cfg)
    if start is None:
        return []
    rows: list[HourRow] = []
    d = start
    while d <= end_day:
        day = d.isoformat()
        d += timedelta(days=1)
        try:
            lat, lon = day_location(query, cfg, day, home)
            status, body = fetch(archive_url_temp(lat, lon, day))
            era = parse_archive(status, body)
            pv = _hourly(query, cfg, day, cfg.pv_power_field)
            cs = _hourly(query, cfg, day, cfg.charge_state_field)
        except Exception:  # noqa: BLE001 - one bad day must not abort assembly
            continue
        for hour, (gti, t_air) in era.items():
            if hour in pv and hour in cs:
                rows.append(HourRow(hour, day, gti, t_air, pv[hour], cs[hour]))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: PASS (10 tests total in this file).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): ERA5 archive (gti+temp) fetch and hourly row assembly"
```

---

### Task 6: backtest — run, render_table, main, console script, README

**Files:**

- Modify: `suncast/backtest.py`, `pyproject.toml`, `README.md`
- Test: `tests/test_backtest.py`

**Interfaces:**

- Consumes: everything above; `make_query_fn` (`suncast/influx.py`),
  `default_fetch` (`suncast/providers/open_meteo.py`), `Store` (`suncast/store.py`),
  `load` (`suncast/config.py`).
- Produces:
  - `run(rows: list[HourRow], panel: PanelConfig) -> list[dict]` — one dict per
    model: `{"name", "mae_bulk", "bias", "mae_cleanday", "vs_m0", "k", "gamma"}`
  - `render_table(scores: list[dict]) -> str`
  - `main() -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_backtest.py
from suncast.backtest import render_table, run


def test_run_reports_three_models_with_m0_baseline():
    true = Params(k=0.5, gamma=-0.004)
    rows = []
    for day, t_air in (("D1", 5.0), ("D2", 20.0), ("D3", 35.0)):
        for gti in (200.0, 400.0, 600.0):
            rows.append(HourRow(f"{day}T{int(gti//100):02d}:00", day, gti, t_air,
                                expected_w(gti, t_air, PANEL, true), 3.0))
    scores = run(rows, PANEL)
    names = [s["name"] for s in scores]
    assert names[0].startswith("M0") and any(n.startswith("M2") for n in names)
    m0 = next(s for s in scores if s["name"].startswith("M0"))
    assert m0["vs_m0"] == 0.0  # baseline compares to itself
    # M2 fits the true law -> its out-of-sample MAE beats flat M0.
    m2 = next(s for s in scores if s["name"].startswith("M2"))
    assert m2["mae_bulk"] <= m0["mae_bulk"]


def test_render_table_contains_headers_and_rows():
    scores = [{"name": "M0 flat", "mae_bulk": 12.3, "bias": -1.0, "mae_cleanday": 40.0,
               "vs_m0": 0.0, "k": 0.47, "gamma": 0.0}]
    txt = render_table(scores)
    assert "MAE" in txt and "M0 flat" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: FAIL (`ImportError: cannot import name 'run'`).

- [ ] **Step 3: Write minimal implementation**

```python
# append to suncast/backtest.py
import os

from suncast.providers.open_meteo import default_fetch


def _cleanday_mae(rows, panel, params) -> float:
    clean = clean_days(rows)
    by_day_pred: dict[str, float] = defaultdict(float)
    by_day_act: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.day in clean:
            by_day_pred[r.day] += expected_w(r.gti, r.t_air, panel, params)
            by_day_act[r.day] += r.pv
    pairs = [(by_day_pred[d], by_day_act[d]) for d in clean]
    return mae_bias(pairs)[0]


def run(rows: list[HourRow], panel: PanelConfig) -> list[dict]:
    """Score M0/M1/M2 and return one summary dict per model (M0 first)."""
    k = flat_k(rows, panel)
    m0 = Params(k=k, gamma=0.0)
    m1 = Params(k=k, gamma=-0.004)
    fit = fit_m2([(r.gti, r.t_air, r.pv) for r in bulk_rows(rows)], panel)

    scores: list[dict] = []

    def add(name, mae, bias, cleanday, kk, gg):
        scores.append({"name": name, "mae_bulk": mae, "bias": bias,
                       "mae_cleanday": cleanday, "vs_m0": 0.0, "k": kk, "gamma": gg})

    mae0, bias0 = score_fixed(rows, panel, m0)
    add("M0 flat", mae0, bias0, _cleanday_mae(rows, panel, m0), k, 0.0)
    mae1, bias1 = score_fixed(rows, panel, m1)
    add("M1 temp(fixed)", mae1, bias1, _cleanday_mae(rows, panel, m1), k, -0.004)
    if fit is not None:
        mae2, bias2 = score_lodo(rows, panel)
        add("M2 temp(fitted,LODO)", mae2, bias2, _cleanday_mae(rows, panel, fit),
            fit.k, fit.gamma)

    for s in scores:
        s["vs_m0"] = 0.0 if mae0 == 0 else (s["mae_bulk"] - mae0) / mae0 * 100.0
    return scores


def render_table(scores: list[dict]) -> str:
    head = f"{'model':22} {'MAE(bulk,W)':>12} {'bias(W)':>9} {'MAE(clean,Wh)':>14} {'vs M0':>7}"
    lines = [head, "-" * len(head)]
    for s in scores:
        lines.append(
            f"{s['name']:22} {s['mae_bulk']:12.1f} {s['bias']:9.1f} "
            f"{s['mae_cleanday']:14.0f} {s['vs_m0']:6.0f}%"
        )
    lines.append("")
    for s in scores:
        lines.append(f"  {s['name']}: k={s['k']:.3f} gamma={s['gamma']:.4f}")
    return "\n".join(lines)


def main() -> None:
    from suncast.config import load
    from suncast.influx import make_query_fn
    from suncast.store import Store

    cfg = load(os.environ)
    query = make_query_fn(cfg)
    home = (float(os.environ.get("HOME_LAT", "48.77")), float(os.environ.get("HOME_LON", "9.16")))
    panel = Store(cfg.db_path).get_panel()
    end_day = datetime.now(UTC).date() - timedelta(days=1)
    rows = assemble(cfg, query, default_fetch, home, end_day)
    scores = run(rows, panel)
    table = render_table(scores)
    print(f"assembled {len(rows)} hourly rows, {len(clean_days(rows))} clean days\n")
    print(table)

    out = "docs/superpowers/results/2026-07-05-backtest.md"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(f"# Backtest results ({end_day})\n\n```text\n{table}\n```\n")
    print(f"\nwrote {out}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest.py -q`
Expected: PASS (12 tests total in this file).

- [ ] **Step 5: Add console script and README, then commit**

Add to `pyproject.toml` under `[project.scripts]` (after the `suncast-backfill` line):

```toml
suncast-backtest = "suncast.backtest:main"
```

Add to `README.md` under the Development section:

```markdown
### Backtest (offline model evaluation)

`suncast-backtest` scores candidate potential-prediction models (flat factor vs
temperature-derate) against Victron history using ERA5 reanalysis, and writes a
results table to `docs/superpowers/results/`. Run it on the Pi with the service
env sourced (`HOME_LAT`/`HOME_LON` set the pre-location-history fallback).
```

Commit:

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format . && .venv/bin/pytest -q
git add suncast/backtest.py tests/test_backtest.py pyproject.toml README.md
git commit -m "feat(backtest): run/render/main + suncast-backtest console script"
```

---

## Verification

- Full pytest suite green (expect the current 77 + ~13 new = ~90); `ruff check`
  and `ruff format --check` clean; markdownlint clean.
- CI green on GitHub after push (existing workflow).
- Live run on the Pi (service env sourced): `suncast-backtest` prints the table
  and writes the results doc; it reads InfluxDB only and writes no InfluxDB/SQLite
  data. The table gives the out-of-sample verdict on M0 vs M1 vs M2.

## Execution

Subagent-driven (fresh implementer per task + review), same process that shipped
suncast v0.1–v0.2.
