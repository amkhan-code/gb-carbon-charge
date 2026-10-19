"""A small results database built by the project's own pipeline code from its committed results."""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from carbon_charge import catalog, db, results_store
from carbon_charge.timeutils import add_settlement_columns, local_midnight_utc, periods_in_day

REPO = Path(__file__).resolve().parents[2]


# Days with data. They span both clock changes (2025-03-30 has 46 periods, 2025-10-26 has 50) and are long enough
# for every night whose window crosses them. 2026-09-29 is the last day of the real window.
SPRING = pd.date_range("2025-03-27", "2025-04-02").date
AUTUMN = pd.date_range("2025-10-23", "2025-10-28").date
LAST_DAY = [pd.Timestamp("2026-09-29").date()]
DAYS = [*SPRING, *AUTUMN, *LAST_DAY]
# Forecast = actual + offset, so every score the tests expect can be computed by hand.
CARBON_OFFSETS = {"lgbm_price": 0.0, "lgbm": "wave8", "yesterday": "wave30", "last_week": -20.0, "lgbm_noweather": 5.0}
PRICE_OFFSETS = {"price_lgbm": 1.5, "price_da": 5.0, "price_da_basis7d": "wave10", "price_last_week": -8.0, "price_yesterday": 3.0}
NO_PRICE = {"night": pd.Timestamp("2025-04-01").date(), "slots": 4}  # price missing here: an incomplete cost night


def offset(spec, i):
    if isinstance(spec, str) and spec.startswith("wave"):
        return float(spec[4:]) * np.sin(i * 1.3)
    return float(spec)


def build_results_db(path: Path, with_neso: bool = False) -> Path:
    con = db.connect(path)
    ts = pd.DatetimeIndex(sorted({t for d in DAYS for t in pd.date_range(
        local_midnight_utc(d).replace(tzinfo=None), periods=periods_in_day(d), freq="30min")}))
    i = np.arange(len(ts))
    hour = ts.hour + ts.minute / 60
    actual_ci = 150 + 60 * np.sin(2 * np.pi * hour / 24)
    actual_price = 80 + 30 * np.cos(2 * np.pi * hour / 24)
    ci = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    ci["actual_gco2_kwh"], ci["neso_forecast_latest_gco2_kwh"], ci["fetched_at_utc"] = actual_ci, actual_ci - 2, pd.Timestamp("2026-10-01")
    db.upsert(con, "ci_history", ci)
    pm = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    pm["price_gbp_mwh"], pm["volume_mwh"], pm["fetched_at_utc"] = actual_price, 1000.0, pd.Timestamp("2026-10-01")
    gap_start = local_midnight_utc(NO_PRICE["night"]).replace(tzinfo=None) + timedelta(hours=18)
    pm.loc[pm["ts_utc"].between(gap_start, gap_start + timedelta(minutes=30 * (NO_PRICE["slots"] - 1))), "price_gbp_mwh"] = np.nan
    db.upsert(con, "price_mid", pm)

    def forecasts(offsets, base, col):
        rows = []
        for name, spec in offsets.items():
            rows.append(pd.DataFrame({"ts_utc": ts, "model": name, col: base + np.array([offset(spec, k) for k in i])}))
        return pd.concat(rows)

    carbon = forecasts(CARBON_OFFSETS, actual_ci, "forecast_gco2_kwh")
    if with_neso:
        carbon = pd.concat([carbon, forecasts({"neso_logged": 3.0}, actual_ci, "forecast_gco2_kwh").head(6)])
    db.upsert(con, "backtest_forecast", carbon)
    db.upsert(con, "backtest_price_forecast", forecasts(PRICE_OFFSETS, actual_price, "forecast_gbp_mwh"))
    results_store.persist(con, REPO / "reports", REPO / "DATA_ISSUES.md")
    con.close()
    return path


@pytest.fixture(scope="session")
def results_db(tmp_path_factory) -> Path:
    return build_results_db(tmp_path_factory.mktemp("results") / "results.duckdb")


@pytest.fixture(scope="session")
def results_db_with_neso(tmp_path_factory) -> Path:
    return build_results_db(tmp_path_factory.mktemp("results_neso") / "results.duckdb", with_neso=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"
