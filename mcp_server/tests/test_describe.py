import pytest
from carbon_charge_mcp.models import DatasetDescription
from carbon_charge_mcp.server import create_server
from carbon_charge_mcp.tools.describe import SECTIONS, _limitations
from mcp import Client

pytestmark = pytest.mark.anyio


async def call(db_path, name="describe_dataset", args=None):
    async with Client(create_server(db_path)) as c:
        return await c.call_tool(name, args or {})


async def test_describe_all_is_typed_and_complete(results_db):
    r = await call(results_db, args={"section": "all"})
    assert not r.is_error
    d = DatasetDescription.model_validate(r.structured_content)  # the published output schema holds
    assert (str(d.forecast_window.first), str(d.forecast_window.last)) == ("2025-01-01", "2026-09-29")
    assert str(d.data_window.last) == "2026-09-29"
    assert d.provenance.out_of_sample is True and d.data_issue_count >= 35 and d.provenance.exported_from_commit
    names = {a.name: a for a in d.arms}
    assert {"lgbm", "lgbm_price", "price_lgbm", "price_da", "timer", "perfect", "neso_logged"} <= set(names)
    # The Layer 1 model must be described as using no price or wind inputs.
    assert not {"price_day_ahead", "price_mid", "wind_forecast"} & set(names["lgbm"].uses_sources)
    assert "price_day_ahead" in names["lgbm_price"].uses_sources
    assert names["lgbm"].forecast_coverage.n_half_hours > 500 and names["timer"].forecast_coverage is None
    assert {s.name for s in d.scenarios} >= {"default", "plug_in_17:00", "energy_20kWh", "mon_wed_fri_only"}
    classes = {c.name: c for c in d.question_classes}
    assert set(classes) == {"carbon_forecast_accuracy", "price_forecast_accuracy", "slot_ranking", "charging_value",
                            "cost_carbon_tradeoff", "experiments"}
    assert {"overall", "season", "time_of_day"} <= set(classes["carbon_forecast_accuracy"].group_by)
    assert "perfect" in classes["charging_value"].arms and "(no description" not in classes["charging_value"].description
    assert "no live forecast" in d.limitations[0].text.lower()
    assert d.sections_returned == ["overview", "arms", "scenarios", "question_classes", "limitations"]


async def test_the_default_is_a_short_overview_with_every_valid_name(results_db):
    r = await call(results_db)  # no arguments
    d = DatasetDescription.model_validate(r.structured_content)
    assert d.sections_returned == ["overview"] and d.arms is None and d.limitations is None
    assert len(r.content[0].text) < 3500  # cheap enough to call first, every time
    ov = d.overview
    assert {"lgbm", "lgbm_price"} <= set(ov.arm_names_by_kind["carbon_model"]) and "price_lgbm" in ov.arm_names_by_kind["price_model"]
    assert {"arrival", "timer", "perfect"} <= set(ov.arm_names_by_kind["strategy"])
    assert "default" in ov.scenario_names and "charging_value" in ov.question_classes and ov.limitation_count >= 6
    assert "section=" in ov.next_step and (str(d.forecast_window.first), str(d.forecast_window.last)) == ("2025-01-01", "2026-09-29")


async def test_a_section_narrows_the_answer(results_db):
    r = await call(results_db, args={"section": "scenarios"})
    d = DatasetDescription.model_validate(r.structured_content)
    assert d.sections_returned == ["scenarios"] and d.scenarios
    assert d.arms is None and d.question_classes is None and d.limitations is None and d.overview is None
    assert len(r.content[0].text) < 3000  # small enough to be cheap in an agent's context


async def test_every_limitation_points_only_at_issues_that_exist(results_db):
    d = DatasetDescription.model_validate((await call(results_db, args={"section": "limitations"})).structured_content)
    assert any(lim.issue_ids for lim in d.limitations)
    assert {"X-5", "WIND-1", "X-4"} <= {i for lim in d.limitations for i in lim.issue_ids}
    # A reference to an issue that is not in the log is dropped, never dangled.
    out = _limitations({}, {"X-5"}, None)
    assert {i for lim in out for i in lim.issue_ids} == {"X-5"}


async def test_neso_arm_is_reported_honestly_both_ways(results_db, results_db_with_neso):
    without = DatasetDescription.model_validate((await call(results_db, args={"section": "all"})).structured_content)
    neso = next(a for a in without.arms if a.name == "neso_logged")
    assert neso.forecast_coverage is None
    assert any("no stored forecasts" in lim.text for lim in without.limitations)
    with_ = DatasetDescription.model_validate((await call(results_db_with_neso, args={"section": "all"})).structured_content)
    assert next(a for a in with_.arms if a.name == "neso_logged").forecast_coverage.n_half_hours == 6
    assert any("covers only 6 half-hours" in lim.text for lim in with_.limitations)


def test_section_values_are_the_documented_ones():
    assert SECTIONS == ("overview", "arms", "scenarios", "question_classes", "limitations", "all")
