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
WIND_SOURCES = ("wind_forecast",)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    target: str  # "ci" or "price"
    exclude_sources: tuple[str, ...] = ()
    params: tuple = ()  # LightGBM overrides as ((key, value), ...)


# Layer 1 models exclude every price feature, so their forecasts do not change in Layer 2.
MODELS = [
    ModelSpec("lgbm", "ci", PRICE_SOURCES + WIND_SOURCES),
    ModelSpec("lgbm_noweather", "ci", ("weather_forecast", *PRICE_SOURCES, *WIND_SOURCES)),
    ModelSpec("lgbm_price", "ci", WIND_SOURCES),
    ModelSpec("price_lgbm", "price", WIND_SOURCES, params=tuple(lgbm.PRICE_PARAMS.items())),
]

# Pre-declared experiments (not part of the standard report). "ci_night" trains on each
# observation's deviation from its 18:00-07:00 night mean: all the charger needs is the order of
# slots within the night. Its forecast = mean of lgbm_price predictions over the night + predicted deviation.
EXPERIMENTS = [
    ModelSpec("lgbm_price", "ci", WIND_SOURCES),
    ModelSpec("lgbm_wind", "ci"),
    ModelSpec("lgbm_night", "ci_night", WIND_SOURCES),
    ModelSpec("lgbm_both", "ci_night"),
]
NIGHT_START_HOUR, NIGHT_END_HOUR = 18, 7
MIN_NIGHT_SLOTS = 24

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


def night_labels(fs: FeatureSet) -> tuple[pd.Series, pd.Series]:
    """(night id, deviation from the night's mean) for rows inside the 18:00-07:00 window.

    The night is identified by the settlement date on which it starts. Nights with any missing
    actual are dropped. Rows outside the window get NaT / NaN.
    """
    m = fs.meta
    h = m["local_hour"]
    in_window = (h >= NIGHT_START_HOUR) | (h < NIGHT_END_HOUR)
    night = m["settlement_date"].where(h >= NIGHT_START_HOUR, m["settlement_date"] - pd.Timedelta(days=1))
    night = night.where(in_window)
    g = fs.y.groupby(night)
    # A full window has 24 (spring change) to 28 (autumn change) half-hours; fewer means the history
    # ends mid-night, and a partial mean would be wrong.
    complete = (g.transform("size") == g.transform("count")) & (g.transform("size") >= MIN_NIGHT_SLOTS)
    mean = g.transform("mean")
    dev = (fs.y - mean).where(complete & in_window)
    return night, dev


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
    night, night_dev = night_labels(fs)
    night_pred: dict[str, list[pd.Series]] = {}

    for fold in make_folds(start, end, refit_days):
        in_train = (day <= fold.train_end).to_numpy()
        test_mask = ((day >= fold.test_start) & (day <= fold.test_end)).to_numpy()
        n_train = {}
        for spec in models:
            y = {"ci": fs.y, "price": basis, "ci_night": night_dev}[spec.target]
            train_mask = in_train & y.notna().to_numpy()
            if train_mask.sum() < MIN_TRAIN_ROWS:
                raise ValueError(f"fold {fold}: only {train_mask.sum()} training rows for {spec.name}")
            cols = lgbm.feature_columns(fs.sources, spec.exclude_sources)
            booster = lgbm.fit(fs.X.loc[train_mask, cols], y[train_mask], params=dict(spec.params))
            pred = lgbm.predict(booster, fs.X.loc[test_mask, cols], clip_low=0 if spec.target == "ci" else None)
            if spec.target == "ci_night":
                night_pred.setdefault(spec.name, []).append(pred[night[test_mask].notna()])
            elif spec.target == "ci":
                ci_out.append(_long(pred, spec.name, "forecast_gco2_kwh"))
            else:
                price_out.append(_long(pred + fs.X.loc[test_mask, "da_price"], spec.name, "forecast_gbp_mwh"))
            n_train[spec.name] = int(train_mask.sum())
        fold_rows.append({**fold.__dict__, "n_train": n_train.get("lgbm", max(n_train.values())),
                          "n_test": int(test_mask.sum())})

    for name, parts in night_pred.items():
        base_parts = [f for f in ci_out if (f["model"] == "lgbm_price").all()]
        if not base_parts:
            raise ValueError("a night-deviation model needs lgbm_price in the same run (it supplies the level)")
        base = pd.concat(base_parts).set_index("ts_utc")["forecast_gco2_kwh"]
        dev_hat = pd.concat(parts)
        nid = night.reindex(dev_hat.index)
        base_w = base.reindex(dev_hat.index)
        present = dev_hat.groupby(nid).transform("count")
        complete = (base_w.groupby(nid).transform("count") == present) & (present >= MIN_NIGHT_SLOTS)
        level = base_w.groupby(nid).transform("mean")
        # Re-centre the predicted deviations so the night's level comes only from the base model.
        shape = dev_hat - dev_hat.groupby(nid).transform("mean")
        ci_out.append(_long((level + shape).where(complete), name, "forecast_gco2_kwh"))

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
