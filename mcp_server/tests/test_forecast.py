from datetime import datetime

import pytest
from carbon_charge_mcp.errors import ErrorPayload
from carbon_charge_mcp.models import DayForecast
from carbon_charge_mcp.server import create_server
from mcp import Client

pytestmark = pytest.mark.anyio


async def call(db_path, args):
    async with Client(create_server(db_path)) as c:
        return await c.call_tool("get_forecast", args)


def ok(r) -> DayForecast:
    assert not r.is_error, r.content[0].text
    return DayForecast.model_validate(r.structured_content)


def err(r) -> ErrorPayload:
    assert r.is_error and r.structured_content
    return ErrorPayload.model_validate(r.structured_content)


async def test_summary_scores_match_hand_computed_values(results_db):
    d = ok(await call(results_db, {"date": "2025-04-01"}))
    s = {x.model: x for x in d.scores}
    assert (d.target, d.unit, d.periods_in_day, d.periods_with_actuals) == ("carbon", "gCO2/kWh", 48, 48)
    # lgbm_price forecasts the actual exactly; last_week is a constant -20 below it.
    assert (s["lgbm_price"].mae, s["lgbm_price"].rmse, s["lgbm_price"].bias, s["lgbm_price"].overlap_top3) == (0, 0, 0, 1.0)
    assert (s["last_week"].mae, s["last_week"].rmse, s["last_week"].bias) == pytest.approx((20, 20, -20))
    assert s["lgbm_noweather"].bias == pytest.approx(5.0)
    assert s["last_week"].overlap_top3 == 1.0  # a constant shift never changes the ranking
    assert s["yesterday"].mae > 5 and s["yesterday"].rmse >= s["yesterday"].mae  # noisy arm: RMSE >= MAE always
    assert d.periods is None
    assert d.forecasts_issued_by_utc == datetime(2025, 3, 31, 10, 0)  # 11:00 BST the day before
    assert d.provenance.out_of_sample is True


async def test_arms_without_forecasts_that_day_are_reported_not_dropped_or_guessed(results_db):
    d = ok(await call(results_db, {"date": "2025-04-01"}))
    un = {u.model: u.reason for u in d.unavailable_models}
    assert set(un) == {"neso_logged"}  # the only standard arm with nothing stored in this fixture
    assert "logger began 2026-09-30" in un["neso_logged"]
    wind = ok(await call(results_db, {"date": "2025-04-01", "models": ["lgbm_wind"]}))
    assert wind.scores == [] and "experiment variant" in wind.unavailable_models[0].reason
    assert any("None of the requested arms" in n for n in wind.notes)


@pytest.mark.parametrize("day,periods,first_local,last_local", [
    ("2025-03-30", 46, "2025-03-30T00:00:00", "2025-03-30T23:30:00"),   # spring: clocks go forward at 01:00
    ("2025-10-26", 50, "2025-10-26T00:00:00", "2025-10-26T23:30:00"),   # autumn: 01:00-02:00 happens twice
    ("2025-04-01", 48, "2025-04-01T00:00:00", "2025-04-01T23:30:00"),
])
async def test_clock_change_days_have_46_or_50_periods_with_correct_local_times(results_db, day, periods, first_local, last_local):
    d = ok(await call(results_db, {"date": day, "detail": "periods"}))
    assert d.periods_in_day == periods and len(d.periods) == periods
    assert d.periods[0].start_local.isoformat() == first_local and d.periods[-1].start_local.isoformat() == last_local
    assert [p.settlement_period for p in d.periods] == list(range(1, periods + 1))
    utc = [p.start_utc for p in d.periods]
    assert utc == sorted(utc) and len(set(utc)) == periods  # no duplicated or lost instants
    if day == "2025-03-30":  # the local clock jumps from 00:30 to 02:00
        assert [p.start_local.strftime("%H:%M") for p in d.periods[:4]] == ["00:00", "00:30", "02:00", "02:30"]
    if day == "2025-10-26":  # and repeats 01:00-02:00
        assert [p.start_local.strftime("%H:%M") for p in d.periods[2:8]] == ["01:00", "01:30", "01:00", "01:30", "02:00", "02:30"]


async def test_periods_detail_carries_actual_and_each_requested_forecast(results_db):
    d = ok(await call(results_db, {"date": "2025-04-01", "detail": "periods", "models": ["last_week", "lgbm_price"]}))
    p = d.periods[10]
    assert set(p.forecasts) == {"last_week", "lgbm_price"}
    assert p.forecasts["last_week"] == pytest.approx(p.actual - 20) and p.forecasts["lgbm_price"] == pytest.approx(p.actual)
    assert [s.model for s in d.scores] == ["last_week", "lgbm_price"]  # in the order asked for


async def test_price_target_uses_realised_prices_and_price_arms(results_db):
    d = ok(await call(results_db, {"date": "2025-04-02", "target": "price"}))
    s = {x.model: x for x in d.scores}
    assert d.unit == "GBP/MWh" and {"price_lgbm", "price_da", "price_last_week"} <= set(s)
    assert s["price_da"].bias == pytest.approx(5.0) and s["price_lgbm"].mae == pytest.approx(1.5)
    assert "lgbm" not in s  # carbon arms are not price arms


async def test_missing_actuals_are_noted_and_scores_use_what_exists(results_db):
    d = ok(await call(results_db, {"date": "2025-04-01", "target": "price"}))  # 4 prices removed on 2025-04-01 evening
    assert d.periods_with_actuals == 44 and any("missing for 4 of 48" in n for n in d.notes)
    assert {s.n_periods for s in d.scores} == {44}


# --- failures: each one tells the agent what to do next ---------------------------------------------

@pytest.mark.parametrize("day,nearest", [("2024-12-31", "2025-01-01"), ("2026-09-30", "2026-09-29"), ("2027-01-01", "2026-09-29")])
async def test_dates_outside_the_window_are_refused_with_the_nearest_valid_date(results_db, day, nearest):
    p = err(await call(results_db, {"date": day}))
    assert p.error_code == "OUT_OF_RANGE" and p.field == "date" and p.got == day
    assert p.valid_range == "2025-01-01 to 2026-09-29" and p.did_you_mean == [nearest]
    assert "describe_dataset" in p.hint
    assert ("no live forecast" in p.message) == (day > "2026-09-29")


async def test_a_day_inside_the_window_with_no_stored_data_is_an_actionable_error_not_an_empty_success(results_db):
    p = err(await call(results_db, {"date": "2025-06-15"}))  # inside the window; the fixture stores no rows for it
    assert p.error_code == "INCOMPLETE_DATA" and "2025-06-15" in p.message and p.hint


@pytest.mark.parametrize("bad", ["yesterday", "2025-02-30", "01/03/2025", "", None, 20250301.5])
async def test_malformed_dates_are_invalid_argument_with_the_format_spelled_out(results_db, bad):
    p = err(await call(results_db, {"date": bad}))
    assert p.error_code == "INVALID_ARGUMENT" and p.field == "date" and "YYYY-MM-DD" in p.hint
    assert p.retryable


async def test_unknown_model_names_get_suggestions_and_the_valid_list(results_db):
    p = err(await call(results_db, {"date": "2025-04-01", "models": ["lgbm_v2", "yesterdy"]}))
    assert p.error_code == "UNKNOWN_NAME" and p.field == "models"
    assert "lgbm" in p.did_you_mean and "yesterday" in p.did_you_mean
    assert {"lgbm", "lgbm_price", "yesterday", "last_week"} <= set(p.allowed_values)
    assert "price_lgbm" not in p.allowed_values  # a price arm is not valid for the carbon target
    wrong_target = err(await call(results_db, {"date": "2025-04-01", "models": ["price_da"]}))
    assert wrong_target.error_code == "UNKNOWN_NAME"


@pytest.mark.parametrize("args", [{"date": "2025-04-01", "models": []}, {"date": "2025-04-01", "detail": "everything"},
                                  {"date": "2025-04-01", "target": "weather"}, {}])
async def test_other_malformed_arguments_are_structured_too(results_db, args):
    p = err(await call(results_db, args))
    assert p.error_code == "INVALID_ARGUMENT" and p.problems
