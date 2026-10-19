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
