"""Baseline day-ahead forecasts. All obey the same 11:00 D-1 cutoff as the model."""

import duckdb
import pandas as pd

from carbon_charge.features.build import FeatureSet


def yesterday(fs: FeatureSet) -> pd.Series:
    """Same period yesterday, as far as the cutoff allows.

    At 11:00 on D-1 only the first part of D-1 has been published, so for later
    periods this falls back to the same period on D-2 (see `yesterday_is_fallback`).
    """
    return fs.X["ci_lag_d1"].fillna(fs.X["ci_lag_d2"])


def yesterday_is_fallback(fs: FeatureSet) -> pd.Series:
    return fs.X["ci_lag_d1"].isna()


def last_week(fs: FeatureSet) -> pd.Series:
    """Same period 7 days before the target day (always published by the cutoff)."""
    return fs.X["ci_lag_d7"]


def neso_logged(con: duckdb.DuckDBPyConnection, fs: FeatureSet) -> pd.Series:
    """NESO's forward forecast as issued by our daily logger, latest snapshot before the cutoff.

    Only exists for days the logger has run; NaN elsewhere.
    """
    m = fs.meta
    log = con.execute(
        "SELECT ts_utc, issued_at_utc, forecast_gco2_kwh FROM ci_forecast_log WHERE ts_utc BETWEEN ? AND ?",
        [m.index.min(), m.index.max()],
    ).df()
    log = log.merge(m[["cutoff_utc"]], left_on="ts_utc", right_index=True)
    log = log[log["issued_at_utc"] <= log["cutoff_utc"]]
    log = log.sort_values("issued_at_utc").drop_duplicates("ts_utc", keep="last")
    return log.set_index("ts_utc")["forecast_gco2_kwh"].reindex(m.index)


def neso_latest_revision(con: duckdb.DuckDBPyConnection, fs: FeatureSet) -> pd.Series:
    """REFERENCE ONLY. The API's historical `forecast` field is not day-ahead (DATA_ISSUES CI-1)."""
    ci = con.execute("SELECT ts_utc, neso_forecast_latest_gco2_kwh v FROM ci_history").df()
    return ci.set_index("ts_utc")["v"].reindex(fs.meta.index)


# --- Realised (Market Index) price baselines -----------------------------------

def price_day_ahead(fs: FeatureSet) -> pd.Series:
    """The known day-ahead auction price used directly as the forecast of the realised price."""
    return fs.X["da_price"]


def price_yesterday(fs: FeatureSet) -> pd.Series:
    """Realised price for the same period yesterday, falling back to D-2 (cutoff-aware)."""
    return fs.X["mid_lag_d1"].fillna(fs.X["mid_lag_d2"])


def price_last_week(fs: FeatureSet) -> pd.Series:
    return fs.X["mid_lag_d7"]


def price_da_plus_basis(fs: FeatureSet) -> pd.Series:
    """Day-ahead price plus the trailing 7-day mean gap between realised and day-ahead prices."""
    return fs.X["da_price"] + fs.X["basis_mean_7d_at_cutoff"]
