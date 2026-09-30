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


def predict(model: lgb.Booster, X: pd.DataFrame) -> pd.Series:
    # Carbon intensity cannot be negative.
    return pd.Series(np.clip(model.predict(X), 0, None), index=X.index)
