"""When does each kind of value become available? The single source of truth for leakage.

Every feature is built from a source value plus an `available_at` time computed here.
A value may enter the feature matrix for target day D only if
`available_at <= cutoff_utc(D)`. All timestamps are naive UTC.
"""

import pandas as pd

from carbon_charge import config
from carbon_charge.timeutils import HALF_HOUR, cutoff_utc

CI_ACTUAL_LAG = pd.Timedelta(minutes=config.CI_ACTUAL_LAG_MINUTES)
WEATHER_RUN_LAG = pd.Timedelta(hours=config.WEATHER_RUN_LAG_HOURS)
# Calendar features are known in advance; they get this as their availability time.
KNOWN_IN_ADVANCE = pd.Timestamp("1970-01-01")


def cutoffs(dates: pd.Series) -> pd.Series:
    """Naive-UTC cutoff for each settlement date in `dates`."""
    unique = pd.Series(dates.unique())
    lookup = {d: cutoff_utc(d).replace(tzinfo=None) for d in unique}
    return dates.map(lookup).astype("datetime64[ns]")


def ci_actual_available_at(period_start: pd.Series) -> pd.Series:
    """A CI actual is usable CI_ACTUAL_LAG after its period ends."""
    return period_start + HALF_HOUR + CI_ACTUAL_LAG


def latest_available_ci_period(cutoff: pd.Series) -> pd.Series:
    """Start of the last CI period whose actual is available at `cutoff`."""
    return (cutoff - CI_ACTUAL_LAG - HALF_HOUR).dt.floor("30min")


def weather_available_at(issued_at: pd.Series) -> pd.Series:
    """A stored weather forecast is usable WEATHER_RUN_LAG after its (upper-bound) issue time."""
    return issued_at + WEATHER_RUN_LAG


MID_LAG = pd.Timedelta(minutes=config.MID_LAG_MINUTES)


def mid_available_at(period_start: pd.Series) -> pd.Series:
    """A Market Index price is usable MID_LAG after its period ends."""
    return period_start + HALF_HOUR + MID_LAG


def latest_available_period(cutoff: pd.Series, lag: pd.Timedelta) -> pd.Series:
    """Start of the last half-hour whose value (published `lag` after it ends) is available at `cutoff`."""
    return (cutoff - lag - HALF_HOUR).dt.floor("30min")


def day_ahead_published_at(settlement_date: pd.Series) -> pd.Series:
    """The day-ahead auction for settlement day D is published at 10:00 UTC on D-1."""
    t = config.DAY_AHEAD_PRICE_PUBLISHED_UTC
    return pd.to_datetime(settlement_date) - pd.Timedelta(days=1) + pd.Timedelta(hours=t.hour, minutes=t.minute)
