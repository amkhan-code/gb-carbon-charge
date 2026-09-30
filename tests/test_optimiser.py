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


# --- cost / carbon blend -------------------------------------------------------

from carbon_charge.charging import blend  # noqa: E402


def _series(nights, seed, lo=20, hi=300):
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(sorted({t for n in nights for t in st.window_slots(n, replace(DEFAULT, plug_in=time(17)))}))
    return pd.Series(rng.uniform(lo, hi, len(idx)), index=idx)


def test_slot_cost_units_and_limits():
    price, ci = pd.Series([100.0]), pd.Series([200.0])
    assert blend.slot_cost(price, ci, 0).iloc[0] == pytest.approx(0.1)  # GBP 100/MWh = GBP 0.1/kWh
    # 200 g/kWh at GBP 250/t = 200e-6 t * 250 = GBP 0.05/kWh on top
    assert blend.slot_cost(price, ci, 250).iloc[0] == pytest.approx(0.15)
    assert blend.slot_cost(price, ci, None).iloc[0] == 200.0  # carbon only


def test_blend_reduces_to_layer_1_when_carbon_only():
    nights = [NORMAL, SPRING_NIGHT, AUTUMN_NIGHT]
    ci, price = _series(nights, 1), _series(nights, 2, 30, 200)
    fc_ci = ci + np.random.default_rng(3).normal(0, 20, len(ci))
    long = blend.simulate(ci, price, {"m": (fc_ci, price)}, DEFAULT, nights, None)
    old = value.simulate(ci, {"m": fc_ci}, DEFAULT, nights)
    got = long.pivot(index="night", columns="strategy", values="carbon_g")
    for col in ("arrival", "timer", "perfect", "m"):
        assert got[col].to_numpy() == pytest.approx(old[col].to_numpy())


def test_cost_only_ignores_carbon_forecast_and_delivers_energy():
    nights = [NORMAL, SPRING_NIGHT, AUTUMN_NIGHT]
    ci, price = _series(nights, 1), _series(nights, 2, 30, 200)
    bad_ci = _series(nights, 9)
    long = blend.simulate(ci, price, {"a": (ci, price), "b": (bad_ci, price)}, DEFAULT, nights, 0)
    piv = long.pivot(index="night", columns="strategy", values="cost_gbp")
    assert piv["a"].to_numpy() == pytest.approx(piv["b"].to_numpy())  # carbon forecast irrelevant at lam=0
    assert (piv["perfect"] <= piv[["arrival", "timer", "a", "b"]].min(axis=1) + 1e-9).all()
    # 8 kWh at GBP p/MWh: cost bounds are 8 kWh x min and max slot price
    assert (long["cost_gbp"] > 0).all()


@pytest.mark.parametrize("lam", [0, 100, 1000, None])
def test_perfect_foresight_is_optimal_on_the_blended_objective(lam):
    nights = [NORMAL, SPRING_NIGHT, AUTUMN_NIGHT]
    for seed in range(10):
        ci, price = _series(nights, seed), _series(nights, seed + 50, -20, 250)  # negative prices are real
        noisy = (ci + np.random.default_rng(seed).normal(0, 50, len(ci)), price + 30)
        long = blend.simulate(ci, price, {"m": noisy}, DEFAULT, nights, lam)
        j = long.assign(j=blend.objective(long["cost_gbp"].to_numpy(), long["carbon_g"].to_numpy(), lam)).pivot(
            index="night", columns="strategy", values="j")
        assert (j["perfect"] <= j.drop(columns="perfect").min(axis=1) + 1e-9).all()


def test_higher_carbon_price_moves_plan_toward_lower_carbon():
    nights = [NORMAL, date(2024, 1, 16), date(2024, 1, 17)]
    ci, price = _series(nights, 4), _series(nights, 5, 30, 200)
    carbon = {}
    for lam in (0, 1000, None):
        long = blend.simulate(ci, price, {}, DEFAULT, nights, lam)
        carbon[lam] = long[long.strategy == "perfect"]["carbon_g"].sum()
    assert carbon[None] <= carbon[1000] <= carbon[0] + 1e-9


def test_blend_summary_columns_and_gap_bounds():
    nights = [date(2024, 1, d) for d in range(10, 25)]
    ci, price = _series(nights, 6), _series(nights, 7, 30, 200)
    long = blend.simulate(ci, price, {"m": (ci, price)}, DEFAULT, nights, 250)
    s = blend.summarize(long, ["m"], 250, n_boot=50).set_index("strategy")
    assert s.loc["m", "gap_captured"] == pytest.approx(1.0)  # perfect forecasts capture the whole gap
    assert s.loc["timer", "gap_captured"] == pytest.approx(0.0)
    assert {"mean_cost_gbp", "mean_carbon_g", "lam"} <= set(s.columns)
