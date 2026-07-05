"""Offline harness: score PV potential models against Victron history."""

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median

from suncast.backfill import day_location, pv_first_day
from suncast.config import Config
from suncast.influx import QueryFn, flux_hourly_field
from suncast.models import PanelConfig
from suncast.providers.open_meteo import default_fetch
from suncast.pvmodel import Params, expected_w, fit_m2

logger = logging.getLogger(__name__)


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
    """MAE/bias over bulk hours using expected_w."""
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


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def archive_url_temp(lat: float, lon: float, day: str) -> str:
    return (
        f"{ARCHIVE_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={day}&end_date={day}"
        f"&hourly=global_tilted_irradiance,temperature_2m"
        f"&tilt=0&azimuth=0&timezone=UTC"
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
            logger.warning("backtest assemble skipped %s", day, exc_info=True)
            continue
        for hour, (gti, t_air) in era.items():
            if hour in pv and hour in cs:
                rows.append(HourRow(hour, day, gti, t_air, pv[hour], cs[hour]))
    return rows


def _model_params(name: str, train: list[HourRow], panel: PanelConfig) -> "Params | None":
    """Fit a model's params on the training rows. None if unfittable (M2 singular)."""
    if name == "M0":
        return Params(k=flat_k(train, panel), gamma=0.0)
    if name == "M1":
        return Params(k=flat_k(train, panel), gamma=-0.004)
    return fit_m2([(r.gti, r.t_air, r.pv) for r in bulk_rows(train)], panel)


def score_model_lodo(
    name: str, rows: list[HourRow], panel: PanelConfig
) -> tuple[float, float, float]:
    """Leave-one-day-out (mae_bulk, bias, mae_cleanday) for one model.

    EVERY model — flat or fitted — is refit on the other days and scored on the
    held-out day, so all three are compared strictly out-of-sample (the flat k
    for M0/M1 barely moves, but this keeps the comparison honest and puts the
    clean-day safeguard out-of-sample too).
    """
    bulk = bulk_rows(rows)
    days = sorted({r.day for r in bulk})
    clean = clean_days(rows)
    bulk_pairs: list[tuple[float, float]] = []
    cd_pairs: list[tuple[float, float]] = []
    for held in days:
        train = [r for r in rows if r.day != held]
        params = _model_params(name, train, panel)
        if params is None:
            continue
        for r in bulk:
            if r.day == held:
                bulk_pairs.append((expected_w(r.gti, r.t_air, panel, params), r.pv))
        if held in clean:
            pred = sum(expected_w(r.gti, r.t_air, panel, params) for r in rows if r.day == held)
            act = sum(r.pv for r in rows if r.day == held)
            cd_pairs.append((pred, act))
    mae_bulk, bias = mae_bias(bulk_pairs)
    mae_cd, _ = mae_bias(cd_pairs)
    return mae_bulk, bias, mae_cd


def run(rows: list[HourRow], panel: PanelConfig) -> list[dict]:
    """Score M0/M1/M2 strictly leave-one-day-out (M0 first). vs_m0 = % MAE change."""
    # Full-data params only for the DISPLAYED k/gamma (what we would deploy); the
    # scores are out-of-sample via score_model_lodo.
    k = flat_k(rows, panel)
    full_fit = fit_m2([(r.gti, r.t_air, r.pv) for r in bulk_rows(rows)], panel)

    specs = [("M0 flat", k, 0.0), ("M1 temp(fixed)", k, -0.004)]
    if full_fit is not None:
        specs.append(("M2 temp(fitted)", full_fit.k, full_fit.gamma))

    scores: list[dict] = []
    for name, kk, gg in specs:
        mae, bias, cd = score_model_lodo(name.split()[0], rows, panel)
        scores.append(
            {
                "name": name,
                "mae_bulk": mae,
                "bias": bias,
                "mae_cleanday": cd,
                "vs_m0": 0.0,
                "k": kk,
                "gamma": gg,
            }
        )

    mae0 = scores[0]["mae_bulk"]
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
    lines.append("")
    lines.append("all metrics are leave-one-day-out (out-of-sample); vs M0 negative = better")
    return "\n".join(lines)


def _read_panel(db_path: str) -> PanelConfig:
    """Read the stored panel config read-only (never creates a row)."""
    import json
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute("SELECT json FROM panel WHERE id = 1").fetchone()
        conn.close()
        if row:
            return PanelConfig(**json.loads(row[0]))
    except Exception:
        logger.warning("no stored panel; using defaults", exc_info=True)
    return PanelConfig()


def main() -> None:
    from suncast.config import load
    from suncast.influx import make_query_fn

    cfg = load(os.environ)
    query = make_query_fn(cfg)
    home = (float(os.environ.get("HOME_LAT", "48.77")), float(os.environ.get("HOME_LON", "9.16")))
    panel = _read_panel(cfg.db_path)
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
