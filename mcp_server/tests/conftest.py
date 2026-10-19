"""A small results database built by the project's own pipeline code from its committed results."""

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from carbon_charge import catalog, db, results_store
from carbon_charge.timeutils import add_settlement_columns, local_midnight_utc

REPO = Path(__file__).resolve().parents[2]


def build_results_db(path: Path, with_neso: bool = False) -> Path:
    con = db.connect(path)
    # One complete settlement day of actuals (2026-09-29, 48 half-hours) so `data_through` is defined.
    day = pd.Timestamp("2026-09-29").date()
    ts = pd.date_range(local_midnight_utc(day).replace(tzinfo=None), periods=48, freq="30min")
    ci = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
    ci["actual_gco2_kwh"], ci["neso_forecast_latest_gco2_kwh"], ci["fetched_at_utc"] = 120.0, 118.0, pd.Timestamp("2026-10-01")
    db.upsert(con, "ci_history", ci)
    # A few stored forecasts per arm, so coverage can be reported.
    start = pd.Timestamp("2025-01-01 00:00")
    for table, col, target in (("backtest_forecast", "forecast_gco2_kwh", "carbon"),
                               ("backtest_price_forecast", "forecast_gbp_mwh", "price")):
        names = [a.name for a in catalog.arms() if a.target == target and a.kind in
                 ("carbon_model", "price_model", "baseline", "reference")]
        if target == "carbon" and not with_neso:
            names = [n for n in names if n != "neso_logged"]
        rows = [{"ts_utc": start + timedelta(minutes=30 * i), "model": n, col: 100.0 + i}
                for n in names for i in range(6)]
        db.upsert(con, table, pd.DataFrame(rows))
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
