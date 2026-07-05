# suncast prediction backtest harness — design

**Status:** approved (2026-07-05)

**Goal:** Build an offline analysis tool that quantifies how much candidate PV
prediction models beat the current flat calibration factor, measured
out-of-sample against real Victron history, so we decide with numbers before
changing the live predictor.

## Background

The live algorithm corrects Open-Meteo's raw irradiance→watts conversion with a
single median ratio (currently 0.473, from 33 bulk-hour days). The ERA5 +
Victron data now available shows this flat factor hides real structure: daily
bulk-hour ratios correlate **negatively** with irradiance —

| bright days (ERA5 > 1000 Wh) | ratio | dim days (ERA5 < 700 Wh) | ratio |
|---|---|---|---|
| 06-14 (1640 Wh) | 0.31 | 06-01 (580 Wh) | 0.53 |
| 06-16 (1096 Wh) | 0.32 | 06-15 (550 Wh) | 0.51 |
| 06-25 (1944 Wh) | 0.36 | 06-05 (586 Wh) | 0.44 |

A flat scalar over-predicts on bright days and under-predicts on dim ones. The
leading physical suspect is **panel temperature** (bright = clear = hot =
efficiency derate), and Open-Meteo returns `temperature_2m` for free in both the
forecast and the ERA5 archive — currently discarded. This harness measures
whether adding temperature (or a fitted variant) actually reduces prediction
error, before any change ships.

## Scope

**In scope:** an offline CLI (`suncast-backtest`) that assembles ERA5 + Victron
history, scores candidate potential-prediction models against truth, and prints
a comparison table plus a results document.

**Out of scope:** changing the live predictor/provider (a separate follow-up
once a winner is proven); predicting *delivered* energy (needs battery SOC +
load model); sun-geometry models (only pursued if temperature fails to explain
the residual); any change to the live calibration or Grafana.

## What we predict and score against

We predict **panel potential** (what the array could produce), scored against
Victron `pv_power` — which throttling corrupts, so scoring is restricted:

- **Bulk hour** — an hour whose mean `charge_state` is in `[2.5, 3.5)`
  (unthrottled MPPT bulk stage; matches the live calibration's definition).
- **Clean day** — a day where, among daylight hours with `pv_power > 5 W`, at
  least 80% are bulk hours (the battery essentially never filled, so the full
  day is unthrottled truth).

**Primary metric:** per-hour MAE and bias over all bulk hours (maximum data).
**Secondary metric:** per-day MAE (Wh) over clean days (unbiased full-day check,
guards against the morning-shoulder sampling bias of the primary metric).

A model is trusted only if it improves on **both**.

## Data assembly

Per UTC hour over the available history (earliest `pv_power` day → yesterday):

- `gti` — ERA5 `global_tilted_irradiance` (tilt 0, az 0) at the van's daily
  location (geo-mean, or `HOME_LAT/HOME_LON` before location history), re-fetched
  from `archive-api.open-meteo.com` with `hourly=global_tilted_irradiance,temperature_2m`.
- `t_air` — ERA5 `temperature_2m` (°C), same call.
- `pv` — Victron hourly mean `pv_power` (W), `timeSrc:"_start"` so hour keys align.
- `cs` — Victron hourly mean `charge_state`, for bulk / clean-day selection.

Hours are joined on their ISO hour key; hours missing any of `gti`, `pv`, `cs`
are dropped.

## Candidate models

Each is a pure function `expected_w(gti, t_air, panel, params) -> float`, capped
at `panel.charger_limit_w`. `base = gti / 1000 * panel.panel_wp`.

- **M0 — flat factor.** `expected = base * k`. `k` = median of daily bulk-hour
  ratios (the current algorithm; `k ≈ 0.47`).
- **M1 — temperature, fixed coefficients.** `expected = base * k * (1 + γ·(T_cell − 25))`,
  with cell temperature `T_cell = t_air + gti * (NOCT − 20) / 800`, `NOCT = 45 °C`
  (so `T_cell = t_air + gti·0.03125`), and literature `γ = −0.004 /°C`. `k` from M0.
- **M2 — temperature, fitted.** Same functional form, but `k` and `γ` fitted to
  the data. The form is linear in two derived features:
  `expected = a·x1 + b·x2` where `x1 = base`, `x2 = base·(T_cell − 25)`; recover
  `k = a`, `γ = b / a`. Fit by ordinary least squares on the 2×2 normal
  equations (pure stdlib — no numpy/scipy).

## Validation

- **Fixed-parameter models (M0, M1)** are scored directly on all bulk hours /
  clean days.
- **Fitted models (M2)** are scored **leave-one-day-out**: for each day `d`, fit
  `(a, b)` on the bulk-hour pairs of all *other* days, predict day `d`'s bulk
  hours, and accumulate the held-out errors. This reports out-of-sample MAE, so
  a fitted model cannot win by memorizing its own training data.

## Output

Printed table and a written `docs/superpowers/results/2026-07-05-backtest.md`:

```text
model               MAE(bulk,W)   bias(W)   MAE(clean-day,Wh)   vs M0
M0 flat 0.47           …            …             …              —
M1 temp (fixed)        …            …             …            −XX%
M2 temp (fitted)       …            …             …            −XX%
```

Plus the fitted `k` and `γ` from M2, and the clean-day count, so we can judge
whether the fitted temperature coefficient is physically plausible
(≈ −0.003…−0.005 /°C for crystalline; flexible panels may differ).

## Architecture

- `suncast/pvmodel.py` — pure candidate model functions + a `Params` dataclass,
  and the closed-form 2-variable OLS fit. No IO.
- `suncast/backtest.py` — data assembly (via the existing `QueryFn` and the
  Open-Meteo archive fetch), bulk/clean-day selection, scoring, leave-one-day-out
  driver, table + results-doc rendering, and a `main()`.
- `pyproject.toml` — console script `suncast-backtest = "suncast.backtest:main"`.

The winning model's pure function lives in `pvmodel.py` ready to be wired into
the provider later, without the harness depending on the live service.

## Testing

- `pvmodel`: each model function on known inputs (e.g. M1 at `T_cell = 25` equals
  M0; a hot hour derates below M0); the OLS fit recovers known `(k, γ)` from
  synthetic linear data; cap is applied.
- `backtest`: bulk-hour and clean-day selection on synthetic `charge_state`;
  the MAE/bias metric on toy pairs; the leave-one-day-out driver excludes the
  held-out day (verified by a fit that would differ in-sample vs out).
- Data assembly is thin IO over the existing tested `QueryFn`.

## Success criteria

The tool runs on the Pi against real history and prints the table. It gives a
clear, out-of-sample verdict: whether M1 or M2 reduces MAE versus M0 on **both**
the bulk-hour and clean-day metrics, and by how much — the go/no-go evidence for
a follow-up that wires the winner into the live predictor.
