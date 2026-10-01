"""Emissions per strategy across nights, and the headline 'gap captured' metric."""

from datetime import date

import numpy as np
import pandas as pd

from carbon_charge.charging import strategies as st
from carbon_charge.charging.scenarios import Scenario


def simulate(
    actual: pd.Series,
    forecasts: dict[str, pd.Series],
    sc: Scenario,
    nights: list[date],
) -> pd.DataFrame:
    """One row per night with grams CO2 for each strategy.

    Columns: arrival, timer, perfect, and one per forecast source. Nights where the
    actual or any forecast is missing anywhere in the window are dropped, so every
    strategy is compared on the same nights.
    """
    rows = []
    for night in nights:
        if not sc.includes(night):
            continue
        slots = st.window_slots(night, sc)
        if actual.reindex(slots).isna().any():
            continue
        if any(f.reindex(slots).isna().any() for f in forecasts.values()):
            continue
        row = {
            "night": night,
            "arrival": st.emissions_g(st.charge_on_arrival(night, sc), actual),
            "timer": st.emissions_g(st.overnight_timer(night, sc), actual),
            "perfect": st.emissions_g(st.optimised(night, sc, actual), actual),
        }
        for name, f in forecasts.items():
            row[name] = st.emissions_g(st.optimised(night, sc, f), actual)
        rows.append(row)
    return pd.DataFrame(rows)


def gap_captured(timer: np.ndarray, strategy: np.ndarray, perfect: np.ndarray) -> float:
    """Share of the timer -> perfect-foresight emissions gap closed by `strategy`."""
    gap = (timer - perfect).sum()
    return float((timer - strategy).sum() / gap) if gap > 0 else float("nan")


def summarize(nights: pd.DataFrame, sources: list[str], block: int = 7, n_boot: int = 2000, seed: int = 1) -> pd.DataFrame:
    """Per-source mean emissions and gap captured, with a moving-block bootstrap 95% CI."""
    if nights.empty:
        return pd.DataFrame()
    timer, perfect, arrival = (nights[c].to_numpy() for c in ("timer", "perfect", "arrival"))
    n = len(nights)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, max(n - block + 1, 1), size=(n_boot, int(np.ceil(n / block))))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :n] % n
    rows = []
    for name in sources:
        s = nights[name].to_numpy()
        boots = [gap_captured(timer[i], s[i], perfect[i]) for i in idx]
        rows.append({
            "strategy": name,
            "nights": n,
            "mean_g_per_night": s.mean(),
            "saving_vs_timer_pct": 100 * (1 - s.sum() / timer.sum()),
            "saving_vs_arrival_pct": 100 * (1 - s.sum() / arrival.sum()),
            "gap_captured": gap_captured(timer, s, perfect),
            "gap_ci_low": np.nanpercentile(boots, 2.5),
            "gap_ci_high": np.nanpercentile(boots, 97.5),
        })
    for name in ("arrival", "timer", "perfect"):
        s = nights[name].to_numpy()
        rows.append({
            "strategy": name, "nights": n, "mean_g_per_night": s.mean(),
            "saving_vs_timer_pct": 100 * (1 - s.sum() / timer.sum()),
            "saving_vs_arrival_pct": 100 * (1 - s.sum() / arrival.sum()),
            "gap_captured": gap_captured(timer, s, perfect),
            "gap_ci_low": np.nan, "gap_ci_high": np.nan,
        })
    return pd.DataFrame(rows)


def paired_gap_difference(nights: pd.DataFrame, a: str, b: str, block: int = 7, n_boot: int = 2000, seed: int = 1) -> dict:
    """Gap captured by `a` minus by `b`, on the same nights, with a moving-block bootstrap 95% CI."""
    timer, perfect = nights["timer"].to_numpy(), nights["perfect"].to_numpy()
    xa, xb = nights[a].to_numpy(), nights[b].to_numpy()
    n = len(nights)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, max(n - block + 1, 1), size=(n_boot, int(np.ceil(n / block))))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :n] % n
    diffs = [gap_captured(timer[i], xa[i], perfect[i]) - gap_captured(timer[i], xb[i], perfect[i]) for i in idx]
    return {
        "model": a, "vs": b, "nights": n,
        "gap_diff": gap_captured(timer, xa, perfect) - gap_captured(timer, xb, perfect),
        "ci_low": float(np.nanpercentile(diffs, 2.5)), "ci_high": float(np.nanpercentile(diffs, 97.5)),
        "grams_saved_per_night": float((xb - xa).mean()),
    }
