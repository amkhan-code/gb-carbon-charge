"""LightGBM day-ahead model. Uses the native API (no scikit-learn dependency)."""

import lightgbm as lgb
import numpy as np
import pandas as pd

PARAMS = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}
NUM_ROUNDS = 400


def feature_columns(sources: dict[str, str], exclude_sources: tuple[str, ...] = ()) -> list[str]:
    return [f for f, s in sources.items() if s not in exclude_sources]


def fit(X: pd.DataFrame, y: pd.Series, params: dict | None = None, rounds: int = NUM_ROUNDS) -> lgb.Booster:
    return lgb.train({**PARAMS, **(params or {})}, lgb.Dataset(X, y), num_boost_round=rounds)


def predict(model: lgb.Booster, X: pd.DataFrame, clip_low: float | None = 0) -> pd.Series:
    # Carbon intensity cannot be negative; prices can, so they pass clip_low=None.
    pred = model.predict(X)
    return pd.Series(pred if clip_low is None else np.clip(pred, clip_low, None), index=X.index)


# The realised price has heavy spikes; the median (L1) objective is robust to them and matches
# the MAE we report. Chosen before looking at test-period results.
PRICE_PARAMS = {"objective": "regression_l1"}
