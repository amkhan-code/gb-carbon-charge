"""get_forecast: how each model's day-ahead forecast compared with what happened on one day."""

import math
from datetime import date as Date
from typing import Annotated, Literal

import pandas as pd
from carbon_charge.timeutils import UK, UTC, cutoff_utc, periods_in_day
from mcp.types import ToolAnnotations
from pydantic import Field

from carbon_charge_mcp import queries
from carbon_charge_mcp.context import check_in_window, meta, provenance, resolve_names
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import FIELD_HINTS, failure, guard
from carbon_charge_mcp.models import DayForecast, ModelDayScore, PeriodRow, Unavailable

FIELD_HINTS["date"] = "Use an ISO date, YYYY-MM-DD, inside the forecast window (see describe_dataset)."
FIELD_HINTS["models"] = "Omit it for the standard arms, or pass names from describe_dataset (section='overview')."
UNITS = {"carbon": "gCO2/kWh", "price": "GBP/MWh"}
FORECAST_KINDS = {"carbon": ("carbon_model", "baseline"), "price": ("price_model", "baseline")}
NO_STORED = ("experiment_variant",)


def _local(ts: pd.Timestamp):
    return ts.tz_localize(UTC).tz_convert(UK).tz_localize(None).to_pydatetime()


def _lowest3(values: dict[int, float]) -> list[int]:
    return [p for p, _ in sorted(values.items(), key=lambda kv: (kv[1], kv[0]))[:3]]


def register(mcp, db: ResultsDb) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="Forecast against actual for one day", read_only_hint=True,
                                          idempotent_hint=True, destructive_hint=False, open_world_hint=False))
    @guard
    def get_forecast(
        date: Annotated[Date, Field(description="Settlement date (UK local day), YYYY-MM-DD, inside the forecast window.")],
        target: Annotated[Literal["carbon", "price"], Field(
            description="'carbon' for carbon intensity (gCO2/kWh) or 'price' for the realised half-hourly market price (GBP/MWh).")] = "carbon",
        models: Annotated[list[str] | None, Field(min_length=1, max_length=8, description=(
            "Arms to include, from describe_dataset. Omit for the standard models and baselines."))] = None,
        detail: Annotated[Literal["summary", "periods"], Field(description=(
            "'summary' (default) returns per-model error scores. 'periods' also returns every half-hour (46 to 50 rows)."))] = "summary",
    ) -> DayForecast:
        """How each model's day-ahead forecast compared with what actually happened on one day: error scores per model
        (MAE, RMSE, bias) and whether it picked the day's three lowest periods correctly. Use it to look at a specific
        day. Forecasts are backtest forecasts made at the 11:00 cutoff the day before; there is no live forecast, so
        dates outside the forecast window are refused. For results over many days use query_metrics instead."""
        m = meta(db)
        check_in_window(date, m, field="date")
        arms = db.rows(queries.ARMS)
        valid = [a["name"] for a in arms if a["target"] == target and a["kind"] in
                 FORECAST_KINDS[target] + ("reference", "experiment_variant")]
        default = [a["name"] for a in arms if a["target"] == target and a["kind"] in FORECAST_KINDS[target]]
        wanted = resolve_names(models, valid, field="models", noun="model") if models else default
        info = {a["name"]: a for a in arms}

        rows = db.rows(queries.FORECAST_DAY[target], date)
        if not rows:
            raise failure("INCOMPLETE_DATA", f"No {target} data is stored for {date}, although it is inside the window.",
                          field="date", got=str(date), hint="Try a neighbouring date; report the gap if it persists.", retryable=False)
        by_period: dict[int, dict] = {}
        for r in rows:
            p = by_period.setdefault(int(r["period"]), {"ts": pd.Timestamp(r["ts_utc"]), "actual": r["actual"], "f": {}})
            if r["model"] is not None and r["forecast"] is not None:
                p["f"][r["model"]] = float(r["forecast"])

        scores, unavailable, notes = [], [], []
        for name in wanted:
            pairs = {k: (v["actual"], v["f"][name]) for k, v in by_period.items()
                     if name in v["f"] and v["actual"] is not None and not math.isnan(v["actual"])}
            if not pairs:
                kind = info[name]["kind"]
                reason = ("an experiment variant: its forecasts were not stored; its results are in "
                          "query_metrics(question_class='experiments')" if kind in NO_STORED else
                          f"no stored forecasts for {date}" + (" (the NESO logger began 2026-09-30)" if name == "neso_logged" else ""))
                unavailable.append(Unavailable(model=name, reason=reason))
                continue
            err = [f - a for a, f in pairs.values()]
            fc = {k: f for k, (a, f) in pairs.items()}
            ac = {k: a for k, (a, f) in pairs.items()}
            lf, la = _lowest3(fc), _lowest3(ac)
            scores.append(ModelDayScore(
                model=name, n_periods=len(pairs), mae=sum(abs(e) for e in err) / len(err),
                rmse=math.sqrt(sum(e * e for e in err) / len(err)), bias=sum(err) / len(err),
                forecast_mean=sum(fc.values()) / len(fc), actual_mean=sum(ac.values()) / len(ac),
                lowest_3_forecast=lf, lowest_3_actual=la, overlap_top3=len(set(lf) & set(la)) / 3))
        with_actual = sum(1 for v in by_period.values() if v["actual"] is not None and not math.isnan(v["actual"]))
        expected = periods_in_day(date)
        if with_actual < expected:
            notes.append(f"Actuals are missing for {expected - with_actual} of {expected} periods; scores use the rest.")
        if expected != 48:
            notes.append(f"{date} is a clock-change day with {expected} settlement periods.")
        if "neso_latest_revision" in wanted:
            notes.append("neso_latest_revision is NOT a day-ahead forecast (DATA_ISSUES CI-1); it is shown for context only.")
        if not scores:
            notes.append("None of the requested arms has a forecast for this day; see unavailable_models.")
        periods = None
        if detail == "periods":
            periods = [PeriodRow(settlement_period=k, start_utc=v["ts"].to_pydatetime(), start_local=_local(v["ts"]),
                                 actual=None if v["actual"] is None or math.isnan(v["actual"]) else float(v["actual"]),
                                 forecasts={n: v["f"].get(n) for n in wanted if any(n in x["f"] for x in by_period.values())})
                       for k, v in sorted(by_period.items())]
        return DayForecast(
            date=date, target=target, unit=UNITS[target], periods_in_day=expected, periods_with_actuals=with_actual,
            forecasts_issued_by_utc=cutoff_utc(date).replace(tzinfo=None), scores=scores, unavailable_models=unavailable,
            periods=periods, notes=notes, provenance=provenance(m))
