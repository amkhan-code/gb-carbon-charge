"""NESO day-ahead half-hourly national demand forecast.

Source: "Day Ahead Half Hourly Demand Forecast Performance" on the NESO data
portal. The file also carries demand outturn, which is deliberately not read:
actual demand must never reach the feature store.
"""

import io
from datetime import datetime

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.ingest import http
from carbon_charge.timeutils import UTC, period_start_utc_series, to_naive_utc

COLUMNS = ["Date", "Settlement_Period", "Demand_Forecast", "Publish_Datetime"]


def fetch() -> pd.DataFrame:
    meta = http.get(
        f"{config.NESO_CKAN_URL}/resource_show", params={"id": config.NESO_DEMAND_RESOURCE_ID}
    ).json()
    csv = http.get(meta["result"]["url"]).content
    return pd.read_csv(io.BytesIO(csv), usecols=COLUMNS)


def transform(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean rows, rejected raw rows)."""
    df = raw.copy()
    df["settlement_date"] = pd.to_datetime(df["Date"]).dt.date
    # The file's own Datetime column is ambiguous local time on clock-change
    # days, so the UTC key is derived from settlement date and period instead.
    df["ts_utc"] = period_start_utc_series(df["Date"], df["Settlement_Period"])
    # Publish_Datetime carries a "Z" suffix and is taken as UTC (see DATA_ISSUES.md).
    df["issued_at_utc"] = to_naive_utc(df["Publish_Datetime"])
    bad = df["ts_utc"].isna() | df["issued_at_utc"].isna() | df["Demand_Forecast"].isna()
    clean = df.loc[~bad].rename(
        columns={"Settlement_Period": "settlement_period", "Demand_Forecast": "demand_forecast_mw"}
    )[["ts_utc", "issued_at_utc", "settlement_date", "settlement_period", "demand_forecast_mw"]]
    return clean, raw.loc[bad]


def ingest(con: duckdb.DuckDBPyConnection) -> tuple[int, pd.DataFrame]:
    clean, rejected = transform(fetch())
    clean = clean[clean["settlement_date"] >= config.HISTORY_START].copy()
    clean["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "demand_forecast", clean), rejected
