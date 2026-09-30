"""Leak-free feature matrix for day-ahead forecasting.

Forecast for settlement day D is made at cutoff_utc(D) (11:00 UK on D-1). Every
feature value comes with the time it became available; anything not available
by the cutoff is set to NaN. Nothing derived from actual demand or observed
weather is used. LightGBM needs no scaling, so no scaler is fitted anywhere; if
one is ever added it must be fitted on training rows only.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import holidays
import numpy as np
import pandas as pd

from carbon_charge import config
from carbon_charge.features import availability as av
from carbon_charge.timeutils import (
    HALF_HOUR,
    UK,
    add_settlement_columns,
    local_midnight_utc,
)

# Every feature declares one of these sources.
ALLOWED_SOURCES = {"calendar", "ci_actual", "demand_forecast", "weather_forecast"}

WEATHER_ROLE_AGGREGATES = {
    # role -> (variable column, aggregate feature name)
    "offshore wind": ("wind_speed_100m_ms", "wx_wind100_offshore_mean"),
    "onshore wind": ("wind_speed_100m_ms", "wx_wind100_onshore_mean"),
    "solar": ("shortwave_radiation_wm2", "wx_solar_rad_mean"),
    "demand": ("temperature_2m_c", "wx_temp_demand_mean"),
}


@dataclass
class FeatureSet:
    X: pd.DataFrame  # index ts_utc; feature columns only
    avail: pd.DataFrame  # same shape as X: when each value became available (NaT where NaN)
    sources: dict[str, str]  # feature -> source kind
    meta: pd.DataFrame  # index ts_utc: settlement_date, settlement_period, cutoff_utc, local_hour
    y: pd.Series  # actual carbon intensity (the label; never a feature)


def _grid(start: date, end: date) -> pd.DataFrame:
    """One row per half-hour of every settlement day in [start, end]."""
    lo = local_midnight_utc(start).replace(tzinfo=None)
    hi = local_midnight_utc(end + timedelta(days=1)).replace(tzinfo=None)
    ts = pd.date_range(lo, hi, freq="30min", inclusive="left")
    df = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    df["settlement_date"] = pd.to_datetime(df["settlement_date"])
    df["cutoff_utc"] = av.cutoffs(df["settlement_date"].dt.date)
    local = df["ts_utc"].dt.tz_localize("UTC").dt.tz_convert(UK)
    df["local_hour"] = local.dt.hour + local.dt.minute / 60
    return df.set_index("ts_utc")


class _Builder:
    def __init__(self, meta: pd.DataFrame):
        self.meta = meta
        self.cols: dict[str, pd.Series] = {}
        self.avail: dict[str, pd.Series] = {}
        self.sources: dict[str, str] = {}

    def add(self, name: str, source: str, values: pd.Series, available_at: pd.Series) -> None:
        assert source in ALLOWED_SOURCES, source
        values = pd.Series(values.to_numpy(dtype="float64"), index=self.meta.index)
        avail = pd.Series(pd.to_datetime(available_at).to_numpy(), index=self.meta.index)
        # THE leakage gate: anything not available by the cutoff is removed.
        late = avail > self.meta["cutoff_utc"]
        values[late] = np.nan
        avail[values.isna()] = pd.NaT
        self.cols[name], self.avail[name], self.sources[name] = values, avail, source


def _calendar(b: _Builder) -> None:
    m = b.meta
    d = m["settlement_date"]
    hol = holidays.country_holidays("GB", subdiv="ENG", years=range(d.dt.year.min(), d.dt.year.max() + 2))
    is_hol = d.dt.date.map(lambda x: x in hol).astype(float)
    known = pd.Series(av.KNOWN_IN_ADVANCE, index=m.index)
    doy = d.dt.dayofyear
    for name, vals in {
        "settlement_period": m["settlement_period"],
        "local_hour": m["local_hour"],
        "day_of_week": d.dt.dayofweek,
        "is_weekend": (d.dt.dayofweek >= 5),
        "is_bank_holiday": is_hol,
        "month": d.dt.month,
        "doy_sin": np.sin(2 * np.pi * doy / 365.25),
        "doy_cos": np.cos(2 * np.pi * doy / 365.25),
    }.items():
        b.add(name, "calendar", vals.astype(float), known)


def _ci_lags(b: _Builder, ci: pd.DataFrame) -> None:
    """Lagged carbon intensity actuals, each masked by publication time."""
    m = b.meta
    by_slot = ci.dropna(subset=["actual_gco2_kwh"]).copy()
    by_slot["settlement_date"] = pd.to_datetime(by_slot["settlement_date"])
    by_slot = by_slot.set_index(["settlement_date", "settlement_period"])
    by_slot = by_slot[~by_slot.index.duplicated()]

    for k in (1, 2, 3, 7):
        key = pd.MultiIndex.from_arrays(
            [m["settlement_date"] - pd.Timedelta(days=k), m["settlement_period"]]
        )
        src = by_slot.reindex(key)
        avail = av.ci_actual_available_at(pd.Series(src["ts_utc"].to_numpy(), index=m.index))
        b.add(f"ci_lag_d{k}", "ci_actual", pd.Series(src["actual_gco2_kwh"].to_numpy(), index=m.index), avail)

    # State of the grid at the moment of the cutoff.
    grid = ci.set_index("ts_utc")["actual_gco2_kwh"]
    grid = grid.reindex(pd.date_range(grid.index.min(), grid.index.max(), freq="30min"))
    roll = {
        "ci_last_at_cutoff": grid,
        "ci_mean_24h_at_cutoff": grid.rolling(48, min_periods=36).mean(),
        "ci_mean_7d_at_cutoff": grid.rolling(336, min_periods=250).mean(),
        "ci_std_24h_at_cutoff": grid.rolling(48, min_periods=36).std(),
    }
    last = av.latest_available_ci_period(m["cutoff_utc"])
    for name, series in roll.items():
        vals = series.reindex(last.to_numpy())
        b.add(name, "ci_actual", pd.Series(vals.to_numpy(), index=m.index),
              av.ci_actual_available_at(last))


def _demand(b: _Builder, con: duckdb.DuckDBPyConnection) -> None:
    m = b.meta
    lo, hi = m.index.min(), m.index.max()
    d = con.execute(
        "SELECT ts_utc, issued_at_utc, demand_forecast_mw FROM demand_forecast WHERE ts_utc BETWEEN ? AND ?",
        [lo, hi],
    ).df()
    d = d.merge(m[["cutoff_utc"]], left_on="ts_utc", right_index=True)
    # Latest vintage published by the cutoff (not merely the latest vintage).
    d = d[d["issued_at_utc"] <= d["cutoff_utc"]].sort_values("issued_at_utc").drop_duplicates("ts_utc", keep="last")
    d = d.set_index("ts_utc").reindex(m.index)
    b.add("demand_forecast_mw", "demand_forecast", d["demand_forecast_mw"], d["issued_at_utc"])


def _weather(b: _Builder, con: duckdb.DuckDBPyConnection) -> None:
    m = b.meta
    lead = config.WEATHER_FEATURE_LEAD_DAYS
    w = con.execute(
        "SELECT * FROM weather_forecast WHERE lead_days = ? AND model = ? AND ts_utc BETWEEN ? AND ?",
        [lead, config.WEATHER_MODEL, m.index.min().floor("h"), m.index.max()],
    ).df()
    hour = m.index.floor("h")
    avail_hour = av.weather_available_at(w.drop_duplicates("ts_utc").set_index("ts_utc")["issued_at_utc"])
    avail = pd.Series(avail_hour.reindex(hour).to_numpy(), index=m.index)
    cols = list(config.WEATHER_VARIABLES.values())
    wide = w.pivot(index="ts_utc", columns="location", values=cols)
    for col in cols:
        for loc in config.WEATHER_LOCATIONS:
            series = wide[col][loc.name] if loc.name in wide[col] else pd.Series(dtype=float)
            vals = series.reindex(hour)
            b.add(f"wx_{col}__{loc.name}", "weather_forecast", pd.Series(vals.to_numpy(), index=m.index), avail)
    for role, (col, name) in WEATHER_ROLE_AGGREGATES.items():
        names = [f"wx_{col}__{loc.name}" for loc in config.WEATHER_LOCATIONS if loc.role == role]
        b.add(name, "weather_forecast", pd.concat([b.cols[n] for n in names], axis=1).mean(axis=1), avail)


def build(con: duckdb.DuckDBPyConnection, start: date, end: date) -> FeatureSet:
    """Features (and label) for every half-hour of settlement days start..end."""
    meta = _grid(start, end)
    b = _Builder(meta)
    ci = con.execute(
        "SELECT ts_utc, settlement_date, settlement_period, actual_gco2_kwh FROM ci_history ORDER BY ts_utc"
    ).df()
    _calendar(b)
    _ci_lags(b, ci)
    _demand(b, con)
    _weather(b, con)
    y = ci.set_index("ts_utc")["actual_gco2_kwh"].reindex(meta.index).rename("actual_gco2_kwh")
    return FeatureSet(
        X=pd.DataFrame(b.cols),
        avail=pd.DataFrame(b.avail),
        sources=b.sources,
        meta=meta,
        y=y,
    )
