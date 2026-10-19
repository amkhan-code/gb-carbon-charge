import pytest
from carbon_charge_mcp.errors import ErrorPayload
from carbon_charge_mcp.models import IssueList
from carbon_charge_mcp.server import create_server
from conftest import REPO
from mcp import Client

pytestmark = pytest.mark.anyio
HEADINGS = [ln for ln in (REPO / "DATA_ISSUES.md").read_text().splitlines() if ln.startswith("### ")]


async def call(db_path, args):
    async with Client(create_server(db_path)) as c:
        return await c.call_tool("list_data_issues", args)


def ok(r) -> IssueList:
    assert not r.is_error, r.content[0].text
    return IssueList.model_validate(r.structured_content)


def err(r) -> ErrorPayload:
    assert r.is_error and r.structured_content
    return ErrorPayload.model_validate(r.structured_content)


async def test_listing_everything_is_bounded_and_complete_in_count(results_db):
    r = await call(results_db, {"limit": 50})
    res = ok(r)
    assert res.total_matching == len(HEADINGS) == res.count and res.detail_returned == "summary"
    assert all(i.handling is None and i.body is None for i in res.issues)             # summaries stay short
    assert len(r.content[0].text) < 14000
    assert sum(res.by_source.values()) == res.count and {"carbon_intensity", "prices", "wind", "cross_source"} <= set(res.by_source)
    default = ok(await call(results_db, {}))
    assert default.count == 20 and default.total_matching == len(HEADINGS) and any("returned the first 20" in n for n in default.notes)


async def test_one_issue_by_id_is_case_insensitive_and_full_detail_includes_handling(results_db):
    res = ok(await call(results_db, {"id": "x-5", "detail": "full"}))
    (i,) = res.issues
    assert (i.id, i.source) == ("X-5", "cross_source") and "clock-change" in i.title.lower()
    assert i.handling and "dropped from every comparison" in i.handling and i.body and "5 nights" in i.body
    assert res.detail_returned == "full"
    flagged = ok(await call(results_db, {"id": "CI-1"})).issues[0]
    assert flagged.flagged and "FLAGGED" not in flagged.title


async def test_filters_combine_and_agree_with_the_log(results_db):
    wind = ok(await call(results_db, {"source": "wind", "limit": 50}))
    assert {i.id for i in wind.issues} == {f"WIND-{n}" for n in range(1, 7)}
    only_flagged = ok(await call(results_db, {"flagged_only": True}))
    assert [i.id for i in only_flagged.issues] == ["CI-1"]
    both = ok(await call(results_db, {"source": "prices", "text": "UTC"}))
    assert both.count >= 1 and all(i.source == "prices" for i in both.issues) and "PX-1" in {i.id for i in both.issues}


async def test_text_search_covers_titles_and_bodies_case_insensitively(results_db):
    hits = {i.id for i in ok(await call(results_db, {"text": "CLOCK-CHANGE", "limit": 50})).issues}
    assert {"X-5", "DEM-5", "PX-1"} <= hits
    assert ok(await call(results_db, {"text": "zzzz-nothing-like-this"})).count == 0


async def test_full_detail_is_limited_so_answers_stay_small(results_db):
    res = ok(await call(results_db, {"detail": "full", "source": "carbon_intensity"}))      # 5 issues: allowed
    assert res.detail_returned == "full" and all(i.body for i in res.issues)
    many = ok(await call(results_db, {"detail": "full", "limit": 50}))
    assert many.detail_returned == "summary" and any("detail='full'" in n for n in many.notes)
    assert all(i.body is None for i in many.issues)


async def test_nothing_matching_says_where_issues_do_exist(results_db):
    res = ok(await call(results_db, {"source": "weather", "text": "zzzzqq"}))
    assert res.count == 0 and any("Sources with issues" in n and "wind" in n for n in res.notes)


@pytest.mark.parametrize("bad,expect", [("x5", "X-5"), ("WIND6", "WIND-6"), ("CI-99", None)])
async def test_unknown_id_is_refused_with_suggestions(results_db, bad, expect):
    p = err(await call(results_db, {"id": bad}))
    assert p.error_code == "UNKNOWN_NAME" and p.field == "id" and "X-5" in p.allowed_values and p.retryable
    if expect:
        assert expect in p.did_you_mean


@pytest.mark.parametrize("args", [{"source": "weather_forecast"}, {"text": "x"}, {"limit": 0}, {"limit": 51}, {"detail": "all"},
                                  {"flagged_only": "maybe"}])
async def test_malformed_arguments_are_structured(results_db, args):
    p = err(await call(results_db, args))
    assert p.error_code == "INVALID_ARGUMENT" and p.problems
    if "source" in args:
        assert "weather" in p.did_you_mean and "prices" in p.allowed_values and p.hint == "Choose one of the allowed values."
