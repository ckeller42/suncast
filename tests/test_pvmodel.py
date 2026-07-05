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
