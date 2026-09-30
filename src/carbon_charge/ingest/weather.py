"""Open-Meteo archived weather forecasts, as issued.

Uses the Previous Runs API: `<variable>_previous_dayN` is the value forecast
for a given hour by a model run initialised at least N*24h before that hour.
The Historical Forecast API is not used because it stitches together the first
hours of each run (near-zero lead time) and carries no issue time.
"""

from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.ingest import http
from carbon_charge.timeutils import UTC, add_settlement_columns

CHUNK = timedelta(days=60)


def _fetch_chunk(start: date, end: date) -> list[dict]:
    hourly = [
        f"{var}_previous_day{n}" for var in config.WEATHER_VARIABLES for n in config.WEATHER_LEAD_DAYS
    ]
    params = {
        "latitude": ",".join(str(loc.latitude) for loc in config.WEATHER_LOCATIONS),
        "longitude": ",".join(str(loc.longitude) for loc in config.WEATHER_LOCATIONS),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(hourly),
        "models": config.WEATHER_MODEL,
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    payload = http.get(config.OPEN_METEO_PREVIOUS_RUNS_URL, params=params).json()
    return payload if isinstance(payload, list) else [payload]


def _to_frame(payloads: list[dict]) -> pd.DataFrame:
    frames = []
    for loc, payload in zip(config.WEATHER_LOCATIONS, payloads, strict=True):
        hourly = payload["hourly"]
        ts = pd.to_datetime(hourly["time"])
        for n in config.WEATHER_LEAD_DAYS:
            df = pd.DataFrame({"ts_utc": ts, "location": loc.name, "lead_days": n})
            for var, col in config.WEATHER_VARIABLES.items():
                df[col] = pd.array(hourly[f"{var}_previous_day{n}"], dtype="float64")
            df["issued_at_utc"] = df["ts_utc"] - pd.Timedelta(days=n)
            frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    return df.dropna(subset=list(config.WEATHER_VARIABLES.values()), how="all")


def ingest(con: duckdb.DuckDBPyConnection, start: date, end: date) -> int:
    """Forecasts for target days in [start, end]."""
    written = 0
    a = start
    while a <= end:
        b = min(a + CHUNK, end)
        df = _to_frame(_fetch_chunk(a, b))
        if not df.empty:
            df = add_settlement_columns(df)
            df["model"] = config.WEATHER_MODEL
            df["fetched_at_utc"] = datetime.now(UTC).replace(tzinfo=None)
            written += db.upsert(con, "weather_forecast", df)
        a = b + timedelta(days=1)
    return written
