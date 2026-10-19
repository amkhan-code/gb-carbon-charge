"""Helpers shared by the tools: the forecast window, provenance, and name resolution."""

from datetime import date

from carbon_charge_mcp import queries
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import failure, suggest
from carbon_charge_mcp.models import Provenance


def meta(db: ResultsDb) -> dict:
    db.require_tables()
    return db.rows(queries.META)[0]


def provenance(m: dict) -> Provenance:
    return Provenance(data_through=m["data_through"], results_exported_at=m["exported_at_utc"],
                      exported_from_commit=m["exported_from_commit"], schema_version=int(m["schema_version"]))


def as_date(v) -> date:
    return v.date() if hasattr(v, "date") and callable(v.date) else v


def window(m: dict) -> tuple[date, date]:
    return as_date(m["test_start"]), as_date(m["test_end"])


def check_in_window(day: date, m: dict, *, field: str, last_override: date | None = None, what: str = "day") -> None:
    first, last = window(m)
    last = last_override or last
    if first <= day <= last:
        return
    nearest = min(max(day, first), last)
    after = day > last
    raise failure(
        "OUT_OF_RANGE",
        f"The {what} {day} is outside the stored forecast window ({first} to {last})."
        + (" There is no live forecast, so dates after the window cannot be answered." if after else ""),
        field=field, got=str(day), valid_range=f"{first} to {last}", did_you_mean=[str(nearest)],
        hint="Call describe_dataset (section='overview') for the window. Retry with a date inside it.")


def sentence_short(text: str | None, limit: int = 260) -> str | None:
    """Shorten at a sentence boundary, never mid-sentence."""
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > 60 else cut.rsplit(" ", 1)[0] + " ...").strip()


def resolve_names(requested: list[str], valid: list[str], *, field: str, noun: str = "name") -> list[str]:
    """Validate names against the real vocabulary; unknown ones get suggestions, never a guess."""
    seen = list(dict.fromkeys(requested))
    unknown = [n for n in seen if n not in valid]
    if unknown:
        raise failure("UNKNOWN_NAME", f"Unknown {noun}: {', '.join(repr(u) for u in unknown)}.", field=field,
                      got=", ".join(unknown), allowed_values=valid,
                      did_you_mean=sorted({s for u in unknown for s in suggest(u, valid, 2)}),
                      hint="Valid names are listed in describe_dataset (section='overview').")
    return seen
