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
