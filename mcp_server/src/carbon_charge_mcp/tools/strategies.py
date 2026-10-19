"""compare_strategies: the four charging strategies on one night, recomputed from the stored forecasts."""

from datetime import date as Date
from datetime import datetime, time, timedelta
from typing import Annotated, Literal

import pandas as pd
from carbon_charge.charging import blend
from carbon_charge.charging import strategies as st
from carbon_charge.charging.scenarios import Scenario
from carbon_charge.timeutils import UK, UTC
from mcp.types import ToolAnnotations
from pydantic import Field

from carbon_charge_mcp import queries
from carbon_charge_mcp.context import as_date, check_in_window, meta, provenance, resolve_names, window
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import FIELD_HINTS, failure, guard
from carbon_charge_mcp.models import (
    ChargeSlot, ForecastUsed, NightComparison, ScenarioInfo, StrategyOutcome,
)

PIPELINES = {"model": ("lgbm_price", "price_lgbm"), "day_ahead": ("lgbm_price", "price_da"),
             "da_basis": ("lgbm_price", "price_da_basis7d"), "naive": ("last_week", "price_last_week")}
DEFAULT_CARBON_PRICE = 250.0
UNITS = {"carbon": "gCO2", "cost": "GBP", "blend": "GBP (cost plus carbon at the stated carbon price)"}
FIELD_HINTS["night"] = "Use an ISO date, YYYY-MM-DD: the evening the car plugs in. The last valid night is the day before the window ends."
FIELD_HINTS["carbon_price_gbp_per_tonne"] = "A number of GBP per tonne of CO2, 0 or more. Only used when objective='blend'."


def _local(ts: pd.Timestamp) -> datetime:
    return ts.tz_localize(UTC).tz_convert(UK).tz_localize(None).to_pydatetime()


def _hm(s: str) -> time:
    return time.fromisoformat(s)


def _series(rows: list[dict], slots: pd.DatetimeIndex) -> pd.Series:
    got = {pd.Timestamp(r["ts_utc"]): r["v"] for r in rows}
    return pd.Series([got.get(t) for t in slots], index=slots, dtype="float64")


def _planning_cost(price: pd.Series | None, ci: pd.Series | None, objective: str, lam: float) -> pd.Series:
    if objective == "carbon":
        return ci
    if objective == "cost":
        return price / 1000
    return blend.slot_cost(price, ci, lam)


KNOWN_GAPS = {"realised price": "PX-4", "actual carbon intensity": "CI-3"}   # data issues that explain such gaps


def _missing(series: pd.Series, what: str) -> str:
    gone = series[series.isna()].index
    first = ", ".join(_local(t).strftime("%d %b %H:%M") for t in gone[:3])
    return f"{what}: {len(gone)} of {len(series)} slots missing (first: {first})"


def register(mcp, db: ResultsDb) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="Compare charging strategies for one night", read_only_hint=True,
                                          idempotent_hint=True, destructive_hint=False, open_world_hint=False))
    @guard
    def compare_strategies(
        night: Annotated[Date, Field(description="The evening the car plugs in, YYYY-MM-DD (UK local date).")],
        scenario: Annotated[str, Field(description="Charging scenario name from describe_dataset. 'default' is plug in 18:00, "
                                                   "8 kWh by 07:00, 7 kW charger.")] = "default",
        objective: Annotated[Literal["carbon", "cost", "blend"], Field(description=(
            "What the plans minimise: 'carbon' (default), wholesale 'cost', or a 'blend' of the two at a carbon price."))] = "carbon",
        carbon_price_gbp_per_tonne: Annotated[float | None, Field(ge=0, le=100000, description=(
            "Only for objective='blend': the price put on carbon, GBP per tonne. Defaults to 250."))] = None,
        forecast: Annotated[str, Field(description=(
            "Which forecast drives the forecast_optimised strategy. 'model' (default), 'day_ahead', 'da_basis' and 'naive' are "
            "named combinations of a carbon and a price forecast and work with any objective. A single arm name also works: "
            "a carbon arm (e.g. lgbm, yesterday) for objective='carbon', or a price arm (e.g. price_da) for 'cost'."))] = "model",
    ) -> NightComparison:
        """Compare four ways of charging a car on one night: charge on arrival, an overnight timer from 00:00, the
        forecast-optimised plan, and perfect foresight (an upper bound that no real system can reach). Plans are
        recomputed from the stored out-of-sample forecasts and scored at what actually happened, so the answer shows
        which half-hours each strategy used and the carbon and wholesale cost that resulted. One night is noisy:
        for how much of the possible saving a forecast captures over the whole period, use query_metrics with
        question_class='charging_value'. Nights outside the window, or without a complete set of data, are refused."""
        m = meta(db)
        first, last = window(m)
        check_in_window(night, m, field="night", last_override=last - timedelta(days=1), what="night")

        scen_rows = db.rows(queries.SCENARIO, scenario)
        if not scen_rows:
            names = [r["name"] for r in db.rows(queries.SCENARIOS)]
            resolve_names([scenario], names, field="scenario", noun="scenario")
        r = scen_rows[0]
        sc = Scenario(name=r["name"], plug_in=_hm(r["plug_in"]), energy_kwh=float(r["energy_kwh"]), power_kw=float(r["power_kw"]),
                      deadline=_hm(r["deadline"]), nights=r["nights"])
        if not sc.includes(night):
            nxt, d = [], night
            while len(nxt) < 3 and d < last:
                d += timedelta(days=1)
                if sc.includes(d) and d <= last - timedelta(days=1):
                    nxt.append(str(d))
            raise failure("NOT_A_CHARGING_NIGHT",
                          f"Scenario '{scenario}' does not charge on {night:%A %d %B %Y} (it charges on Mondays, Wednesdays and Fridays).",
                          field="night", got=str(night), did_you_mean=nxt,
                          hint="Pick a night the scenario charges, or use scenario='default' which charges every night.")

        lam = None
        if objective != "blend" and carbon_price_gbp_per_tonne is not None:
            raise failure("INVALID_ARGUMENT", "carbon_price_gbp_per_tonne only applies when objective='blend'.",
                          field="carbon_price_gbp_per_tonne", got=str(carbon_price_gbp_per_tonne),
                          hint="Remove it, or set objective='blend'.")
        notes: list[str] = []
        if objective == "blend":
            lam = DEFAULT_CARBON_PRICE if carbon_price_gbp_per_tonne is None else float(carbon_price_gbp_per_tonne)
            if carbon_price_gbp_per_tonne is None:
                notes.append(f"No carbon price given; used the default of GBP {DEFAULT_CARBON_PRICE:.0f} per tonne.")

        arms = db.rows(queries.ARMS)
        carbon_arms = [a["name"] for a in arms if a["target"] == "carbon" and a["kind"] in ("carbon_model", "baseline")]
        price_arms = [a["name"] for a in arms if a["target"] == "price" and a["kind"] in ("price_model", "baseline")]
        allowed = {"carbon": list(PIPELINES) + carbon_arms, "cost": list(PIPELINES) + price_arms, "blend": list(PIPELINES)}[objective]
        resolve_names([forecast], allowed, field="forecast", noun="forecast")
        if forecast in PIPELINES:
            carbon_arm, price_arm = PIPELINES[forecast]
        else:
            carbon_arm, price_arm = (forecast, None) if forecast in carbon_arms else (None, forecast)
        need_ci = objective in ("carbon", "blend")
        need_price = objective in ("cost", "blend")
        if objective == "carbon":
            price_arm = None
        if objective == "cost":
            carbon_arm = None

        slots = st.window_slots(night, sc)
        lo, hi = slots[0].to_pydatetime(), slots[-1].to_pydatetime()
        ci = _series(db.rows(queries.CI_RANGE, lo, hi), slots)
        price = _series(db.rows(queries.PRICE_RANGE, lo, hi), slots)
        ci_fc = _series(db.rows(queries.CARBON_FC_RANGE, carbon_arm, lo, hi), slots) if carbon_arm else None
        price_fc = _series(db.rows(queries.PRICE_FC_RANGE, price_arm, lo, hi), slots) if price_arm else None

        required = [(ci, "actual carbon intensity")] * need_ci + [(price, "realised price")] * need_price
        required += [(ci_fc, f"{carbon_arm} carbon forecast")] * bool(ci_fc is not None) + \
                    [(price_fc, f"{price_arm} price forecast")] * bool(price_fc is not None)
        gaps = [_missing(s, what) for s, what in required if s.isna().any()]
        if gaps:
            raise failure("INCOMPLETE_DATA", f"Not enough data to compare strategies on {night} for objective '{objective}': "
                          + "; ".join(gaps) + ".", field="night", got=str(night),
                          hint="Try another night. For objective='carbon' only carbon data is needed, so a night with missing prices can still be answered."
                               + "".join(f" Known issue {i}: list_data_issues(id='{i}')." for w, i in KNOWN_GAPS.items()
                                         if any(g.startswith(w) for g in gaps)))
        have_ci, have_price = not ci.isna().any(), not price.isna().any()
        if not have_price and not need_price:
            notes.append("Realised prices are incomplete for this night, so cost_gbp is null; the carbon comparison is unaffected.")
        if not have_ci and not need_ci:
            notes.append("Actual carbon intensity is incomplete for this night, so carbon_g is null.")

        plans = {
            "arrival": st.charge_on_arrival(night, sc),
            "timer": st.overnight_timer(night, sc),
            "forecast_optimised": st.optimised(night, sc, _planning_cost(price_fc, ci_fc, objective, lam)),
            "perfect_foresight": st.optimised(night, sc, _planning_cost(price, ci, objective, lam)),
        }

        def measures(plan: pd.Series) -> tuple[float | None, float | None, float]:
            carbon = float((plan * ci.reindex(plan.index)).sum()) if have_ci else None
            cost = float((plan * price.reindex(plan.index)).sum() / 1000) if have_price else None
            obj = {"carbon": carbon, "cost": cost,
                   "blend": None if carbon is None or cost is None else cost + (lam or 0) * carbon / 1e6}[objective]
            return carbon, cost, obj

        scored = {k: measures(p) for k, p in plans.items()}
        timer_c, timer_p, timer_o = scored["timer"]
        pct = lambda v, ref: None if v is None or ref in (None, 0) else round(100 * (1 - v / ref), 2)  # noqa: E731
        outcomes = []
        for k, plan in plans.items():
            c, p, o = scored[k]
            outcomes.append(StrategyOutcome(
                strategy=k, energy_kwh=round(float(plan.sum()), 6), carbon_g=c, cost_gbp=p, objective_value=o,
                slots=[ChargeSlot(start_utc=t.to_pydatetime(), start_local=_local(t), kwh=round(float(v), 6))
                       for t, v in sorted(plan.items()) if v > 0],
                vs_timer_objective_pct=pct(o, timer_o), vs_timer_carbon_pct=pct(c, timer_c), vs_timer_cost_pct=pct(p, timer_p)))
        gap = timer_o - scored["perfect_foresight"][2]
        captured = None if abs(gap) < 1e-9 else (timer_o - scored["forecast_optimised"][2]) / gap
        if captured is None:
            notes.append("The overnight timer was already optimal on this night, so there was nothing to capture.")
        else:
            notes.append("A single night is noisy; query_metrics(question_class='charging_value') gives the result over the whole period.")
        if forecast in PIPELINES and objective == "carbon":
            notes.append(f"Pipeline '{forecast}' uses the {carbon_arm} carbon forecast for this objective.")
        if len(slots) != 26:
            notes.append(f"This night has {len(slots)} half-hour slots in the window because of a clock change.")
        return NightComparison(
            night=night, scenario=ScenarioInfo(name=sc.name, plug_in=r["plug_in"], deadline=r["deadline"], energy_kwh=sc.energy_kwh,
                                               power_kw=sc.power_kw, nights=sc.nights),
            window_start_local=_local(slots[0]), window_end_local=_local(slots[-1] + pd.Timedelta(minutes=30)),
            window_slots=len(slots), objective=objective, objective_unit=UNITS[objective], carbon_price_gbp_per_tonne=lam,
            forecast=ForecastUsed(selected=forecast, carbon_arm=carbon_arm, price_arm=price_arm), outcomes=outcomes,
            gap_captured_this_night=captured, notes=notes, provenance=provenance(m))
