"""UTC <-> GB settlement date/period conversions.

A settlement day is a UK local calendar day. It has 48 half-hour periods,
except 46 on the spring clock-change day and 50 on the autumn one.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from carbon_charge.config import CUTOFF_TIME

UK = ZoneInfo("Europe/London")
UTC = timezone.utc
HALF_HOUR = timedelta(minutes=30)


def local_midnight_utc(d: date) -> datetime:
    """UTC instant at which UK local day `d` starts."""
    return datetime.combine(d, time(0), tzinfo=UK).astimezone(UTC)


def periods_in_day(d: date) -> int:
    return int((local_midnight_utc(d + timedelta(days=1)) - local_midnight_utc(d)) / HALF_HOUR)


def period_start_utc(d: date, period: int) -> datetime:
    n = periods_in_day(d)
    if not 1 <= period <= n:
        raise ValueError(f"settlement period {period} out of range 1-{n} for {d}")
    return local_midnight_utc(d) + (period - 1) * HALF_HOUR


def settlement_date_period(ts_utc: datetime) -> tuple[date, int]:
    """Settlement date and period containing `ts_utc` (naive is taken as UTC)."""
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=UTC)
    d = ts_utc.astimezone(UK).date()
    return d, int((ts_utc - local_midnight_utc(d)) // HALF_HOUR) + 1


def cutoff_utc(target_date: date) -> datetime:
    """Information cutoff for forecasting `target_date`: CUTOFF_TIME UK local on D-1."""
    return datetime.combine(target_date - timedelta(days=1), CUTOFF_TIME, tzinfo=UK).astimezone(UTC)


def to_naive_utc(s: pd.Series) -> pd.Series:
    """Timestamps as naive UTC, the form stored in DuckDB (naive input is taken as UTC)."""
    return pd.to_datetime(s, utc=True).dt.tz_localize(None)


def add_settlement_columns(df: pd.DataFrame, ts_col: str = "ts_utc") -> pd.DataFrame:
    """Add settlement_date and settlement_period derived from a UTC timestamp column."""
    ts = pd.to_datetime(df[ts_col], utc=True)
    midnight = ts.dt.tz_convert(UK).dt.normalize()
    out = df.copy()
    out["settlement_date"] = midnight.dt.tz_localize(None).dt.date
    out["settlement_period"] = ((ts - midnight) // HALF_HOUR + 1).astype("int16")
    return out


def period_start_utc_series(dates: pd.Series, periods: pd.Series) -> pd.Series:
    """Vectorised period_start_utc, returned as naive UTC. Out-of-range periods give NaT."""
    days = pd.to_datetime(dates)
    midnight = days.dt.tz_localize(UK).dt.tz_convert("UTC")
    next_midnight = (days + pd.Timedelta(days=1)).dt.tz_localize(UK).dt.tz_convert("UTC")
    ts = midnight + (periods - 1) * HALF_HOUR
    valid = (periods >= 1) & (ts < next_midnight)
    return ts.where(valid).dt.tz_localize(None)
