"""The four charging strategies. Each returns a plan: kWh delivered in each half-hour slot.

Slots are half-hours identified by naive-UTC start time, so clock-change nights
(which have 1 hour fewer/more in the window) need no special handling.
"""

from datetime import date, datetime, timedelta

import pandas as pd

from carbon_charge.charging.scenarios import Scenario
from carbon_charge.timeutils import HALF_HOUR, UK, UTC, local_midnight_utc

SLOT_HOURS = 0.5
EPS = 1e-9


class Infeasible(ValueError):
    pass


def _local_to_naive_utc(d: date, t) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, t, tzinfo=UK).astimezone(UTC).replace(tzinfo=None))


def window_slots(night: date, sc: Scenario) -> pd.DatetimeIndex:
    """Half-hour slots from plug-in on `night` until the deadline on the next morning."""
    start = _local_to_naive_utc(night, sc.plug_in)
    end = _local_to_naive_utc(night + timedelta(days=1), sc.deadline)
    return pd.date_range(start, end, freq=HALF_HOUR, inclusive="left")


def timer_start(night: date) -> pd.Timestamp:
    """Overnight timer: charging begins at 00:00 UK time on the morning after `night`."""
    return pd.Timestamp(local_midnight_utc(night + timedelta(days=1)).replace(tzinfo=None))


def _fill(order: list[pd.Timestamp], sc: Scenario) -> pd.Series:
    cap = sc.power_kw * SLOT_HOURS
    remaining, plan = sc.energy_kwh, {}
    for t in order:
        if remaining <= EPS:
            break
        e = min(cap, remaining)
        plan[t] = e
        remaining -= e
    if remaining > EPS:
        raise Infeasible(f"{sc.name}: {remaining:.2f} kWh cannot be delivered in the window")
    return pd.Series(plan, dtype="float64")


def charge_on_arrival(night: date, sc: Scenario) -> pd.Series:
    return _fill(list(window_slots(night, sc)), sc)


def overnight_timer(night: date, sc: Scenario) -> pd.Series:
    start = timer_start(night)
    return _fill([t for t in window_slots(night, sc) if t >= start], sc)


def optimised(night: date, sc: Scenario, cost: pd.Series) -> pd.Series:
    """Fill the cheapest slots first (optimal for a per-slot power cap and linear cost).

    `cost` is a forecast for the forecast-optimised strategy and the actual for
    perfect foresight. Ties break toward the earlier slot.
    """
    slots = list(window_slots(night, sc))
    if cost.reindex(slots).isna().any():
        raise ValueError("cost missing for part of the charging window")
    return _fill(sorted(slots, key=lambda t: (cost[t], t)), sc)


def emissions_g(plan: pd.Series, actual: pd.Series) -> float:
    """Grams of CO2 emitted by a plan, priced at the actual carbon intensity."""
    return float((plan * actual.reindex(plan.index)).sum())
