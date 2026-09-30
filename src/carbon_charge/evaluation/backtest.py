"""Rolling-origin backtest. Never a random split.

The model is refit every `refit_days`. Each fold trains only on target days whose
labels were complete before the fold's first forecast was issued, then forecasts the
next `refit_days` days with that frozen model.

Two targets: carbon intensity, and the realised Market Index price. The price model predicts
the *basis* (realised minus day-ahead price) and adds it back to the known day-ahead price.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import pandas as pd

from carbon_charge import config, db
from carbon_charge.features.build import FeatureSet, build
from carbon_charge.models import baselines, lgbm

PRICE_SOURCES = ("price_day_ahead", "price_mid")


@dataclass(frozen=True)
class ModelSpec:
    name: str
    target: str  # "ci" or "price"
    exclude_sources: tuple[str, ...] = ()
    params: tuple = ()  # LightGBM overrides as ((key, value), ...)


# Layer 1 models exclude every price feature, so their forecasts do not change in Layer 2.
MODELS = [
    ModelSpec("lgbm", "ci", PRICE_SOURCES),
    ModelSpec("lgbm_noweather", "ci", ("weather_forecast", *PRICE_SOURCES)),
    ModelSpec("lgbm_price", "ci"),
    ModelSpec("price_lgbm", "price", params=tuple(lgbm.PRICE_PARAMS.items())),
]

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


def _long(s: pd.Series, model: str, col: str) -> pd.DataFrame:
    return s.dropna().rename(col).rename_axis("ts_utc").reset_index().assign(model=model)


def run(
    con: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    refit_days: int = 14,
    history_start: date = config.HISTORY_START,
    fs: FeatureSet | None = None,
    models: list[ModelSpec] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (carbon forecasts, price forecasts, folds). Forecast frames are long: ts_utc/model/value."""
    fs = fs or build(con, history_start, end)
    models = models or MODELS
    day = fs.meta["settlement_date"].dt.date
    ci_out: list[pd.DataFrame] = []
    price_out: list[pd.DataFrame] = []
    fold_rows = []
    # Price target: basis = realised - day-ahead. Rows without a day-ahead price cannot be used.
    basis = fs.y_price - fs.X["da_price"]

    for fold in make_folds(start, end, refit_days):
        in_train = (day <= fold.train_end).to_numpy()
        test_mask = ((day >= fold.test_start) & (day <= fold.test_end)).to_numpy()
        n_train = {}
        for spec in models:
            y = fs.y if spec.target == "ci" else basis
            train_mask = in_train & y.notna().to_numpy()
            if train_mask.sum() < MIN_TRAIN_ROWS:
                raise ValueError(f"fold {fold}: only {train_mask.sum()} training rows for {spec.name}")
            cols = lgbm.feature_columns(fs.sources, spec.exclude_sources)
            booster = lgbm.fit(fs.X.loc[train_mask, cols], y[train_mask], params=dict(spec.params))
            pred = lgbm.predict(booster, fs.X.loc[test_mask, cols], clip_low=0 if spec.target == "ci" else None)
            if spec.target == "ci":
                ci_out.append(_long(pred, spec.name, "forecast_gco2_kwh"))
            else:
                price_out.append(_long(pred + fs.X.loc[test_mask, "da_price"], spec.name, "forecast_gbp_mwh"))
            n_train[spec.name] = int(train_mask.sum())
        fold_rows.append({**fold.__dict__, "n_train": n_train.get("lgbm", max(n_train.values())),
                          "n_test": int(test_mask.sum())})

    in_test = ((day >= start) & (day <= end)).to_numpy()
    ci_base = {
        "yesterday": baselines.yesterday(fs),
        "last_week": baselines.last_week(fs),
        "neso_logged": baselines.neso_logged(con, fs),
        "neso_latest_revision": baselines.neso_latest_revision(con, fs),
    }
    price_base = {
        "price_da": baselines.price_day_ahead(fs),
        "price_yesterday": baselines.price_yesterday(fs),
        "price_last_week": baselines.price_last_week(fs),
        "price_da_basis7d": baselines.price_da_plus_basis(fs),
    }
    ci_out += [_long(s[in_test], n, "forecast_gco2_kwh") for n, s in ci_base.items()]
    price_out += [_long(s[in_test], n, "forecast_gbp_mwh") for n, s in price_base.items()]

    ci = pd.concat(ci_out, ignore_index=True)[["ts_utc", "model", "forecast_gco2_kwh"]]
    pr = pd.concat(price_out, ignore_index=True)[["ts_utc", "model", "forecast_gbp_mwh"]]
    return ci, pr, pd.DataFrame(fold_rows)


def save(con: duckdb.DuckDBPyConnection, ci: pd.DataFrame, price: pd.DataFrame) -> int:
    con.execute("DELETE FROM backtest_forecast")
    con.execute("DELETE FROM backtest_price_forecast")
    return db.upsert(con, "backtest_forecast", ci) + db.upsert(con, "backtest_price_forecast", price)
