"""NESO Carbon Intensity API: national half-hourly actuals and forecasts."""

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.ingest import http
from carbon_charge.timeutils import HALF_HOUR, UTC, add_settlement_columns, to_naive_utc

FORECAST_LOG_COLUMNS = [
    "ts_utc", "issued_at_utc", "settlement_date", "settlement_period", "forecast_gco2_kwh",
]

# The API rejects ranges over 31 days.
CHUNK = timedelta(days=30)


def _fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%MZ")


def _parse(payload: dict) -> pd.DataFrame:
    # Some records omit the "forecast" key entirely (see DATA_ISSUES.md).
    rows = [
        (r["from"], r["intensity"].get("actual"), r["intensity"].get("forecast"))
        for r in payload["data"]
    ]
    df = pd.DataFrame(rows, columns=["ts_utc", "actual", "forecast"])
    df["ts_utc"] = to_naive_utc(df["ts_utc"])
    return df


def fetch_history(start: datetime, end: datetime) -> pd.DataFrame:
    """Periods starting in [start, end), both UTC."""
    frames = []
    a = start
    while a < end:
        b = min(a + CHUNK, end)
        # The API returns periods whose END lies in [from, to].
        resp = http.get(f"{config.CARBON_INTENSITY_URL}/intensity/{_fmt(a + HALF_HOUR)}/{_fmt(b)}")
        frames.append(_parse(resp.json()))
        a = b
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ts_utc")
    return df[(df["ts_utc"] >= start.replace(tzinfo=None)) & (df["ts_utc"] < end.replace(tzinfo=None))]


def ingest_history(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> int:
    df = fetch_history(start, end).rename(
        columns={"actual": "actual_gco2_kwh", "forecast": "neso_forecast_latest_gco2_kwh"}
    )
    df = add_settlement_columns(df)
    df["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
    return db.upsert(con, "ci_history", df)


def fetch_forward_forecast() -> pd.DataFrame:
    """Snapshot the API's forward 48h forecast, stamped with the time it was taken."""
    now = datetime.now(UTC).replace(microsecond=0)
    resp = http.get(f"{config.CARBON_INTENSITY_URL}/intensity/{_fmt(now)}/fw48h")
    df = _parse(resp.json())
    issued = now.replace(tzinfo=None)
    # Keep only periods that have not started yet.
    df = df[df["ts_utc"] > issued].rename(columns={"forecast": "forecast_gco2_kwh"})
    df = add_settlement_columns(df.drop(columns="actual"))
    df["issued_at_utc"] = issued
    return df[FORECAST_LOG_COLUMNS]


def log_forward_forecast(con: duckdb.DuckDBPyConnection) -> int:
    return db.upsert(con, "ci_forecast_log", fetch_forward_forecast())


def write_forward_forecast_csv(out_dir: Path) -> Path:
    """Write one snapshot to `out_dir` as ci_forecast_<issue time>.csv, for the data branch."""
    df = fetch_forward_forecast()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = df["issued_at_utc"].iloc[0].strftime("%Y%m%dT%H%MZ")
    path = out_dir / f"ci_forecast_{stamp}.csv"
    df.to_csv(path, index=False)
    return path


def import_forecast_csvs(con: duckdb.DuckDBPyConnection, csv_dir: Path) -> int:
    """Load snapshot CSVs (e.g. a checkout of the data branch) into ci_forecast_log."""
    frames = [
        pd.read_csv(f, parse_dates=["ts_utc", "issued_at_utc"])
        for f in sorted(csv_dir.rglob("ci_forecast_*.csv"))
    ]
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
    return db.upsert(con, "ci_forecast_log", df[FORECAST_LOG_COLUMNS])
