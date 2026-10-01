"""NESO national day-ahead wind forecast (history file with per-forecast issue times)."""

from datetime import date, datetime

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.ingest import http
from carbon_charge.timeutils import UTC, period_start_utc_series, to_naive_utc

PAGE = 32000


def fetch(since: date) -> pd.DataFrame:
    rows, offset = [], 0
    rid = config.NESO_WIND_HISTORY_RESOURCE_ID
    while True:
        sql = f'SELECT * FROM "{rid}" WHERE "Date" >= \'{since}\' ORDER BY "_id" LIMIT {PAGE} OFFSET {offset}'
        recs = http.get(f"{config.NESO_CKAN_URL}/datastore_search_sql", params={"sql": sql}).json()["result"]["records"]
        rows += recs
        if len(recs) < PAGE:
            break
        offset += PAGE
    return pd.DataFrame(rows)


def transform(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean rows, rejected raw rows).

    The UTC key comes from Datetime_GMT (checked against settlement date + period; rows that
    disagree or have an impossible period are rejected). The issue time is Forecast_Timestamp,
    read as UTC: the conservative reading for a cutoff rule.
    """
    df = raw.copy()
    df["settlement_period"] = pd.to_numeric(df["Settlement_period"])
    df["ts_utc"] = to_naive_utc(df["Datetime_GMT"])
    df["issued_at_utc"] = to_naive_utc(df["Forecast_Timestamp"])
    df["capacity_mw"] = pd.to_numeric(df["Capacity"], errors="coerce")
    df["wind_forecast_mw"] = pd.to_numeric(df["Incentive_forecast"], errors="coerce")
    expected = period_start_utc_series(df["Date"], df["settlement_period"])
    bad = (expected != df["ts_utc"]) | expected.isna() | df["issued_at_utc"].isna() | df["wind_forecast_mw"].isna()
    df["settlement_date"] = pd.to_datetime(df["Date"]).dt.date
    clean = df.loc[~bad].drop_duplicates(["ts_utc", "issued_at_utc"], keep="last")
    cols = ["ts_utc", "issued_at_utc", "settlement_date", "settlement_period", "capacity_mw", "wind_forecast_mw"]
    return clean[cols], raw.loc[bad]


def ingest(con: duckdb.DuckDBPyConnection, since: date = config.HISTORY_START) -> tuple[int, pd.DataFrame]:
    clean, rejected = transform(fetch(since))
    clean = clean.copy()
    clean["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "wind_forecast", clean), rejected


WIND_LOG_COLUMNS = ["ts_utc", "issued_at_utc", "settlement_date", "settlement_period", "capacity_mw", "wind_forecast_mw"]


def fetch_live() -> pd.DataFrame:
    """Tomorrow's forecast from the live file, stamped with NESO's own last-modified time.

    The file has no per-row issue time. The resource's `last_modified` is when NESO refreshed it, so
    it is the right issue time even if our job runs late; a later intraday refresh gets a later stamp
    and is (correctly) rejected by the cutoff gate.
    """
    rid = config.NESO_WIND_LIVE_RESOURCE_ID
    meta = http.get(f"{config.NESO_CKAN_URL}/resource_show", params={"id": rid}).json()["result"]
    recs = http.get(f"{config.NESO_CKAN_URL}/datastore_search", params={"resource_id": rid, "limit": 500}).json()["result"]["records"]
    raw = pd.DataFrame(recs)
    raw["Forecast_Timestamp"] = meta["last_modified"]
    clean, _ = transform(raw)
    return clean[WIND_LOG_COLUMNS]


def write_live_csv(out_dir) -> "Path":
    from pathlib import Path

    df = fetch_live()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = df["issued_at_utc"].iloc[0].strftime("%Y%m%dT%H%MZ")
    path = out_dir / f"wind_forecast_{stamp}.csv"
    df.to_csv(path, index=False)
    return path


def import_csvs(con: duckdb.DuckDBPyConnection, csv_dir) -> int:
    from pathlib import Path

    frames = [pd.read_csv(f, parse_dates=["ts_utc", "issued_at_utc"]) for f in sorted(Path(csv_dir).rglob("wind_forecast_*.csv"))]
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    df["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "wind_forecast", df)
