from dataclasses import replace
from datetime import date, time

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import linprog

from carbon_charge.charging import strategies as st
from carbon_charge.charging import value
from carbon_charge.charging.scenarios import DEFAULT, sensitivity_set
from carbon_charge.timeutils import UK, UTC

NORMAL = date(2024, 1, 15)
SPRING_NIGHT = date(2024, 3, 30)  # night that crosses the 46-period day
AUTUMN_NIGHT = date(2024, 10, 26)  # night that crosses the 50-period day
NIGHTS = [NORMAL, SPRING_NIGHT, AUTUMN_NIGHT]


def _actual(nights, seed=0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(sorted({t for n in nights for t in st.window_slots(n, replace(DEFAULT, plug_in=time(17)))}))
    return pd.Series(rng.uniform(20, 300, len(idx)), index=idx)


def _plans(night, sc, actual, forecast):
    return {
        "arrival": st.charge_on_arrival(night, sc),
        "timer": st.overnight_timer(night, sc),
        "forecast": st.optimised(night, sc, forecast),
        "perfect": st.optimised(night, sc, actual),
    }


@pytest.mark.parametrize("sc", sensitivity_set(), ids=lambda s: s.name)
@pytest.mark.parametrize("night", NIGHTS)
def test_every_strategy_delivers_exact_energy_within_power_and_window(sc, night):
    actual = _actual(NIGHTS)
    forecast = actual + np.random.default_rng(1).normal(0, 30, len(actual))
    slots = st.window_slots(night, sc)
    start = pd.Timestamp(pd.Timestamp.combine(night, sc.plug_in).tz_localize(UK).astimezone(UTC).tz_localize(None))
    end = pd.Timestamp(pd.Timestamp.combine(night + pd.Timedelta(days=1), sc.deadline).tz_localize(UK).astimezone(UTC).tz_localize(None))
    for name, plan in _plans(night, sc, actual, forecast).items():
        assert plan.sum() == pytest.approx(sc.energy_kwh), name  # energy delivered
        assert (plan >= 0).all()
        assert (plan / st.SLOT_HOURS <= sc.power_kw + 1e-9).all(), name  # power limit (kW)
        assert plan.index.isin(slots).all(), name
        assert plan.index.min() >= start and plan.index.max() + pd.Timedelta("30min") <= end, name  # ready on time


def test_window_length_on_clock_change_nights():
    sc = DEFAULT  # 18:00 -> 07:00 is 13 h normally
    assert len(st.window_slots(NORMAL, sc)) == 26
    assert len(st.window_slots(SPRING_NIGHT, sc)) == 24  # clocks forward: an hour lost
    assert len(st.window_slots(AUTUMN_NIGHT, sc)) == 28  # clocks back: an hour gained


def test_arrival_starts_at_plug_in_and_timer_not_before_midnight():
    arr = st.charge_on_arrival(NORMAL, DEFAULT)
    assert arr.index[0] == st.window_slots(NORMAL, DEFAULT)[0]
    assert list(arr.round(3)) == [3.5, 3.5, 1.0]  # 8 kWh at 7 kW in half-hour slots
    timer = st.overnight_timer(NORMAL, DEFAULT)
    assert timer.index[0] == st.timer_start(NORMAL) == pd.Timestamp("2024-01-16 00:00")
    # Across the spring change local midnight is still GMT; across autumn it is BST (23:00 UTC).
    assert st.timer_start(SPRING_NIGHT) == pd.Timestamp("2024-03-31 00:00")
    assert st.timer_start(AUTUMN_NIGHT) == pd.Timestamp("2024-10-26 23:00")


def test_forecast_optimised_with_perfect_forecast_equals_perfect_foresight():
    actual = _actual(NIGHTS)
    for night in NIGHTS:
        a = st.emissions_g(st.optimised(night, DEFAULT, actual), actual)
        b = st.emissions_g(st.optimised(night, DEFAULT, actual.copy()), actual)
        assert a == b


def test_perfect_foresight_never_worse_than_any_other_strategy():
    for seed in range(20):
        actual = _actual(NIGHTS, seed)
        forecast = actual + np.random.default_rng(seed).normal(0, 60, len(actual))
        for night in NIGHTS:
            for sc in sensitivity_set():
                g = {k: st.emissions_g(p, actual) for k, p in _plans(night, sc, actual, forecast).items()}
                assert g["perfect"] <= min(g.values()) + 1e-6


def test_greedy_matches_linear_programme():
    actual = _actual(NIGHTS, 3)
    for night in NIGHTS:
        for sc in sensitivity_set():
            slots = st.window_slots(night, sc)
            cost = actual.reindex(slots).to_numpy()
            res = linprog(cost, A_eq=[np.ones(len(slots))], b_eq=[sc.energy_kwh],
                          bounds=[(0, sc.power_kw * st.SLOT_HOURS)] * len(slots))
            assert st.emissions_g(st.optimised(night, sc, actual), actual) == pytest.approx(res.fun)


def test_infeasible_energy_raises():
    with pytest.raises(st.Infeasible):
        st.charge_on_arrival(NORMAL, replace(DEFAULT, energy_kwh=200.0))
    with pytest.raises(st.Infeasible):  # 00:00-07:00 is 14 slots = 49 kWh at 7 kW
        st.overnight_timer(NORMAL, replace(DEFAULT, energy_kwh=60.0))


def test_missing_forecast_in_window_is_rejected():
    actual = _actual(NIGHTS)
    bad = actual.copy()
    bad.iloc[:] = np.nan
    with pytest.raises(ValueError):
        st.optimised(NORMAL, DEFAULT, bad)


def test_gap_captured_bounds():
    timer, perfect = np.array([100.0, 200.0]), np.array([60.0, 120.0])
    assert value.gap_captured(timer, perfect, perfect) == 1.0
    assert value.gap_captured(timer, timer, perfect) == 0.0
    assert value.gap_captured(timer, np.array([80.0, 160.0]), perfect) == pytest.approx(0.5)


def test_simulate_uses_same_nights_and_orders_strategies():
    nights = [NORMAL, date(2024, 1, 16), date(2024, 1, 17)]
    rng = np.random.default_rng(0)
    idx = pd.DatetimeIndex(sorted({t for n in nights for t in st.window_slots(n, DEFAULT)}))
    actual = pd.Series(rng.uniform(20, 300, len(idx)), index=idx)
    good = actual + rng.normal(0, 5, len(idx))
    bad = pd.Series(rng.uniform(20, 300, len(idx)), index=idx)
    holes = good.copy()
    holes.loc[st.window_slots(nights[1], DEFAULT)[3]] = np.nan  # one night dropped for everybody
    df = value.simulate(actual, {"good": good, "holes": holes}, DEFAULT, nights)
    assert list(df["night"]) == [nights[0], nights[2]]
    assert (df["perfect"] <= df["good"] + 1e-9).all() and (df["perfect"] <= df["timer"] + 1e-9).all()
    s = value.summarize(df, ["good"], n_boot=50).set_index("strategy")
    assert s.loc["perfect", "gap_captured"] == pytest.approx(1.0)
    assert s.loc["timer", "gap_captured"] == pytest.approx(0.0)
    assert 0.5 < s.loc["good", "gap_captured"] <= 1.0


def test_mon_wed_fri_scenario_only_includes_those_nights():
    sc = next(s for s in sensitivity_set() if s.nights == "mon_wed_fri")
    assert [sc.includes(date(2024, 1, d)) for d in range(15, 22)] == [True, False, True, False, True, False, False]
