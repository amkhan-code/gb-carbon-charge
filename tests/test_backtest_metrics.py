from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from carbon_charge.evaluation import backtest, metrics, ranking
from carbon_charge.features.build import build
from carbon_charge.models import baselines, lgbm


def test_folds_cover_test_period_in_order_with_training_strictly_earlier():
    folds = backtest.make_folds(date(2025, 1, 1), date(2025, 3, 20), 14)
    assert folds[0].test_start == date(2025, 1, 1) and folds[-1].test_end == date(2025, 3, 20)
    for prev, nxt in zip(folds, folds[1:]):
        assert nxt.test_start == prev.test_end + timedelta(days=1)
    for f in folds:
        # Labels of the training day must be complete before the first forecast of the fold.
        assert f.train_end <= f.test_start - timedelta(days=2)


def test_backtest_trains_only_on_the_past_and_never_on_test_rows(con, monkeypatch):
    seen = []
    real_fit = lgbm.fit
    monkeypatch.setattr(lgbm, "fit", lambda X, y, **k: (seen.append(X.index), real_fit(X, y, rounds=5, **k))[1])
    monkeypatch.setattr(backtest, "MIN_TRAIN_ROWS", 100)
    fs = build(con, date(2024, 2, 1), date(2024, 4, 20))
    forecasts, price_fc, folds = backtest.run(con, date(2024, 3, 25), date(2024, 4, 20), 7, fs=fs)
    n_models = len(backtest.MODELS)
    assert len(seen) == n_models * len(folds)
    for i, fold in enumerate(folds.itertuples()):
        cutoff_first = pd.Timestamp(fold.test_start) - pd.Timedelta(days=1)
        for idx in seen[n_models * i: n_models * (i + 1)]:
            train_days = fs.meta.loc[idx, "settlement_date"]
            assert train_days.max() <= pd.Timestamp(fold.train_end)
            assert train_days.max() < cutoff_first  # strictly before D-1: labels were complete
    lg = forecasts[forecasts["model"] == "lgbm"]
    days = fs.meta.loc[lg["ts_utc"], "settlement_date"]
    assert days.min() == pd.Timestamp(date(2024, 3, 25)) and days.max() == pd.Timestamp(date(2024, 4, 20))
    assert set(forecasts["model"]) >= {"lgbm", "lgbm_noweather", "yesterday", "last_week"}


def test_noweather_model_excludes_weather_features(con):
    fs = build(con, date(2024, 3, 1), date(2024, 3, 5))
    cols = lgbm.feature_columns(fs.sources, ("weather_forecast",))
    assert cols and not [c for c in cols if c.startswith("wx_")]


def test_baselines_respect_cutoff(con):
    fs = build(con, date(2024, 4, 1), date(2024, 4, 5))
    y, w = baselines.yesterday(fs), baselines.last_week(fs)
    hour = fs.meta["local_hour"]
    # Early periods use D-1; later ones fall back to D-2 (never the unpublished D-1 value).
    day = fs.meta["settlement_date"] == pd.Timestamp("2024-04-03")
    assert (y[day & (hour <= 9.5)] == fs.X.loc[day & (hour <= 9.5), "ci_lag_d1"]).all()
    assert (y[day & (hour > 9.5)] == fs.X.loc[day & (hour > 9.5), "ci_lag_d2"]).all()
    assert (w[day] == fs.X.loc[day, "ci_lag_d7"]).all()
    assert baselines.neso_logged(con, fs).isna().all()  # no logger snapshots yet


def test_neso_logged_uses_latest_snapshot_before_cutoff_only(con):
    from carbon_charge.timeutils import add_settlement_columns
    fs = build(con, date(2024, 4, 3), date(2024, 4, 3))
    ts = fs.meta.index
    early, late = pd.Timestamp("2024-04-02 06:00"), pd.Timestamp("2024-04-02 10:30")  # cutoff is 10:00 UTC (BST)
    rows = []
    for issued, val in ((early, 111.0), (late, 999.0)):
        d = add_settlement_columns(pd.DataFrame({"ts_utc": ts}))
        d["issued_at_utc"], d["forecast_gco2_kwh"] = issued, val
        rows.append(d)
    con.register("logs", pd.concat(rows))
    con.execute("INSERT INTO ci_forecast_log SELECT ts_utc, issued_at_utc, settlement_date, settlement_period, forecast_gco2_kwh FROM logs")
    assert (baselines.neso_logged(con, fs) == 111.0).all()


def _frame():
    ts = pd.date_range("2024-01-01", periods=4, freq="30min")
    f = pd.DataFrame({
        "ts_utc": list(ts) * 2, "model": ["a"] * 4 + ["b"] * 4,
        "forecast_gco2_kwh": [110, 90, 100, 100, 100, 100, np.nan, 100],
    })
    return metrics.eval_frame(f, pd.Series(100.0, index=ts))


def test_mae_rmse_and_common_sample():
    t = metrics.tables(_frame(), ["a", "b"])["overall"].set_index("model")
    # Common sample drops the timestamp where model b has no forecast.
    assert t.loc["a", "n"] == t.loc["b", "n"] == 3
    assert t.loc["a", "mae"] == pytest.approx(20 / 3, abs=0.01)
    assert t.loc["a", "rmse"] == pytest.approx(np.sqrt(200 / 3), abs=0.01)
    assert t.loc["b", "mae"] == 0


def test_seasons_and_time_of_day_buckets():
    df = metrics.eval_frame(
        pd.DataFrame({"ts_utc": pd.to_datetime(["2024-01-10 03:00", "2024-07-10 12:00"]),
                      "model": "a", "forecast_gco2_kwh": [1.0, 1.0]}),
        pd.Series({pd.Timestamp("2024-01-10 03:00"): 1.0, pd.Timestamp("2024-07-10 12:00"): 1.0}),
    )
    assert list(df["season"]) == ["DJF", "JJA"]
    assert list(df["tod_block"]) == ["00-04", "12-16"]  # 12:00 UTC in July is 13:00 BST
    assert list(df["local_hour"]) == [3, 13]


def test_ranking_overlap_perfect_reversed_and_random_baseline():
    night = date(2024, 1, 15)
    from carbon_charge.charging.strategies import window_slots
    from carbon_charge.charging.scenarios import DEFAULT
    slots = window_slots(night, DEFAULT)
    actual = pd.Series(np.arange(len(slots), dtype=float), index=slots)
    out = ranking.ranking_accuracy(actual, {"perfect": actual, "reversed": -actual}, [night]).set_index(["source", "k"])
    assert ranking.slots_needed(DEFAULT) == 3
    assert out.loc[("perfect", 3), "mean_overlap"] == 1.0
    assert out.loc[("reversed", 3), "mean_overlap"] == 0.0
    assert out.loc[("random", 3), "mean_overlap"] == pytest.approx(3 / 26)
