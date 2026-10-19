from datetime import datetime

import duckdb
import pandas as pd
import pytest
from carbon_charge.charging import blend, value
from carbon_charge.charging import strategies as st
from carbon_charge.charging.scenarios import DEFAULT
from carbon_charge_mcp.errors import ErrorPayload
from carbon_charge_mcp.models import NightComparison
from carbon_charge_mcp.server import create_server
from mcp import Client

pytestmark = pytest.mark.anyio
SPRING_NIGHT, AUTUMN_NIGHT = "2025-03-29", "2025-10-25"   # windows that contain a clock change
NIGHTS = ["2025-03-27", "2025-03-28", SPRING_NIGHT, "2025-03-30", "2025-03-31", "2025-10-23", "2025-10-24", AUTUMN_NIGHT, "2025-10-26", "2025-10-27"]


async def call(db_path, args):
    async with Client(create_server(db_path)) as c:
        return await c.call_tool("compare_strategies", args)


def ok(r) -> NightComparison:
    assert not r.is_error, r.content[0].text
    return NightComparison.model_validate(r.structured_content)


def err(r) -> ErrorPayload:
    assert r.is_error and r.structured_content
    return ErrorPayload.model_validate(r.structured_content)


def by(n: NightComparison):
    return {o.strategy: o for o in n.outcomes}


@pytest.mark.parametrize("night,slots,timer_start_utc", [
    ("2025-03-28", 26, datetime(2025, 3, 29, 0, 0)),        # ordinary GMT night
    (SPRING_NIGHT, 24, datetime(2025, 3, 30, 0, 0)),         # an hour is lost: 24 slots; local midnight is still GMT
    (AUTUMN_NIGHT, 28, datetime(2025, 10, 25, 23, 0)),       # an hour is gained: 28 slots; local midnight is 23:00 UTC (BST)
])
async def test_every_strategy_delivers_the_energy_within_power_and_window(results_db, night, slots, timer_start_utc):
    n = ok(await call(results_db, {"night": night}))
    assert n.window_slots == slots and [o.strategy for o in n.outcomes] == ["arrival", "timer", "forecast_optimised", "perfect_foresight"]
    window = st.window_slots(pd.Timestamp(night).date(), DEFAULT)
    assert len(window) == slots
    for o in n.outcomes:
        assert o.energy_kwh == pytest.approx(8.0)                                   # energy delivered
        assert all(0 < s.kwh <= 3.5 + 1e-9 for s in o.slots)                        # 7 kW for half an hour
        assert all(pd.Timestamp(s.start_utc) in window for s in o.slots)            # inside plug-in to deadline
        assert len({s.start_utc for s in o.slots}) == len(o.slots)                  # no slot used twice
    b = by(n)
    assert b["timer"].slots[0].start_utc == timer_start_utc                         # timer begins at local midnight
    assert b["arrival"].slots[0].start_local.strftime("%H:%M") == "18:00"           # arrival begins at plug-in
    assert [round(s.kwh, 3) for s in b["arrival"].slots] == [3.5, 3.5, 1.0]
    assert n.window_start_local.strftime("%H:%M") == "18:00" and n.window_end_local.strftime("%H:%M") == "07:00"


@pytest.mark.parametrize("objective,kwargs", [("carbon", {}), ("cost", {}), ("blend", {"carbon_price_gbp_per_tonne": 250}),
                                              ("blend", {"carbon_price_gbp_per_tonne": 2500})])
async def test_perfect_foresight_is_never_beaten_on_any_night_or_objective(results_db, objective, kwargs):
    async with Client(create_server(results_db)) as c:
        for night in NIGHTS:
            for forecast in ("model", "naive", "da_basis"):
                n = NightComparison.model_validate((await c.call_tool("compare_strategies", {
                    "night": night, "objective": objective, "forecast": forecast, **kwargs})).structured_content)
                b = by(n)
                assert b["perfect_foresight"].objective_value <= min(o.objective_value for o in n.outcomes) + 1e-9, (night, forecast)
                assert b["timer"].vs_timer_objective_pct == 0


async def test_a_forecast_that_ranks_slots_correctly_captures_the_whole_gap(results_db):
    # lgbm_price equals the actual; last_week is the actual minus a constant, so it ranks the slots identically.
    for forecast in ("model", "lgbm_price", "last_week"):
        n = ok(await call(results_db, {"night": SPRING_NIGHT, "forecast": forecast}))
        assert n.gap_captured_this_night == pytest.approx(1.0)
        assert by(n)["forecast_optimised"].slots == by(n)["perfect_foresight"].slots
    noisy = ok(await call(results_db, {"night": SPRING_NIGHT, "forecast": "yesterday"}))
    assert noisy.gap_captured_this_night < 1.0 and by(noisy)["forecast_optimised"].objective_value > by(noisy)["perfect_foresight"].objective_value
    cost = ok(await call(results_db, {"night": SPRING_NIGHT, "objective": "cost", "forecast": "price_lgbm"}))  # constant offset
    assert cost.gap_captured_this_night == pytest.approx(1.0) and cost.forecast.price_arm == "price_lgbm" and cost.forecast.carbon_arm is None


async def test_the_blended_objective_is_cost_plus_priced_carbon(results_db):
    n = ok(await call(results_db, {"night": AUTUMN_NIGHT, "objective": "blend", "carbon_price_gbp_per_tonne": 400}))
    assert n.carbon_price_gbp_per_tonne == 400 and "GBP" in n.objective_unit
    for o in n.outcomes:
        assert o.objective_value == pytest.approx(o.cost_gbp + 400 * o.carbon_g / 1e6)
    default = ok(await call(results_db, {"night": AUTUMN_NIGHT, "objective": "blend"}))
    assert default.carbon_price_gbp_per_tonne == 250 and any("default of GBP 250" in x for x in default.notes)


async def test_agrees_with_the_pipelines_own_simulator(results_db):
    con = duckdb.connect(str(results_db), read_only=True)
    ser = lambda sql: con.execute(sql).df().set_index("ts_utc")["v"]  # noqa: E731
    ci, price = ser("SELECT ts_utc, actual_gco2_kwh v FROM ci_history"), ser("SELECT ts_utc, price_gbp_mwh v FROM price_mid")
    ci_fc = ser("SELECT ts_utc, forecast_gco2_kwh v FROM backtest_forecast WHERE model='lgbm_price'")
    price_fc = ser("SELECT ts_utc, forecast_gbp_mwh v FROM backtest_price_forecast WHERE model='price_lgbm'")
    ycarbon = ser("SELECT ts_utc, forecast_gco2_kwh v FROM backtest_forecast WHERE model='yesterday'")
    for night in (SPRING_NIGHT, AUTUMN_NIGHT, "2025-03-28"):
        d = pd.Timestamp(night).date()
        sim = blend.simulate(ci, price, {"m": (ci_fc, price_fc)}, DEFAULT, [d], 250.0).set_index("strategy")
        n = by(ok(await call(results_db, {"night": night, "objective": "blend", "carbon_price_gbp_per_tonne": 250})))
        for ours, theirs in (("arrival", "arrival"), ("timer", "timer"), ("perfect_foresight", "perfect"), ("forecast_optimised", "m")):
            assert n[ours].carbon_g == pytest.approx(sim.loc[theirs, "carbon_g"]), (night, ours)
            assert n[ours].cost_gbp == pytest.approx(sim.loc[theirs, "cost_gbp"]), (night, ours)
        l1 = value.simulate(ci, {"y": ycarbon}, DEFAULT, [d]).iloc[0]            # the Layer 1 carbon-only simulator
        c = by(ok(await call(results_db, {"night": night, "forecast": "yesterday"})))
        assert c["forecast_optimised"].carbon_g == pytest.approx(l1["y"]) and c["timer"].carbon_g == pytest.approx(l1["timer"])
        assert c["perfect_foresight"].carbon_g == pytest.approx(l1["perfect"]) and c["arrival"].carbon_g == pytest.approx(l1["arrival"])
    con.close()


async def test_missing_prices_degrade_gracefully_for_carbon_and_refuse_for_cost(results_db):
    night = "2025-04-01"   # the fixture removes 4 realised prices at the start of this night's window
    n = ok(await call(results_db, {"night": night}))                       # carbon objective: prices not needed
    assert all(o.cost_gbp is None and o.carbon_g is not None for o in n.outcomes)
    assert any("cost_gbp is null" in x for x in n.notes)
    p = err(await call(results_db, {"night": night, "objective": "cost"}))
    assert p.error_code == "INCOMPLETE_DATA" and "realised price: 4 of 26 slots missing" in p.message and "18:00" in p.message
    assert "objective='carbon'" in p.hint and p.retryable and "list_data_issues(id='PX-4')" in p.hint
    assert err(await call(results_db, {"night": night, "objective": "blend"})).error_code == "INCOMPLETE_DATA"


# --- failures ------------------------------------------------------------------------------------

async def test_the_last_night_needs_the_next_morning_so_it_is_one_day_earlier_than_the_window_end(results_db):
    p = err(await call(results_db, {"night": "2026-09-29"}))
    assert p.error_code == "OUT_OF_RANGE" and p.valid_range == "2025-01-01 to 2026-09-28" and p.did_you_mean == ["2026-09-28"]
    assert err(await call(results_db, {"night": "2024-12-31"})).did_you_mean == ["2025-01-01"]


async def test_unknown_scenario_lists_the_valid_ones(results_db):
    p = err(await call(results_db, {"night": SPRING_NIGHT, "scenario": "plug_in_18:00"}))
    assert p.error_code == "UNKNOWN_NAME" and p.field == "scenario" and "default" in p.allowed_values
    assert {"plug_in_17:00", "plug_in_19:00"} & set(p.did_you_mean)


async def test_a_scenario_that_skips_the_night_names_the_nights_it_does_charge(results_db):
    n = ok(await call(results_db, {"night": "2025-03-31", "scenario": "mon_wed_fri_only"}))      # a Monday
    assert n.scenario.nights == "mon_wed_fri"
    p = err(await call(results_db, {"night": "2025-04-01", "scenario": "mon_wed_fri_only"}))      # a Tuesday
    assert p.error_code == "NOT_A_CHARGING_NIGHT" and p.did_you_mean[0] == "2025-04-02" and "Tuesday" in p.message


async def test_a_sensitivity_scenario_changes_the_plan(results_db):
    n = ok(await call(results_db, {"night": SPRING_NIGHT, "scenario": "energy_20kWh"}))
    assert n.scenario.energy_kwh == 20 and all(o.energy_kwh == pytest.approx(20) for o in n.outcomes)
    late = ok(await call(results_db, {"night": SPRING_NIGHT, "scenario": "plug_in_21:00"}))
    # 21:00 to 07:00 is 10 hours (20 slots) normally, and 18 on the night the clocks go forward.
    assert late.window_slots == 18 and by(late)["arrival"].slots[0].start_local.strftime("%H:%M") == "21:00"
    assert ok(await call(results_db, {"night": "2025-03-28", "scenario": "plug_in_21:00"})).window_slots == 20


@pytest.mark.parametrize("args,field", [
    ({"objective": "carbon", "carbon_price_gbp_per_tonne": 100}, "carbon_price_gbp_per_tonne"),   # price without a blend
    ({"objective": "blend", "forecast": "lgbm"}, "forecast"),            # a single arm cannot supply both carbon and price
    ({"objective": "cost", "forecast": "yesterday"}, "forecast"),         # a carbon arm cannot drive a cost plan
    ({"objective": "carbon", "forecast": "price_da"}, "forecast"),
])
async def test_incompatible_choices_are_explained_with_what_would_be_valid(results_db, args, field):
    p = err(await call(results_db, {"night": SPRING_NIGHT, **args}))
    assert p.field == field and p.error_code in ("INVALID_ARGUMENT", "UNKNOWN_NAME") and p.retryable
    if p.error_code == "UNKNOWN_NAME":
        assert {"model", "day_ahead", "da_basis", "naive"} <= set(p.allowed_values)


@pytest.mark.parametrize("args", [{"night": "tonight"}, {"night": SPRING_NIGHT, "objective": "greenest"},
                                  {"night": SPRING_NIGHT, "objective": "blend", "carbon_price_gbp_per_tonne": -5},
                                  {"night": SPRING_NIGHT, "objective": "blend", "carbon_price_gbp_per_tonne": "lots"}, {}])
async def test_malformed_arguments_are_structured(results_db, args):
    p = err(await call(results_db, args))
    assert p.error_code == "INVALID_ARGUMENT" and p.problems and p.retryable


async def test_a_night_with_no_stored_data_is_incomplete_not_empty(results_db):
    p = err(await call(results_db, {"night": "2025-06-15"}))
    assert p.error_code == "INCOMPLETE_DATA" and "actual carbon intensity" in p.message
