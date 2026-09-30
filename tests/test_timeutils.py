from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from carbon_charge.timeutils import (
    UTC,
    add_settlement_columns,
    cutoff_utc,
    period_start_utc,
    period_start_utc_series,
    periods_in_day,
    settlement_date_period,
)

SPRING = date(2024, 3, 31)  # clocks go forward: 46 periods
AUTUMN = date(2024, 10, 27)  # clocks go back: 50 periods
NORMAL_GMT = date(2024, 1, 15)
NORMAL_BST = date(2024, 7, 15)


@pytest.mark.parametrize(
    "day,expected", [(SPRING, 46), (AUTUMN, 50), (NORMAL_GMT, 48), (NORMAL_BST, 48)]
)
def test_periods_in_day(day, expected):
    assert periods_in_day(day) == expected


def test_period_start_gmt_and_bst():
    assert period_start_utc(NORMAL_GMT, 1) == datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    # In BST the settlement day starts at 23:00 UTC the evening before.
    assert period_start_utc(NORMAL_BST, 1) == datetime(2024, 7, 14, 23, 0, tzinfo=UTC)
    assert period_start_utc(NORMAL_BST, 48) == datetime(2024, 7, 15, 22, 30, tzinfo=UTC)


def test_spring_day_boundaries():
    assert period_start_utc(SPRING, 1) == datetime(2024, 3, 31, 0, 0, tzinfo=UTC)
    assert period_start_utc(SPRING, 46) == datetime(2024, 3, 31, 22, 30, tzinfo=UTC)
    with pytest.raises(ValueError):
        period_start_utc(SPRING, 47)


def test_autumn_day_boundaries():
    assert period_start_utc(AUTUMN, 1) == datetime(2024, 10, 26, 23, 0, tzinfo=UTC)
    assert period_start_utc(AUTUMN, 50) == datetime(2024, 10, 27, 23, 30, tzinfo=UTC)
    with pytest.raises(ValueError):
        period_start_utc(AUTUMN, 51)


def test_autumn_repeated_hour_gets_distinct_periods():
    # 01:00-02:00 local happens twice; the two passes are periods 3-4 and 5-6.
    assert settlement_date_period(datetime(2024, 10, 27, 0, 0, tzinfo=UTC)) == (AUTUMN, 3)
    assert settlement_date_period(datetime(2024, 10, 27, 1, 0, tzinfo=UTC)) == (AUTUMN, 5)


@pytest.mark.parametrize("day", [SPRING, AUTUMN, NORMAL_GMT, NORMAL_BST])
def test_round_trip_every_period(day):
    n = periods_in_day(day)
    starts = [period_start_utc(day, p) for p in range(1, n + 1)]
    assert [settlement_date_period(ts) for ts in starts] == [(day, p) for p in range(1, n + 1)]
    assert all(b - a == timedelta(minutes=30) for a, b in zip(starts, starts[1:]))


def test_days_are_contiguous_across_clock_changes():
    for day in (SPRING, AUTUMN):
        last = period_start_utc(day, periods_in_day(day))
        assert last + timedelta(minutes=30) == period_start_utc(day + timedelta(days=1), 1)


def test_vectorised_matches_scalar():
    ts = pd.date_range("2024-03-30 12:00", "2024-04-01 12:00", freq="30min").append(
        pd.date_range("2024-10-26 12:00", "2024-10-28 12:00", freq="30min")
    )
    out = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    expected = [settlement_date_period(t.to_pydatetime()) for t in ts]
    assert list(zip(out["settlement_date"], out["settlement_period"])) == expected

    back = period_start_utc_series(out["settlement_date"], out["settlement_period"])
    assert list(back) == list(ts)


def test_vectorised_rejects_out_of_range_periods():
    dates = pd.Series([SPRING, SPRING, AUTUMN, AUTUMN, NORMAL_GMT, NORMAL_GMT])
    periods = pd.Series([46, 47, 50, 51, 0, 49])
    assert list(period_start_utc_series(dates, periods).isna()) == [False, True, False, True, True, True]


def test_cutoff_is_11_uk_local_on_day_before():
    assert cutoff_utc(NORMAL_GMT) == datetime(2024, 1, 14, 11, 0, tzinfo=UTC)
    assert cutoff_utc(NORMAL_BST) == datetime(2024, 7, 14, 10, 0, tzinfo=UTC)
    # Forecasting the spring clock-change day: D-1 is still GMT.
    assert cutoff_utc(SPRING) == datetime(2024, 3, 30, 11, 0, tzinfo=UTC)
    # Forecasting the day after it: D-1 is the clock-change day itself, now BST.
    assert cutoff_utc(SPRING + timedelta(days=1)) == datetime(2024, 3, 31, 10, 0, tzinfo=UTC)
