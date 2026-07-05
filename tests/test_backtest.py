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
