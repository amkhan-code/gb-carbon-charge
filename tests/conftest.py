"""A small synthetic database spanning the 2024-03-31 clock change."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from carbon_charge import config, db
from carbon_charge.features import availability as av
from carbon_charge.timeutils import add_settlement_columns, local_midnight_utc

START, END = date(2024, 2, 1), date(2024, 4, 20)


def _ts(start: date, end: date, freq: str) -> pd.DatetimeIndex:
    lo = local_midnight_utc(start).replace(tzinfo=None)
    hi = local_midnight_utc(end + timedelta(days=1)).replace(tzinfo=None)
    return pd.date_range(lo, hi, freq=freq, inclusive="left")


def populate_prices(con, rng) -> None:
    hours = _ts(START, END, "60min")
    da = add_settlement_columns(pd.DataFrame({"ts_utc": hours}))
    hr = hours.hour
    da["price_gbp_mwh"] = 80 + 30 * np.sin(2 * np.pi * (hr - 6) / 24) + rng.normal(0, 8, len(hours))
    da["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "price_day_ahead", da)

    ts = _ts(START, END, "30min")
    mid = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    base = pd.Series(da["price_gbp_mwh"].to_numpy(), index=hours).reindex(ts.floor("h")).to_numpy()
    mid["price_gbp_mwh"] = base + rng.normal(0, 12, len(ts))
    mid["volume_mwh"] = 1000.0
    mid["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "price_mid", mid)


def populate_wind(con, rng) -> None:
    ts = _ts(START, END, "30min")
    w = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    cut = av.cutoffs(pd.to_datetime(w["settlement_date"]).dt.date)
    w["issued_at_utc"] = cut - pd.Timedelta(hours=2)
    w["capacity_mw"] = 20000.0
    w["wind_forecast_mw"] = rng.uniform(1000, 15000, len(ts))
    w["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "wind_forecast", w)


def populate(con) -> None:
    rng = np.random.default_rng(0)
    ts = _ts(START, END, "30min")
    ci = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    hour = ts.hour + ts.minute / 60
    ci["actual_gco2_kwh"] = 150 + 60 * np.sin(2 * np.pi * hour / 24) + rng.normal(0, 10, len(ts))
    ci["neso_forecast_latest_gco2_kwh"] = ci["actual_gco2_kwh"] + 3
    ci["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "ci_history", ci)

    dem = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    cut = av.cutoffs(pd.to_datetime(dem["settlement_date"]).dt.date)
    dem["issued_at_utc"] = cut - pd.Timedelta(hours=2)  # published comfortably before the cutoff
    dem["demand_forecast_mw"] = 30000 + 5000 * np.sin(2 * np.pi * hour / 24) + rng.normal(0, 300, len(ts))
    dem["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "demand_forecast", dem)

    hours = _ts(START, END, "60min")
    frames = []
    for loc in config.WEATHER_LOCATIONS:
        for lead in (1, 2):
            w = pd.DataFrame({"ts_utc": hours, "location": loc.name, "lead_days": lead})
            for col in config.WEATHER_VARIABLES.values():
                w[col] = rng.uniform(0, 20, len(hours))
            w["issued_at_utc"] = w["ts_utc"] - pd.Timedelta(days=lead)
            frames.append(w)
    w = add_settlement_columns(pd.concat(frames, ignore_index=True))
    w["model"] = config.WEATHER_MODEL
    w["fetched_at_utc"] = pd.Timestamp("2024-06-01")
    db.upsert(con, "weather_forecast", w)
    populate_prices(con, rng)
    populate_wind(con, rng)


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "test.duckdb")
    populate(c)
    yield c
    c.close()
