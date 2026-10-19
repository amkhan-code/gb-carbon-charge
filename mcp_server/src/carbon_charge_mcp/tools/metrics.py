"""query_metrics: aggregate results over the whole backtest, by question class."""

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from carbon_charge_mcp import queries
from carbon_charge_mcp.context import meta, provenance, resolve_names
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import FIELD_HINTS, failure, guard, suggest
from carbon_charge_mcp.models import Caveat, MetricResult, MetricRow
from carbon_charge_mcp.vocab import CLASS_CAVEATS, DEFAULT_GROUP, QUESTION_CLASS_TEXT, group_sort_key

QuestionClass = Literal["carbon_forecast_accuracy", "price_forecast_accuracy", "slot_ranking", "charging_value",
                        "cost_carbon_tradeoff", "experiments"]
FIELD_HINTS["question_class"] = "Pick one of: " + ", ".join(QUESTION_CLASS_TEXT) + ". describe_dataset (section='question_classes') explains each."
FIELD_HINTS["limit"] = "An integer from 1 to 500."
SCENARIO_CLASSES = ("charging_value", "cost_carbon_tradeoff")


def register(mcp, db: ResultsDb) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="Query aggregate metrics", read_only_hint=True, idempotent_hint=True,
                                          destructive_hint=False, open_world_hint=False))
    @guard
    def query_metrics(
        question_class: Annotated[QuestionClass, Field(description=(
            "What kind of result: carbon_forecast_accuracy, price_forecast_accuracy, slot_ranking, charging_value, "
            "cost_carbon_tradeoff or experiments."))],
        arms: Annotated[list[str] | None, Field(min_length=1, max_length=20, description=(
            "Only these arms (models, baselines, strategies, pipelines). Omit for all that have results."))] = None,
        group_by: Annotated[str | None, Field(description=(
            "How to break the results down, e.g. overall, season, time_of_day, local_hour. Omit for the class default."))] = None,
        metrics: Annotated[list[str] | None, Field(min_length=1, max_length=10, description=(
            "Only these metrics, e.g. ['mae']. Omit for all."))] = None,
        scenario: Annotated[str | None, Field(description=(
            "Charging scenario (charging_value and cost_carbon_tradeoff only). Defaults to 'default'."))] = None,
        objective: Annotated[Literal["carbon", "cost"] | None, Field(description=(
            "charging_value only: the carbon or the wholesale-cost result. Defaults to 'carbon'."))] = None,
        eval_window: Annotated[str | None, Field(description=(
            "experiments only: which evaluation window, e.g. the test period or the development window. Defaults to the test period."))] = None,
        limit: Annotated[int, Field(ge=1, le=500, description="Most rows to return.")] = 200,
    ) -> MetricResult:
        """Aggregate results over the whole backtest: forecast error (MAE, RMSE), slot-ranking accuracy, the share of the
        timer-to-perfect-foresight gap each charging strategy captures, the cost/carbon trade-off, and model experiments,
        with confidence intervals where they exist. Use it for questions about the whole period; for one day use
        get_forecast, for one night use compare_strategies. Every result lists the sample size and the known data
        issues that affect how to read it. Call describe_dataset first for valid arm and scenario names."""
        m = meta(db)
        qc = question_class
        class_arms = [r["arm"] for r in db.rows(queries.CLASS_ARMS) if r["question_class"] == qc]
        group_keys = [r["group_key"] for r in db.rows(queries.CLASS_GROUPS) if r["question_class"] == qc]
        class_metrics = [r["metric"] for r in db.rows(queries.CLASS_METRICS, qc)]
        all_arms = {r["name"] for r in db.rows(queries.ARMS)}

        group = group_by or DEFAULT_GROUP.get(qc) or ("overall" if "overall" in group_keys else group_keys[0])
        if group not in group_keys:
            raise failure("INVALID_ARGUMENT", f"'{group}' is not a way to break down {qc}.", field="group_by", got=group,
                          allowed_values=group_keys, did_you_mean=suggest(group, group_keys),
                          hint=f"Use one of the allowed values, or omit group_by (default '{DEFAULT_GROUP.get(qc, 'overall')}').")
        for name, val, ok_classes in (("scenario", scenario, SCENARIO_CLASSES), ("objective", objective, ("charging_value",)),
                                      ("eval_window", eval_window, ("experiments",))):
            if val is not None and qc not in ok_classes:
                raise failure("INVALID_ARGUMENT", f"'{name}' does not apply to {qc}; it applies to {', '.join(ok_classes)}.",
                              field=name, got=str(val), hint=f"Remove '{name}' or choose a question_class it applies to.")
        eff_scenario = scenario or ("default" if qc in SCENARIO_CLASSES else None)
        eff_objective = objective or ("carbon" if qc == "charging_value" else None)
        if scenario is not None:
            resolve_names([scenario], [r["name"] for r in db.rows(queries.SCENARIOS)], field="scenario", noun="scenario")
        windows = [r["eval_window"] for r in db.rows(queries.EVAL_WINDOWS)]
        eff_window = eval_window or (windows[-1] if qc == "experiments" and windows else None)
        if eval_window is not None:
            resolve_names([eval_window], windows, field="eval_window", noun="evaluation window")
        if metrics:
            resolve_names(metrics, class_metrics, field="metrics", noun="metric")
        notes: list[str] = []
        wanted = None
        if arms:
            unknown = [a for a in dict.fromkeys(arms) if a not in all_arms]
            if unknown:
                resolve_names(unknown, sorted(all_arms), field="arms", noun="arm")
            wanted = list(dict.fromkeys(arms))
            missing = [a for a in wanted if a not in class_arms]
            if missing:
                notes.append(f"No {qc} results for {', '.join(missing)}. Arms that have results: {', '.join(class_arms)}.")

        rows = db.rows(queries.METRIC_ROWS, qc, group, eff_scenario, eff_scenario, eff_objective, eff_objective, eff_window, eff_window)
        rows = [r for r in rows if (wanted is None or r["arm"] in wanted) and (not metrics or r["metric"] in metrics)]
        rows.sort(key=lambda r: (group_sort_key(group, r["group_value"]), r["arm"], r["metric"]))
        total = len(rows)
        if total > limit:
            notes.append(f"{total} rows matched; returned the first {limit}. Narrow with arms, metrics or group_by, or raise limit (maximum 500).")
        rows = rows[:limit]
        if not rows and not notes:
            notes.append("No rows matched these filters. Try omitting arms, metrics or the other filters.")
        if group == "reference_sample":
            notes.append("reference_sample scores arms on the half-hours where the NESO latest-revision field exists. That field is "
                         "NOT a day-ahead forecast (CI-1), so it is a reference, not a baseline.")
        if qc == "experiments":
            notes.append("Draw conclusions from the test-window rows. Development-window rows were used to choose what to test.")
        sizes: dict[str, list[int]] = {}
        for r in rows:
            if r["n"] is not None and r["n_unit"]:
                sizes.setdefault(r["n_unit"], [])
                if int(r["n"]) not in sizes[r["n_unit"]]:
                    sizes[r["n_unit"]].append(int(r["n"]))
        if any(len(v) > 1 for v in sizes.values()):
            notes.append("These rows have different sample sizes, so the arms were not all scored on the same sample; compare with care.")
        titles = {r["id"]: r["title"] for r in db.rows(queries.ISSUE_TITLES)}
        filters = {"group_by": group}
        for k, v in (("scenario", eff_scenario), ("objective", eff_objective), ("eval_window", eff_window),
                     ("arms", wanted), ("metrics", metrics)):
            if v:
                filters[k] = v
        return MetricResult(
            question_class=qc, filters_applied=filters, truncated=total > limit, total_rows=total,
            rows=[MetricRow(**r) for r in rows], sample_sizes={k: sorted(v) for k, v in sizes.items()}, notes=notes,
            caveats=[Caveat(issue_id=i, title=titles[i]) for i in CLASS_CAVEATS.get(qc, []) if i in titles], provenance=provenance(m))
