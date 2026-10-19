"""Typed outputs. Every tool returns one of these models, so the output schema is published to the client."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SOURCE_NOTE = ("All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00 UK time "
               "on the day before the day they forecast. Nothing here is a live forecast.")


class Provenance(BaseModel):
    data_through: date = Field(description="Last complete day with actual carbon-intensity data.")
    results_exported_at: datetime = Field(description="When the results were last exported into the database (UTC). The underlying analysis may have been run slightly earlier.")
    exported_from_commit: str | None = Field(description="Git commit checked out when the results were exported, if known. It identifies the code at export time, not necessarily the code that computed every number.")
    schema_version: int
    out_of_sample: Literal[True] = True
    note: str = SOURCE_NOTE


class DateRange(BaseModel):
    first: date
    last: date


class ArmCoverage(BaseModel):
    n_half_hours: int
    first: datetime = Field(description="First forecast timestamp (UTC).")
    last: datetime = Field(description="Last forecast timestamp (UTC).")


class ArmInfo(BaseModel):
    name: str = Field(description="The name to pass wherever a tool takes an arm or model.")
    kind: str = Field(description="carbon_model, price_model, baseline, reference, strategy, pipeline or experiment_variant.")
    target: str = Field(description="carbon, price or charging.")
    description: str
    uses_sources: list[str] = Field(description="Kinds of input the arm uses (never anything published after the cutoff).")
    note: str | None = None
    forecast_coverage: ArmCoverage | None = Field(
        default=None, description="Where stored forecasts exist for this arm. Null for strategies and pipelines, "
                                  "or when the arm has no stored forecasts.")


class ScenarioInfo(BaseModel):
    name: str
    plug_in: str = Field(description="Local (UK) time the car plugs in on the evening of the night date.")
    deadline: str = Field(description="Local (UK) time the car must be ready, the next morning.")
    energy_kwh: float
    power_kw: float
    nights: Literal["every", "mon_wed_fri"]


class QuestionClassInfo(BaseModel):
    name: str
    description: str
    group_by: list[str] = Field(description="Ways the results can be broken down (group_key values).")
    arms: list[str] = Field(description="Arms that have results for this class.")


class Limitation(BaseModel):
    text: str
    issue_ids: list[str] = Field(default_factory=list, description="Entries in the data-issues log that explain it.")


class Overview(BaseModel):
    arm_names_by_kind: dict[str, list[str]] = Field(description="Every valid arm name, grouped by kind. Names only; ask for section='arms' for descriptions.")
    scenario_names: list[str]
    question_classes: list[str]
    limitation_count: int
    next_step: str


class DatasetDescription(BaseModel):
    summary: str
    forecast_window: DateRange = Field(description="Days that have stored out-of-sample forecasts.")
    data_window: DateRange = Field(description="Days with actual carbon-intensity data (a wider range).")
    sections_returned: list[str]
    overview: Overview | None = None
    arms: list[ArmInfo] | None = None
    scenarios: list[ScenarioInfo] | None = None
    question_classes: list[QuestionClassInfo] | None = None
    limitations: list[Limitation] | None = None
    data_issue_count: int
    provenance: Provenance


class PeriodRow(BaseModel):
    settlement_period: int
    start_utc: datetime
    start_local: datetime = Field(description="Start of the period in UK local time (clock-change days have 46 or 50 periods).")
    actual: float | None
    forecasts: dict[str, float | None] = Field(description="Forecast by arm; null where that arm has no value for the period.")


class ModelDayScore(BaseModel):
    model: str
    n_periods: int = Field(description="Periods with both an actual and this arm's forecast.")
    mae: float
    rmse: float
    bias: float = Field(description="Mean of forecast minus actual: positive means over-forecasting.")
    forecast_mean: float
    actual_mean: float
    lowest_3_forecast: list[int] = Field(description="The 3 settlement periods this arm forecast lowest (greenest for carbon, cheapest for price).")
    lowest_3_actual: list[int] = Field(description="The 3 periods that actually were lowest.")
    overlap_top3: float = Field(description="Share of those 3 periods the forecast got right, 0 to 1.")


class Unavailable(BaseModel):
    model: str
    reason: str


class DayForecast(BaseModel):
    date: date
    target: Literal["carbon", "price"]
    unit: str
    periods_in_day: Literal[46, 48, 50]
    periods_with_actuals: int
    forecasts_issued_by_utc: datetime = Field(description="The cutoff (11:00 UK on the previous day): every forecast was made from data published by then.")
    scores: list[ModelDayScore]
    unavailable_models: list[Unavailable]
    periods: list[PeriodRow] | None = Field(default=None, description="Only when detail='periods'.")
    notes: list[str] = Field(default_factory=list)
    provenance: Provenance


class ChargeSlot(BaseModel):
    start_utc: datetime
    start_local: datetime = Field(description="UK local time. On the autumn clock-change night 01:00-02:00 happens twice, so use start_utc to tell the two apart.")
    kwh: float


class StrategyOutcome(BaseModel):
    strategy: Literal["arrival", "timer", "forecast_optimised", "perfect_foresight"]
    slots: list[ChargeSlot] = Field(description="The half-hours this strategy charges in, in time order.")
    energy_kwh: float
    carbon_g: float | None = Field(description="Carbon emitted, priced at actual carbon intensity. Null if actuals are incomplete.")
    cost_gbp: float | None = Field(description="Wholesale cost at the realised market price. Null if prices are incomplete.")
    objective_value: float = Field(description="The quantity the plan minimises, in the units of objective_unit. Lower is better.")
    vs_timer_objective_pct: float | None = Field(description="Saving against the overnight timer on the objective. Negative means worse than the timer.")
    vs_timer_carbon_pct: float | None
    vs_timer_cost_pct: float | None


class ForecastUsed(BaseModel):
    selected: str
    carbon_arm: str | None
    price_arm: str | None


class NightComparison(BaseModel):
    night: date = Field(description="The evening the car plugs in; it must be ready the next morning.")
    scenario: ScenarioInfo
    window_start_local: datetime
    window_end_local: datetime
    window_slots: int = Field(description="Half-hours available. 26 normally, 24 or 28 on a night with a clock change.")
    objective: Literal["carbon", "cost", "blend"]
    objective_unit: str
    carbon_price_gbp_per_tonne: float | None
    forecast: ForecastUsed
    outcomes: list[StrategyOutcome] = Field(description="arrival, timer, forecast_optimised, perfect_foresight, in that order.")
    gap_captured_this_night: float | None = Field(
        description="(timer - forecast_optimised) / (timer - perfect_foresight) for this one night; null when the timer was already optimal.")
    notes: list[str] = Field(default_factory=list)
    provenance: Provenance


class MetricRow(BaseModel):
    target: str | None = Field(default=None, description="carbon, price or charging: what the arm is being measured on.")
    arm: str
    vs_arm: str | None = Field(default=None, description="For paired comparisons: the arm this one is compared against.")
    group_key: str
    group_value: str
    metric: str
    value: float
    ci_low: float | None = Field(default=None, description="Lower end of a 95% interval, where one exists.")
    ci_high: float | None = None
    n: int | None = Field(default=None, description="Sample size, in n_unit.")
    n_unit: str | None = Field(default=None, description="half_hours or nights.")
    unit: str


class Caveat(BaseModel):
    issue_id: str
    title: str


class MetricResult(BaseModel):
    question_class: str
    evaluation_period: str | None = Field(
        default=None, description="The days these results cover, and how long that is, so sample sizes can be put in context.")
    filters_applied: dict[str, str | list[str]] = Field(description="The filters actually used, including defaults the server applied.")
    rows: list[MetricRow]
    total_rows: int = Field(description="Rows matching before the limit was applied.")
    truncated: bool
    sample_sizes: dict[str, list[int]] = Field(description="Distinct sample sizes by unit. Differing sizes mean arms were scored on different samples.")
    caveats: list[Caveat] = Field(description="Known data issues that affect how to read these numbers.")
    notes: list[str] = Field(default_factory=list)
    provenance: Provenance


class DataIssue(BaseModel):
    id: str
    source: str
    title: str
    flagged: bool = Field(description="True for issues the log marks as especially important.")
    status: str | None = None
    handling: str | None = Field(default=None, description="How the pipeline deals with it. Only with detail='full'.")
    body: str | None = Field(default=None, description="The full log entry. Only with detail='full'.")


class IssueList(BaseModel):
    count: int
    total_matching: int
    issues: list[DataIssue]
    detail_returned: Literal["summary", "full"]
    by_source: dict[str, int] = Field(description="Matching issues per source.")
    notes: list[str] = Field(default_factory=list)
    provenance: Provenance
