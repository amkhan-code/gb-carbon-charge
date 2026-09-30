"""Rolling-origin backtest. Never a random split.

The model is refit every `refit_days`. Each fold trains only on target days whose
labels were complete before the fold's first forecast was issued, then forecasts the
next `refit_days` days with that frozen model.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.features.build import FeatureSet, build
from carbon_charge.models import baselines, lgbm

# Models whose training set excludes these feature sources.
MODEL_EXCLUDES = {"lgbm": (), "lgbm_noweather": ("weather_forecast",)}

# Labels for day D_train are complete at D_train+1 00:00 (+ publication lag); the first
# forecast of a fold is issued at 11:00 on (test_start - 1). So the last usable training
# day is test_start - 2.
TRAIN_GAP_DAYS = 2
MIN_TRAIN_ROWS = 5000


@dataclass(frozen=True)
class Fold:
    test_start: date
    test_end: date
    train_end: date  # last target day used for training


def make_folds(start: date, end: date, refit_days: int) -> list[Fold]:
    folds, a = [], start
    while a <= end:
        b = min(a + timedelta(days=refit_days - 1), end)
        folds.append(Fold(a, b, a - timedelta(days=TRAIN_GAP_DAYS)))
        a = b + timedelta(days=1)
    return folds


def run(
    con: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    refit_days: int = 14,
    history_start: date = config.HISTORY_START,
    fs: FeatureSet | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (forecasts long: ts_utc/model/forecast_gco2_kwh, folds table)."""
    fs = fs or build(con, history_start, end)
    m = fs.meta
    day = m["settlement_date"].dt.date
    out: list[pd.DataFrame] = []
    fold_rows = []

    for fold in make_folds(start, end, refit_days):
        train_mask = (day <= fold.train_end).to_numpy() & fs.y.notna().to_numpy()
        test_mask = ((day >= fold.test_start) & (day <= fold.test_end)).to_numpy()
        if train_mask.sum() < MIN_TRAIN_ROWS:
            raise ValueError(f"fold {fold}: only {train_mask.sum()} training rows")
        for name, excl in MODEL_EXCLUDES.items():
            cols = lgbm.feature_columns(fs.sources, excl)
            booster = lgbm.fit(fs.X.loc[train_mask, cols], fs.y[train_mask])
            pred = lgbm.predict(booster, fs.X.loc[test_mask, cols])
            out.append(pred.rename("forecast_gco2_kwh").rename_axis("ts_utc").reset_index().assign(model=name))
        fold_rows.append({**fold.__dict__, "n_train": int(train_mask.sum()), "n_test": int(test_mask.sum())})

    base = {
        "yesterday": baselines.yesterday(fs),
        "last_week": baselines.last_week(fs),
        "neso_logged": baselines.neso_logged(con, fs),
        "neso_latest_revision": baselines.neso_latest_revision(con, fs),
    }
    in_test = ((day >= start) & (day <= end)).to_numpy()
    for name, s in base.items():
        s = s[in_test].dropna()
        out.append(s.rename("forecast_gco2_kwh").rename_axis("ts_utc").reset_index().assign(model=name))

    forecasts = pd.concat(out, ignore_index=True)[["ts_utc", "model", "forecast_gco2_kwh"]]
    return forecasts, pd.DataFrame(fold_rows)


def save(con: duckdb.DuckDBPyConnection, forecasts: pd.DataFrame) -> int:
    con.execute("DELETE FROM backtest_forecast")
    return db.upsert(con, "backtest_forecast", forecasts)
