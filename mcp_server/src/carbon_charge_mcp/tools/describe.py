"""describe_dataset: what is here, and the valid names to use in every other tool."""

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from carbon_charge_mcp import queries
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import FIELD_HINTS, guard
from carbon_charge_mcp.models import (
    ArmCoverage, ArmInfo, DatasetDescription, DateRange, Limitation, Overview, Provenance, QuestionClassInfo,
    ScenarioInfo,
)
from carbon_charge_mcp.vocab import QUESTION_CLASS_TEXT, UNDESCRIBED

SECTIONS = ("overview", "arms", "scenarios", "question_classes", "limitations", "all")
FIELD_HINTS["section"] = "Use one of: " + ", ".join(SECTIONS) + ". 'all' is the default."


def _provenance(meta: dict) -> Provenance:
    return Provenance(data_through=meta["data_through"], results_exported_at=meta["exported_at_utc"],
                      exported_from_commit=meta["exported_from_commit"], schema_version=int(meta["schema_version"]))


def _limitations(arm_cov: dict[str, ArmCoverage], issue_ids: set[str], test_end) -> list[Limitation]:
    items = [
        ("There is no live forecast. Every forecast is a backtest forecast, so tomorrow's carbon intensity cannot be asked for.", []),
        ("Lagged baselines (same period yesterday and last week) have no value for periods that do not exist on a clock-change day, "
         "so a handful of nights are dropped from comparisons that include them.", ["X-5"]),
        ("The NESO wind forecast's history has holes and a model trained with wind degrades when it is missing; wind arms are experiments only.",
         ["WIND-1", "WIND-6"]),
        ("Publication lags (carbon actuals 1 hour, ECMWF runs 8 hours, day-ahead price 10:00 UTC on D-1) are assumptions, not published times.",
         ["X-4", "PX-3", "WX-2"]),
        ("Costs are wholesale only, priced at the realised Market Index price: no network charges, levies or supplier margin.", ["PX-7"]),
    ]
    neso = arm_cov.get("neso_logged")
    if neso is None:
        items.insert(1, ("NESO's own forecast (arm neso_logged) has no stored forecasts in this results set: the daily logger began on "
                         "2026-09-30 and the backtest window ends before it has enough history.", ["X-3", "CI-1"]))
    else:
        items.insert(1, (f"NESO's own forecast (arm neso_logged) covers only {neso.n_half_hours} half-hours, "
                         f"{neso.first:%Y-%m-%d} to {neso.last:%Y-%m-%d}; compare it only on those days.", ["X-3", "CI-1"]))
    return [Limitation(text=t, issue_ids=[i for i in ids if i in issue_ids]) for t, ids in items]


def register(mcp, db: ResultsDb) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="Describe the dataset", read_only_hint=True, idempotent_hint=True,
                                          destructive_hint=False, open_world_hint=False))
    @guard
    def describe_dataset(
        section: Annotated[Literal["overview", "arms", "scenarios", "question_classes", "limitations", "all"], Field(
            description="Which part to return. 'overview' (default) is short: date ranges and every valid name. "
                        "Ask for one detailed section when you need descriptions; 'all' returns everything and is large.")] = "overview",
    ) -> DatasetDescription:
        """Describe what this server can answer: the date ranges, and the valid names for arms (models, baselines,
        strategies), charging scenarios and result classes. Call this first: it tells you which names and dates are
        valid for every other tool. The default 'overview' is short; request a section ('arms', 'scenarios',
        'question_classes', 'limitations') for detail. It does not return any forecast or metric values."""
        db.require_tables()
        meta = db.rows(queries.META)[0]
        cov = db.rows(queries.DATA_COVERAGE)[0]
        issue_ids = {r["id"] for r in db.rows(queries.ISSUE_IDS)}
        arm_cov = {r["arm"]: ArmCoverage(n_half_hours=r["n_half_hours"], first=r["first_ts"], last=r["last_ts"])
                   for r in db.rows(queries.CARBON_ARM_COVERAGE) + db.rows(queries.PRICE_ARM_COVERAGE)}
        want = lambda name: section in ("all", name)  # noqa: E731

        arm_rows = db.rows(queries.ARMS)
        scen_rows = db.rows(queries.SCENARIOS)
        class_arm_rows = db.rows(queries.CLASS_ARMS)
        n_limits = len(_limitations(arm_cov, issue_ids, meta["test_end"]))
        overview = None
        if want("overview"):
            by_kind: dict[str, list[str]] = {}
            for r in arm_rows:
                by_kind.setdefault(r["kind"], []).append(r["name"])
            overview = Overview(
                arm_names_by_kind=by_kind, scenario_names=[r["name"] for r in scen_rows],
                question_classes=list(dict.fromkeys(r["question_class"] for r in class_arm_rows)), limitation_count=n_limits,
                next_step="Pass these names to the other tools. Call describe_dataset again with section='arms', "
                          "'question_classes' or 'limitations' for details.")

        arms = scenarios = classes = limits = None
        if want("arms"):
            arms = [ArmInfo(name=r["name"], kind=r["kind"], target=r["target"], description=r["description"],
                            uses_sources=[s for s in (r["uses_sources"] or "").split(",") if s],
                            note=r["note"] or None, forecast_coverage=arm_cov.get(r["name"]))
                    for r in arm_rows]
        if want("scenarios"):
            scenarios = [ScenarioInfo(**r) for r in scen_rows]
        if want("question_classes"):
            by_arm: dict[str, list[str]] = {}
            for r in class_arm_rows:
                by_arm.setdefault(r["question_class"], []).append(r["arm"])
            by_group: dict[str, list[str]] = {}
            for r in db.rows(queries.CLASS_GROUPS):
                by_group.setdefault(r["question_class"], []).append(r["group_key"])
            classes = [QuestionClassInfo(name=c, description=QUESTION_CLASS_TEXT.get(c, UNDESCRIBED),
                                         group_by=by_group.get(c, []), arms=a) for c, a in by_arm.items()]
        if want("limitations"):
            limits = _limitations(arm_cov, issue_ids, meta["test_end"])
        first, last = meta["test_start"], meta["test_end"]
        return DatasetDescription(
            summary=(f"Day-ahead GB carbon-intensity and wholesale-price forecasts, and EV-charging results, from a "
                     f"rolling-origin backtest over {first} to {last}. Results are out-of-sample."),
            forecast_window=DateRange(first=first, last=last),
            data_window=DateRange(first=cov["first_day"], last=cov["last_day"]),
            sections_returned=[section] if section != "all" else list(SECTIONS[:-1]),
            overview=overview,
            arms=arms, scenarios=scenarios, question_classes=classes, limitations=limits,
            data_issue_count=db.rows(queries.ISSUE_COUNT)[0]["n"], provenance=_provenance(meta))
