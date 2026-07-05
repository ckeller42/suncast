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


def fit_two_var(pairs) -> "tuple[float, float] | None":
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
