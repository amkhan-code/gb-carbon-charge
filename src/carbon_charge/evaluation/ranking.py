"""Slot-ranking accuracy: does the forecast pick the actually-greenest slots?"""

import math
from datetime import date

import pandas as pd

from carbon_charge.charging.scenarios import DEFAULT, Scenario
from carbon_charge.charging.strategies import SLOT_HOURS, window_slots


def slots_needed(sc: Scenario) -> int:
    return math.ceil(sc.energy_kwh / (sc.power_kw * SLOT_HOURS))


def overlap(forecast: pd.Series, actual: pd.Series, slots: pd.DatetimeIndex, k: int) -> float:
    """|forecast-greenest k slots ∩ actual-greenest k slots| / k, within the window."""
    f, a = forecast.reindex(slots), actual.reindex(slots)
    top_f = set(f.sort_values(kind="stable").index[:k])
    top_a = set(a.sort_values(kind="stable").index[:k])
    return len(top_f & top_a) / k


def ranking_accuracy(
    actual: pd.Series,
    forecasts: dict[str, pd.Series],
    nights: list[date],
    sc: Scenario = DEFAULT,
    ks: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """Mean overlap per source and k, over nights with complete data for every source.

    Includes a 'random' row: the expected overlap of a random choice of k slots.
    """
    ks = ks or (slots_needed(sc), 2 * slots_needed(sc))
    per_night: list[dict] = []
    for night in nights:
        slots = window_slots(night, sc)
        if actual.reindex(slots).isna().any() or any(f.reindex(slots).isna().any() for f in forecasts.values()):
            continue
        for k in ks:
            for name, f in forecasts.items():
                per_night.append({"night": night, "k": k, "source": name,
                                  "overlap": overlap(f, actual, slots, k), "window_slots": len(slots)})
    df = pd.DataFrame(per_night)
    if df.empty:
        return df
    out = df.groupby(["source", "k"]).agg(mean_overlap=("overlap", "mean"), nights=("night", "nunique")).reset_index()
    rnd = df.groupby("k").agg(win=("window_slots", "mean"), nights=("night", "nunique")).reset_index()
    rnd = pd.DataFrame({"source": "random", "k": rnd["k"], "mean_overlap": rnd["k"] / rnd["win"], "nights": rnd["nights"]})
    return pd.concat([out, rnd], ignore_index=True)
