"""query_metrics: results must equal the pipeline's own report files, whatever numbers those currently hold."""

import pandas as pd
import pytest
from carbon_charge_mcp.errors import ErrorPayload
from carbon_charge_mcp.models import MetricResult
from carbon_charge_mcp.server import create_server
from conftest import REPO
from mcp import Client

pytestmark = pytest.mark.anyio
CSV = lambda name: pd.read_csv(REPO / "reports" / f"{name}.csv")  # noqa: E731


async def call(db_path, args):
    async with Client(create_server(db_path)) as c:
        return await c.call_tool("query_metrics", args)


def ok(r) -> MetricResult:
    assert not r.is_error, r.content[0].text
    return MetricResult.model_validate(r.structured_content)


def err(r) -> ErrorPayload:
    assert r.is_error and r.structured_content
    return ErrorPayload.model_validate(r.structured_content)


def val(res: MetricResult, arm, metric, group="all"):
    hit = [r for r in res.rows if r.arm == arm and r.metric == metric and r.group_value == group]
    assert len(hit) == 1, (arm, metric, group, len(hit))
    return hit[0]


async def test_carbon_accuracy_equals_the_report_csv(results_db):
    res = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy"}))
    csv = CSV("metrics_overall").set_index("model")
    assert {r.arm for r in res.rows} == set(csv.index)
    for arm in csv.index:
        assert val(res, arm, "mae").value == pytest.approx(csv.loc[arm, "mae"])
        assert val(res, arm, "rmse").value == pytest.approx(csv.loc[arm, "rmse"])
        assert val(res, arm, "mae").n == csv.loc[arm, "n"] and val(res, arm, "mae").unit == "gCO2/kWh"
    assert res.sample_sizes == {"half_hours": [int(csv["n"].iloc[0])]}         # every arm scored on the same half-hours
    assert res.filters_applied == {"group_by": "overall"} and not res.truncated
    assert {c.issue_id for c in res.caveats} >= {"X-2", "X-5"} and all(c.title for c in res.caveats)
    assert res.provenance.out_of_sample


async def test_breakdowns_come_in_natural_order(results_db):
    season = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy", "group_by": "season", "arms": ["lgbm"], "metrics": ["mae"]}))
    assert [r.group_value for r in season.rows] == ["DJF", "MAM", "JJA", "SON"]
    csv = CSV("metrics_by_season").set_index(["model", "season"])
    for r in season.rows:
        assert r.value == pytest.approx(csv.loc[("lgbm", r.group_value), "mae"])
    hours = ok(await call(results_db, {"question_class": "price_forecast_accuracy", "group_by": "local_hour", "arms": ["price_da"], "metrics": ["mae"]}))
    assert [r.group_value for r in hours.rows] == [str(h) for h in range(24)]
    assert hours.rows[0].unit == "GBP/MWh"


async def test_arm_and_metric_filters_narrow_the_answer(results_db):
    res = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy", "arms": ["lgbm"], "metrics": ["mae"]}))
    assert len(res.rows) == 1 and res.filters_applied["arms"] == ["lgbm"] and res.filters_applied["metrics"] == ["mae"]


async def test_charging_value_matches_the_report_with_intervals_and_sample(results_db):
    res = ok(await call(results_db, {"question_class": "charging_value", "metrics": ["gap_captured"]}))
    csv = CSV("optimiser_summary")
    csv = csv[csv.scenario == "default"].set_index("strategy")
    assert res.filters_applied["scenario"] == "default" and res.filters_applied["objective"] == "carbon"
    for arm, row in csv.iterrows():
        r = val(res, arm, "gap_captured")
        assert r.value == pytest.approx(row["gap_captured"]) and r.n == row["nights"] and r.n_unit == "nights"
        if pd.notna(row["gap_ci_low"]):
            assert (r.ci_low, r.ci_high) == pytest.approx((row["gap_ci_low"], row["gap_ci_high"])) and r.ci_low < r.value < r.ci_high
    assert val(res, "perfect", "gap_captured").value == pytest.approx(1.0) and val(res, "timer", "gap_captured").value == pytest.approx(0.0)


async def test_cost_objective_and_scenarios_select_different_samples(results_db):
    carbon = ok(await call(results_db, {"question_class": "charging_value", "metrics": ["gap_captured"], "arms": ["perfect"]}))
    cost = ok(await call(results_db, {"question_class": "charging_value", "objective": "cost", "metrics": ["gap_captured"], "arms": ["model"]}))
    assert carbon.rows[0].n > cost.rows[0].n                                    # fewer nights have complete prices (README counts table)
    csv = CSV("optimiser_blend_cost_sensitivity")
    row = csv[(csv.scenario == "default") & (csv.strategy == "model")].iloc[0]
    assert cost.rows[0].value == pytest.approx(row["gap_captured"]) and cost.rows[0].n == row["nights"]
    other = ok(await call(results_db, {"question_class": "charging_value", "scenario": "energy_20kWh", "arms": ["lgbm"], "metrics": ["gap_captured"]}))
    assert other.filters_applied["scenario"] == "energy_20kWh"
    csv = CSV("optimiser_summary")
    assert other.rows[0].value == pytest.approx(csv[(csv.scenario == "energy_20kWh") & (csv.strategy == "lgbm")]["gap_captured"].iloc[0])


async def test_tradeoff_runs_from_cost_only_to_carbon_only(results_db):
    res = ok(await call(results_db, {"question_class": "cost_carbon_tradeoff", "arms": ["model"], "metrics": ["mean_cost_gbp"]}))
    order = [r.group_value for r in res.rows]
    assert order == ["0", "50", "100", "250", "500", "1000", "2500", "carbon only"]
    assert res.filters_applied["group_by"] == "carbon_price_gbp_per_tonne"
    assert res.rows[-1].value > res.rows[0].value                              # caring only about carbon costs more than caring only about cost


async def test_slot_ranking_includes_the_random_baseline(results_db):
    res = ok(await call(results_db, {"question_class": "slot_ranking", "arms": ["lgbm", "random_carbon"]}))
    assert {r.group_value for r in res.rows} == {"3", "6"} and res.filters_applied["group_by"] == "slots_chosen"
    for k in ("3", "6"):
        assert val(res, "lgbm", f"overlap_top{k}", k).value > val(res, "random_carbon", f"overlap_top{k}", k).value
    assert all(0 <= r.value <= 1 for r in res.rows)


async def test_experiments_default_to_the_test_window_and_keep_the_two_windows_apart(results_db):
    test = ok(await call(results_db, {"question_class": "experiments", "metrics": ["gap_captured_difference"]}))
    dev = ok(await call(results_db, {"question_class": "experiments", "metrics": ["gap_captured_difference"], "eval_window": "2024-07-01_2024-12-31"}))
    assert test.filters_applied["eval_window"] == "2025-01-01_2026-09-29" and dev.filters_applied["eval_window"] == "2024-07-01_2024-12-31"
    assert all(r.vs_arm == "lgbm_price" for r in test.rows + dev.rows)
    night_dev = next(r for r in dev.rows if r.arm == "lgbm_night")
    night_test = next(r for r in test.rows if r.arm == "lgbm_night")
    assert night_dev.ci_low > 0 > night_test.value                             # the gain that did not replicate
    assert any("test-window rows" in n for n in test.notes)


async def test_the_reference_sample_is_labelled_as_not_day_ahead(results_db):
    res = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy", "group_by": "reference_sample", "metrics": ["mae"]}))
    assert "neso_latest_revision" in {r.arm for r in res.rows} and any("NOT a day-ahead forecast" in n for n in res.notes)


# --- empty, large and failing -------------------------------------------------------------

async def test_a_valid_arm_with_no_results_is_an_empty_success_that_says_what_does_have_results(results_db):
    res = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy", "arms": ["neso_logged"]}))
    assert res.rows == [] and res.total_rows == 0
    assert any("No carbon_forecast_accuracy results for neso_logged" in n and "lgbm" in n for n in res.notes)


async def test_large_answers_are_capped_and_say_how_to_narrow_them(results_db):
    res = ok(await call(results_db, {"question_class": "carbon_forecast_accuracy", "group_by": "local_hour", "limit": 7}))
    assert len(res.rows) == 7 and res.truncated and res.total_rows > 7
    assert any("Narrow with arms, metrics or group_by" in n for n in res.notes)


async def test_unknown_arm_is_refused_with_a_suggestion(results_db):
    p = err(await call(results_db, {"question_class": "carbon_forecast_accuracy", "arms": ["lgbmprice"]}))
    assert p.error_code == "UNKNOWN_NAME" and p.field == "arms" and "lgbm_price" in p.did_you_mean
    assert "lgbm" in p.allowed_values


async def test_a_breakdown_the_class_does_not_have_is_refused_with_the_valid_ones(results_db):
    p = err(await call(results_db, {"question_class": "charging_value", "group_by": "season"}))
    assert p.error_code == "INVALID_ARGUMENT" and p.field == "group_by" and p.allowed_values == ["overall"]
    p = err(await call(results_db, {"question_class": "carbon_forecast_accuracy", "group_by": "seasons"}))
    assert "season" in p.did_you_mean and {"overall", "season", "time_of_day", "local_hour"} <= set(p.allowed_values)


@pytest.mark.parametrize("args,field", [
    ({"question_class": "carbon_forecast_accuracy", "scenario": "default"}, "scenario"),
    ({"question_class": "slot_ranking", "objective": "cost"}, "objective"),
    ({"question_class": "charging_value", "eval_window": "2025-01-01_2026-09-29"}, "eval_window"),
])
async def test_a_filter_that_does_not_apply_to_the_class_is_named_not_ignored(results_db, args, field):
    p = err(await call(results_db, args))
    assert p.error_code == "INVALID_ARGUMENT" and p.field == field and "applies to" in p.message


async def test_unknown_scenario_window_and_metric_list_the_valid_values(results_db):
    p = err(await call(results_db, {"question_class": "charging_value", "scenario": "plug_in_18:00"}))
    assert p.error_code == "UNKNOWN_NAME" and "default" in p.allowed_values
    p = err(await call(results_db, {"question_class": "experiments", "eval_window": "2025"}))
    assert p.error_code == "UNKNOWN_NAME" and "2025-01-01_2026-09-29" in p.allowed_values
    p = err(await call(results_db, {"question_class": "carbon_forecast_accuracy", "metrics": ["accuracy"]}))
    assert p.error_code == "UNKNOWN_NAME" and {"mae", "rmse"} <= set(p.allowed_values)


@pytest.mark.parametrize("args", [{}, {"question_class": "accuracy"}, {"question_class": "slot_ranking", "limit": 0},
                                  {"question_class": "slot_ranking", "limit": 501}, {"question_class": "slot_ranking", "arms": []}])
async def test_malformed_arguments_are_structured(results_db, args):
    p = err(await call(results_db, args))
    assert p.error_code == "INVALID_ARGUMENT" and p.problems
    if args.get("question_class") == "accuracy":
        assert "charging_value" in p.allowed_values and p.did_you_mean


async def test_chance_baselines_are_distinct_per_target_and_never_collide(results_db):
    res = ok(await call(results_db, {"question_class": "slot_ranking", "arms": ["random_carbon", "random_price"], "group_by": "slots_chosen"}))
    keys = [(r.arm, r.metric, r.group_value) for r in res.rows]
    assert len(keys) == len(set(keys)) == 4                                     # one row per (arm, k): no duplicates
    assert {r.target for r in res.rows} == {"carbon", "price"}
    assert {r.arm: r.target for r in res.rows} == {"random_carbon": "carbon", "random_price": "price"}


async def test_no_result_class_returns_two_rows_for_the_same_key(results_db):
    async with Client(create_server(results_db)) as c:
        for qc in ("carbon_forecast_accuracy", "price_forecast_accuracy", "slot_ranking", "charging_value", "cost_carbon_tradeoff", "experiments"):
            for group in ({"carbon_forecast_accuracy": ["overall", "season", "time_of_day", "local_hour", "reference_sample"],
                           "price_forecast_accuracy": ["overall", "season", "time_of_day", "local_hour"],
                           "slot_ranking": ["slots_chosen"], "charging_value": ["overall"],
                           "cost_carbon_tradeoff": ["carbon_price_gbp_per_tonne"], "experiments": ["overall", "wind_available"]}[qc]):
                res = MetricResult.model_validate((await c.call_tool("query_metrics", {"question_class": qc, "group_by": group, "limit": 500})).structured_content)
                keys = [(r.arm, r.vs_arm, r.metric, r.group_value) for r in res.rows]
                assert len(keys) == len(set(keys)), (qc, group)
