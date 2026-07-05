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
