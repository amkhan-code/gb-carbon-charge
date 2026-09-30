"""DuckDB connection, schema, and idempotent upserts.

All timestamps are stored as naive TIMESTAMP holding UTC; columns are suffixed _utc.
"""

from pathlib import Path

import duckdb
import pandas as pd

from carbon_charge import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS ci_history (
    ts_utc TIMESTAMP PRIMARY KEY,
    settlement_date DATE NOT NULL,
    settlement_period SMALLINT NOT NULL,
    actual_gco2_kwh DOUBLE,
    -- The API's historical "forecast" field. NOT day-ahead: it is the last
    -- revision before delivery and its issue time is unknown. Never a feature.
    neso_forecast_latest_gco2_kwh DOUBLE,
    fetched_at_utc TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ci_forecast_log (
    ts_utc TIMESTAMP NOT NULL,
    issued_at_utc TIMESTAMP NOT NULL,  -- when the snapshot was taken
    settlement_date DATE NOT NULL,
    settlement_period SMALLINT NOT NULL,
    forecast_gco2_kwh DOUBLE,
    PRIMARY KEY (ts_utc, issued_at_utc)
);

CREATE TABLE IF NOT EXISTS demand_forecast (
    ts_utc TIMESTAMP NOT NULL,
    issued_at_utc TIMESTAMP NOT NULL,
    settlement_date DATE NOT NULL,
    settlement_period SMALLINT NOT NULL,
    demand_forecast_mw DOUBLE,
    fetched_at_utc TIMESTAMP NOT NULL,
    PRIMARY KEY (ts_utc, issued_at_utc)
);

CREATE TABLE IF NOT EXISTS weather_forecast (
    ts_utc TIMESTAMP NOT NULL,  -- hourly
    location VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    lead_days SMALLINT NOT NULL,
    -- Latest possible model run initialisation time (ts_utc - lead_days).
    -- The run becomes available some hours later; features must add that lag.
    issued_at_utc TIMESTAMP NOT NULL,
    settlement_date DATE NOT NULL,
    settlement_period SMALLINT NOT NULL,
    temperature_2m_c DOUBLE,
    wind_speed_10m_ms DOUBLE,
    wind_speed_100m_ms DOUBLE,
    shortwave_radiation_wm2 DOUBLE,
    cloud_cover_pct DOUBLE,
    fetched_at_utc TIMESTAMP NOT NULL,
    PRIMARY KEY (ts_utc, location, model, lead_days)
);

-- Out-of-sample day-ahead forecasts from the rolling-origin backtest (and baselines).
CREATE TABLE IF NOT EXISTS backtest_forecast (
    ts_utc TIMESTAMP NOT NULL,
    model VARCHAR NOT NULL,
    forecast_gco2_kwh DOUBLE,
    PRIMARY KEY (ts_utc, model)
);
"""


def connect(path: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = path or config.DB_PATH
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def upsert(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Insert rows, replacing any with the same primary key. Returns rows written."""
    if df.empty:
        return 0
    con.register("_incoming", df)
    try:
        con.execute(f"INSERT OR REPLACE INTO {table} BY NAME SELECT * FROM _incoming")
    finally:
        con.unregister("_incoming")
    return len(df)
