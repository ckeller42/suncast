"""Offline harness: score PV potential models against Victron history."""

from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from suncast.models import PanelConfig
from suncast.pvmodel import Params, expected_w, fit_m2


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
