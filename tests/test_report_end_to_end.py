"""Smoke test: backtest -> evaluate -> optimise -> render on the small synthetic database."""

from datetime import date

import pytest

from carbon_charge import config, figures, report
from carbon_charge.evaluation import backtest
from carbon_charge.models import lgbm


@pytest.fixture()
def reports(con, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(backtest, "MIN_TRAIN_ROWS", 100)
    real_fit = lgbm.fit
    monkeypatch.setattr(lgbm, "fit", lambda X, y, **k: real_fit(X, y, rounds=10, **k))
    ci, price, folds = backtest.run(con, date(2024, 3, 25), date(2024, 4, 15), 7)
    backtest.save(con, ci, price)
    config.REPORTS_DIR.mkdir(parents=True)
    folds.to_csv(config.REPORTS_DIR / "backtest_folds.csv", index=False)
    tables = {**report.evaluate(con), **report.optimise(con)}
    report.write_csvs(tables)
    return tables


def test_price_models_and_baselines_are_all_scored(reports):
    scored = set(reports["price_overall"]["model"])
    assert scored == set(report.PRICE_CORE)
    assert reports["price_overall"]["n"].nunique() == 1  # same half-hours for every model


def test_day_ahead_baseline_is_exactly_the_da_price(con, reports):
    fc = con.execute("SELECT ts_utc, forecast_gbp_mwh f FROM backtest_price_forecast WHERE model='price_da'").df()
    da = con.execute("SELECT ts_utc, price_gbp_mwh p FROM price_day_ahead").df().set_index("ts_utc")["p"]
    assert (fc["f"].to_numpy() == da.reindex(fc["ts_utc"].dt.floor("h")).to_numpy()).all()


def test_layer1_models_unchanged_by_price_features(con):
    from carbon_charge.features.build import build
    fs = build(con, date(2024, 2, 1), date(2024, 4, 15))
    for spec in backtest.MODELS:
        if spec.name in ("lgbm", "lgbm_noweather"):
            cols = lgbm.feature_columns(fs.sources, spec.exclude_sources)
            assert cols and not [c for c in cols if c.startswith(("da_", "mid_", "basis_", "wind_fc"))], spec.name
        assert not [c for c in lgbm.feature_columns(fs.sources, spec.exclude_sources) if c.startswith("wind_fc")], spec.name


def test_blend_frontier_and_render(reports):
    fr = reports["optimiser_blend_frontier"]
    assert set(fr["strategy"]) >= {"arrival", "timer", "perfect", "model", "day_ahead", "da_basis", "naive"}
    perfect_gap = fr[fr["strategy"] == "perfect"]["gap_captured"]
    assert (perfect_gap.round(6) == 1.0).all()
    text = report.render()
    assert "# Layer 2: wholesale prices" in text and "Cost/carbon trade-off" in text
    assert "carbon only" in text


def test_charts_are_written_and_embedded(con, reports):
    names = figures.make_all(con)
    assert {"gap_captured.png", "carbon_accuracy.png", "price_accuracy.png", "cost_carbon_frontier.png"} <= set(names)
    for n in names:
        assert (config.REPORTS_DIR / "figures" / n).stat().st_size > 5000
    text = report.render()
    assert "figures/gap_captured.png" in text and "figures/cost_carbon_frontier.png" in text


def test_experiment_harness_returns_paired_comparisons_on_synthetic_data(con, monkeypatch):
    from carbon_charge.evaluation import experiment

    monkeypatch.setattr(backtest, "MIN_TRAIN_ROWS", 100)
    real_fit = lgbm.fit
    monkeypatch.setattr(lgbm, "fit", lambda X, y, **k: real_fit(X, y, rounds=10, **k))
    out = experiment.run(con, date(2024, 3, 25), date(2024, 4, 15), 7)
    assert set(out["accuracy"]["model"]) == {"lgbm_price", "lgbm_wind", "lgbm_night", "lgbm_both"}
    assert out["accuracy"]["n"].nunique() == 1  # same rows for every candidate
    assert set(out["paired_gap"]["model"]) == {"lgbm_wind", "lgbm_night", "lgbm_both"}
    assert (out["paired_gap"]["vs"] == "lgbm_price").all()
    assert out["accuracy_by_wind_availability"]["wind_available"].all()  # synthetic wind is always present
    assert (out["paired_gap"]["ci_low"] <= out["paired_gap"]["ci_high"]).all()
