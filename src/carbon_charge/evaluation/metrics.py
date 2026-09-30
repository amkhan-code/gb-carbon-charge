"""MAE / RMSE by season and time of day."""

import numpy as np
import pandas as pd

from carbon_charge.timeutils import UK

SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
CORE_MODELS = ["lgbm", "lgbm_noweather", "lgbm_price", "yesterday", "last_week"]
PRICE_CORE_MODELS = ["price_lgbm", "price_da", "price_da_basis7d", "price_yesterday", "price_last_week"]


def eval_frame(forecasts: pd.DataFrame, actual: pd.Series) -> pd.DataFrame:
    """Long frame: ts_utc, model, forecast, actual, error, season, tod_block, local_hour.

    `forecasts` has ts_utc, model and one value column (carbon or price).
    """
    value_col = [c for c in forecasts.columns if c.startswith("forecast_")][0]
    df = forecasts.rename(columns={value_col: "forecast"}).copy()
    df["actual"] = df["ts_utc"].map(actual)
    df = df.dropna(subset=["forecast", "actual"])
    local = df["ts_utc"].dt.tz_localize("UTC").dt.tz_convert(UK)
    df["season"] = local.dt.month.map(SEASONS)
    df["local_hour"] = local.dt.hour
    df["tod_block"] = (local.dt.hour // 4 * 4).map(lambda h: f"{h:02d}-{h + 4:02d}")
    df["error"] = df["forecast"] - df["actual"]
    return df


def common_rows(df: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    """Rows (timestamps) for which every model in `models` has a forecast, restricted to those models."""
    sub = df[df["model"].isin(models)]
    counts = sub.groupby("ts_utc")["model"].nunique()
    keep = counts[counts == len(models)].index
    return sub[sub["ts_utc"].isin(keep)]


def summarize(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = df.groupby(["model", *by])["error"]
    out = g.agg(n="size", mae=lambda e: e.abs().mean(), rmse=lambda e: float(np.sqrt((e**2).mean()))).reset_index()
    return out.round({"mae": 2, "rmse": 2})


def tables(df: pd.DataFrame, models: list[str]) -> dict[str, pd.DataFrame]:
    d = common_rows(df, models)
    return {
        "overall": summarize(d, []),
        "by_season": summarize(d, ["season"]),
        "by_time_of_day": summarize(d, ["tod_block"]),
        "by_hour": summarize(d, ["local_hour"]),
    }
