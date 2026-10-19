from pathlib import Path

import pandas as pd
import pytest

from carbon_charge import catalog, config, results_store
from carbon_charge.evaluation import backtest
from carbon_charge.features.build import ALLOWED_SOURCES

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


# --- DATA_ISSUES.md parser -------------------------------------------------------

def test_parse_edge_cases():
    text = """# Title
## Section
### CI-1: the thing is NOT right  — FLAGGED
- Why: because
  it wraps over lines.
- Handling: stored as `x`; never
  used as a feature.
- Status: open
### X-9: no bullets at all
Just prose here.
## Next section
### ZZ-1: unknown prefix
- Handling: none
"""
    df = results_store.parse_data_issues(text).set_index("id")
    assert list(df.index) == ["CI-1", "X-9", "ZZ-1"]
    assert df.loc["CI-1", "flagged"] and df.loc["CI-1", "title"] == "the thing is NOT right"
    assert df.loc["CI-1", "handling"] == "stored as `x`; never used as a feature."  # continuation lines joined
    assert df.loc["CI-1", "status"] == "open"
    assert df.loc["CI-1", "source"] == "carbon_intensity" and df.loc["X-9", "source"] == "cross_source"
    assert df.loc["ZZ-1", "source"] == "other"
    assert pd.isna(df.loc["X-9", "handling"]) and df.loc["X-9", "body"] == "Just prose here."


def test_real_data_issues_file_parses_completely():
    df = results_store.parse_data_issues((ROOT / "DATA_ISSUES.md").read_text())
    headings = [ln for ln in (ROOT / "DATA_ISSUES.md").read_text().splitlines() if ln.startswith("### ")]
    assert len(df) == len(headings) >= 35  # nothing silently dropped
    assert df["id"].is_unique and df["title"].str.len().gt(0).all()
    assert {"CI-1", "DEM-3", "WX-3", "X-5", "PX-1", "WIND-1", "WIND-6", "ENV-1"} <= set(df["id"])
    assert set(df["source"]) <= set(results_store.ISSUE_SOURCES.values())
    assert df.set_index("id").loc["CI-1", "flagged"]
    assert not df["title"].str.contains("FLAGGED").any()


# --- arm catalog ----------------------------------------------------------------

def test_catalog_feature_use_matches_the_real_model_specs():
    by_name = {a.name: a for a in catalog.arms()}
    assert len(by_name) == len(catalog.arms())  # unique names
    for spec in backtest.MODELS + backtest.EXPERIMENTS:
        assert spec.name in by_name, spec.name
        expected = tuple(sorted(ALLOWED_SOURCES - set(spec.exclude_sources)))
        assert by_name[spec.name].uses_sources == expected, spec.name
    # The Layer 1 models never use price or wind features: the catalog must say so.
    assert not {"price_day_ahead", "price_mid", "wind_forecast"} & set(by_name["lgbm"].uses_sources)


# --- the long results table -----------------------------------------------------

@pytest.fixture(scope="module")
def metrics() -> pd.DataFrame:
    return results_store.build_metrics(REPORTS)


def test_every_question_class_is_present_and_columns_are_exact(metrics):
    assert list(metrics.columns) == results_store.METRIC_COLUMNS
    assert set(metrics["question_class"]) == {
        "carbon_forecast_accuracy", "price_forecast_accuracy", "slot_ranking", "charging_value",
        "cost_carbon_tradeoff", "experiments"}
    assert metrics["value"].notna().all() and metrics["metric"].notna().all() and metrics["arm"].notna().all()


def test_every_arm_in_the_results_is_described_in_the_catalog(metrics):
    known = {a.name for a in catalog.arms()} | {"random"}
    assert set(metrics["arm"]) <= known, set(metrics["arm"]) - known
    assert set(metrics["vs_arm"].dropna()) <= known


def test_intervals_bracket_their_values(metrics):
    ci = metrics.dropna(subset=["ci_low", "ci_high"])
    assert len(ci) > 100
    assert ((ci["ci_low"] <= ci["value"] + 1e-9) & (ci["value"] <= ci["ci_high"] + 1e-9)).all()


def test_headline_numbers_match_the_report(metrics):
    def one(**kw):
        d = metrics
        for k, v in kw.items():
            d = d[d[k] == v]
        assert len(d) == 1, (kw, len(d))
        return d.iloc[0]

    mae = one(question_class="carbon_forecast_accuracy", arm="lgbm", group_key="overall", metric="mae")
    assert (round(mae["value"], 2), mae["n"], mae["n_unit"]) == (19.17, 30564, "half_hours")
    gap = one(question_class="charging_value", objective="carbon", scenario="default", arm="lgbm", metric="gap_captured")
    assert round(gap["value"], 2) == 0.34 and gap["n"] == 631 and gap["ci_low"] < 0.34 < gap["ci_high"]
    cost = one(question_class="charging_value", objective="cost", scenario="default", arm="model", metric="gap_captured")
    assert cost["n"] == 627  # the cost comparison uses fewer nights than the carbon one (see README counts table)
    assert one(question_class="charging_value", objective="carbon", scenario="default", arm="perfect",
               metric="gap_captured")["value"] == pytest.approx(1.0)
    assert one(question_class="charging_value", objective="carbon", scenario="default", arm="timer",
               metric="gap_captured")["value"] == pytest.approx(0.0)


def test_experiment_windows_keep_development_and_test_separate(metrics):
    ex = metrics[metrics["question_class"] == "experiments"]
    assert {"2024-07-01_2024-12-31", "2025-01-01_2026-09-29"} <= set(ex["eval_window"])
    dev = ex[(ex["eval_window"] == "2024-07-01_2024-12-31") & (ex["metric"] == "gap_captured_difference")
             & (ex["arm"] == "lgbm_night")].iloc[0]
    test = ex[(ex["eval_window"] == "2025-01-01_2026-09-29") & (ex["metric"] == "gap_captured_difference")
              & (ex["arm"] == "lgbm_night")].iloc[0]
    assert dev["ci_low"] > 0 > test["value"]  # the development gain that did not replicate
    assert dev["vs_arm"] == test["vs_arm"] == "lgbm_price"


# --- persistence ----------------------------------------------------------------

def test_persist_is_idempotent_and_writes_provenance(con, monkeypatch):
    monkeypatch.setattr(config, "REPORTS_DIR", REPORTS)
    first = results_store.persist(con, REPORTS, ROOT / "DATA_ISSUES.md")
    second = results_store.persist(con, REPORTS, ROOT / "DATA_ISSUES.md")
    assert first == second and first["results_meta"] == 1 and first["data_issues"] >= 35
    meta = con.execute("SELECT * FROM results_meta").df().iloc[0]
    assert meta["schema_version"] == results_store.SCHEMA_VERSION
    assert str(meta["test_start"])[:10] == "2025-01-01" and str(meta["test_end"])[:10] == "2026-09-29"
    n = con.execute("SELECT count(*) FROM results_metrics").fetchone()[0]
    assert n == first["results_metrics"]
