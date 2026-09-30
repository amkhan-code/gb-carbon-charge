"""Cost/carbon trade-off for charging.

Each half-hour slot has a marginal cost per kWh:

    price_gbp_mwh / 1000  +  lam * carbon_g_per_kwh / 1e6        [GBP/kWh]

where `lam` is a carbon price in GBP per tonne CO2. lam = 0 is cost only; lam = None means carbon
only (Layer 1). Plans are made with forecasts and scored with realised values, so a strategy's
GBP cost and grams of CO2 are always outturn numbers.
"""

from datetime import date

import numpy as np
import pandas as pd

from carbon_charge.charging import strategies as st
from carbon_charge.charging import value
from carbon_charge.charging.scenarios import Scenario

LAMBDAS: list[float | None] = [0, 50, 100, 250, 500, 1000, 2500, None]


def slot_cost(price_gbp_mwh: pd.Series, ci_g_kwh: pd.Series, lam: float | None) -> pd.Series:
    if lam is None:
        return ci_g_kwh
    return price_gbp_mwh / 1000 + lam * ci_g_kwh / 1e6


def objective(cost_gbp: np.ndarray, carbon_g: np.ndarray, lam: float | None) -> np.ndarray:
    """The quantity a plan minimises, in the same units as `slot_cost` summed over kWh."""
    return carbon_g if lam is None else cost_gbp + lam * carbon_g / 1e6


def _realised(plan: pd.Series, price: pd.Series, ci: pd.Series) -> tuple[float, float]:
    kwh = plan
    return float((kwh * price.reindex(kwh.index)).sum() / 1000), float((kwh * ci.reindex(kwh.index)).sum())


def simulate(
    actual_ci: pd.Series,
    actual_price: pd.Series,
    pipelines: dict[str, tuple[pd.Series, pd.Series]],
    sc: Scenario,
    nights: list[date],
    lam: float | None,
) -> pd.DataFrame:
    """One row per (night, strategy) with realised cost (GBP) and carbon (g).

    `pipelines` maps a name to (carbon forecast, price forecast). Nights where any input is
    missing anywhere in the window are dropped for every strategy.
    """
    rows = []
    inputs = [actual_ci, actual_price] + [s for pair in pipelines.values() for s in pair]
    perfect_cost = slot_cost(actual_price, actual_ci, lam)
    pipeline_cost = {n: slot_cost(price_fc, ci_fc, lam) for n, (ci_fc, price_fc) in pipelines.items()}
    for night in nights:
        if not sc.includes(night):
            continue
        slots = st.window_slots(night, sc)
        if any(s.reindex(slots).isna().any() for s in inputs):
            continue
        plans = {
            "arrival": st.charge_on_arrival(night, sc),
            "timer": st.overnight_timer(night, sc),
            "perfect": st.optimised(night, sc, perfect_cost),
        }
        for name, cost in pipeline_cost.items():
            plans[name] = st.optimised(night, sc, cost)
        for name, plan in plans.items():
            cost, carbon = _realised(plan, actual_price, actual_ci)
            rows.append({"night": night, "strategy": name, "cost_gbp": cost, "carbon_g": carbon})
    return pd.DataFrame(rows)


def summarize(long: pd.DataFrame, pipelines: list[str], lam: float | None, **kw) -> pd.DataFrame:
    """Gap captured on the blended objective, plus mean realised cost and carbon per night."""
    if long.empty:
        return pd.DataFrame()
    wide = long.assign(j=objective(long["cost_gbp"].to_numpy(), long["carbon_g"].to_numpy(), lam)).pivot(
        index="night", columns="strategy", values="j").reset_index()
    out = value.summarize(wide, pipelines, **kw)
    means = long.groupby("strategy")[["cost_gbp", "carbon_g"]].mean()
    out["mean_cost_gbp"] = out["strategy"].map(means["cost_gbp"])
    out["mean_carbon_g"] = out["strategy"].map(means["carbon_g"])
    out["lam"] = "carbon only" if lam is None else lam
    return out
