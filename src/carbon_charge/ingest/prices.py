"""Wholesale prices: N2EX day-ahead (hourly) and Elexon Market Index (half-hourly)."""

from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.ingest import http
from carbon_charge.timeutils import UTC, add_settlement_columns, to_naive_utc

PAGE = 32000
MID_CHUNK = timedelta(days=6)  # the API rejects ranges over 7 days


def fetch_day_ahead() -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        resp = http.get(
            f"{config.NESO_CKAN_URL}/datastore_search",
            params={"resource_id": config.NESO_DAY_AHEAD_PRICE_RESOURCE_ID, "limit": PAGE, "offset": offset},
        ).json()["result"]["records"]
        rows += resp
        if len(resp) < PAGE:
            break
        offset += PAGE
    return pd.DataFrame(rows)


def transform_day_ahead(raw: pd.DataFrame) -> pd.DataFrame:
    """The source's Date is the UTC date and 'HH:00 - HH+1:00' is a UTC hour (DATA_ISSUES PX-1)."""
    df = raw.copy()
    hour = df["Delivery Period"].str.slice(0, 2).astype(int)
    df["ts_utc"] = pd.to_datetime(df["Date"]) + pd.to_timedelta(hour, unit="h")
    df["price_gbp_mwh"] = pd.to_numeric(df["Price"], errors="coerce")
    df = df.drop_duplicates("ts_utc", keep="last")
    return add_settlement_columns(df[["ts_utc", "price_gbp_mwh"]])


def ingest_day_ahead(con: duckdb.DuckDBPyConnection) -> int:
    df = transform_day_ahead(fetch_day_ahead())
    df = df[df["settlement_date"] >= config.HISTORY_START].copy()
    df["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "price_day_ahead", df)


def fetch_mid(start: date, end: date) -> pd.DataFrame:
    """MID rows for UTC days start..end inclusive."""
    frames, a = [], start
    while a <= end:
        b = min(a + MID_CHUNK, end)
        params = {"from": f"{a}T00:00Z", "to": f"{b}T23:59Z", "format": "json"}
        frames.append(pd.DataFrame(http.get(config.ELEXON_MID_URL, params=params).json()["data"]))
        a = b + timedelta(days=1)
    return pd.concat(frames, ignore_index=True)


def transform_mid(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw[raw["dataProvider"] == config.MID_PROVIDER].copy()
    df["ts_utc"] = to_naive_utc(df["startTime"])
    df = df.drop_duplicates("ts_utc", keep="last")
    # Zero volume means nothing traded: the 0.00 price is a placeholder, not a price.
    df["price_gbp_mwh"] = df["price"].where(df["volume"] > 0)
    df = df.rename(columns={"volume": "volume_mwh"})
    return add_settlement_columns(df[["ts_utc", "price_gbp_mwh", "volume_mwh"]])


def ingest_mid(con: duckdb.DuckDBPyConnection, start: date, end: date) -> int:
    df = transform_mid(fetch_mid(start, end))
    df["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "price_mid", df)
