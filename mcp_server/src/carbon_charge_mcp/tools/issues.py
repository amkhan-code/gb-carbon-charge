"""list_data_issues: the project's log of known data problems, searchable and bounded."""

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from carbon_charge_mcp import queries
from carbon_charge_mcp.context import meta, provenance
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import FIELD_HINTS, failure, guard, suggest
from carbon_charge_mcp.models import DataIssue, IssueList

FULL_DETAIL_MAX = 5
FIELD_HINTS["id"] = "An issue id such as X-5 or WIND-1 (case does not matter). Omit it to search or list."
FIELD_HINTS["text"] = "At least 2 characters; matched case-insensitively against each issue's id, title and body."
Source = Literal["carbon_intensity", "demand_forecast", "weather", "prices", "wind", "cross_source", "environment"]
_ORDER = ["carbon_intensity", "demand_forecast", "weather", "prices", "wind", "cross_source", "environment", "other"]


def _num(issue_id: str) -> tuple[str, int]:
    prefix, _, n = issue_id.partition("-")
    return (prefix, int(n) if n.isdigit() else 0)


def _short(text: str | None, n: int = 120) -> str | None:
    if not text:
        return None
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def register(mcp, db: ResultsDb) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="List known data issues", read_only_hint=True, idempotent_hint=True,
                                          destructive_hint=False, open_world_hint=False))
    @guard
    def list_data_issues(
        id: Annotated[str | None, Field(description="One issue id, e.g. 'X-5' or 'WIND-1'.")] = None,
        source: Annotated[Source | None, Field(description="Only issues about this data source.")] = None,
        text: Annotated[str | None, Field(min_length=2, max_length=100, description="Words to search for in id, title and body.")] = None,
        flagged_only: Annotated[bool, Field(description="Only the issues the log marks as especially important.")] = False,
        detail: Annotated[Literal["summary", "full"], Field(description=(
            f"'summary' (default) gives id, source, title and status. 'full' adds how it is handled and the whole entry, "
            f"for at most {FULL_DETAIL_MAX} issues at a time."))] = "summary",
        limit: Annotated[int, Field(ge=1, le=50, description="Most issues to return.")] = 20,
    ) -> IssueList:
        """The project's log of known data problems: what is wrong with each source, how it was handled, and whether it
        is still open. Use it to explain why a number looks odd, or to check whether a caveat applies. Ask for one issue
        by id, filter by source, or search by words. Results from the other tools list the ids that apply to them."""
        m = meta(db)
        rows = db.rows(queries.ISSUES)
        ids = [r["id"] for r in rows]
        if id is not None:
            want = id.strip().upper()
            if want not in ids:
                raise failure("UNKNOWN_NAME", f"There is no data issue with id '{id}'.", field="id", got=id, allowed_values=sorted(ids, key=_num),
                              did_you_mean=suggest(want, ids), hint="Ids look like CI-1, X-5, WIND-6. Search with text=... to find one by topic.")
            rows = [r for r in rows if r["id"] == want]
        if source:
            rows = [r for r in rows if r["source"] == source]
        if flagged_only:
            rows = [r for r in rows if r["flagged"]]
        if text:
            needle = text.lower()
            rows = [r for r in rows if needle in f"{r['id']} {r['title']} {r['body']}".lower()]
        rows.sort(key=lambda r: (_ORDER.index(r["source"]) if r["source"] in _ORDER else 99, _num(r["id"])))
        total = len(rows)
        notes: list[str] = []
        shown = rows[:limit]
        returned = detail
        if detail == "full" and len(shown) > FULL_DETAIL_MAX:
            returned, shown = "summary", shown
            notes.append(f"{len(shown)} issues matched, more than the {FULL_DETAIL_MAX} allowed for detail='full', so summaries are "
                         f"returned. Narrow the filter or ask for one id.")
        if total > limit:
            notes.append(f"{total} issues matched; returned the first {limit}. Narrow with source, text or id, or raise limit (maximum 50).")
        if not rows:
            known = {}
            for r in db.rows(queries.ISSUES):
                known[r["source"]] = known.get(r["source"], 0) + 1
            notes.append("No issues matched. Sources with issues: " + ", ".join(f"{k} ({v})" for k, v in known.items()) +
                         ". Try a shorter search or drop a filter.")
        full = returned == "full"
        by_source: dict[str, int] = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        return IssueList(
            count=len(shown), total_matching=total, detail_returned=returned, by_source=by_source, notes=notes,
            issues=[DataIssue(id=r["id"], source=r["source"], title=r["title"], flagged=bool(r["flagged"]),
                              status=(r["status"] or None) if full else _short(r["status"]),
                              handling=(r["handling"] or None) if full else None, body=(r["body"] or None) if full else None)
                    for r in shown], provenance=provenance(m))
