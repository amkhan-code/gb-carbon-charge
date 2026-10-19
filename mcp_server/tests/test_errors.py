import logging
import re
from typing import Literal

import duckdb
import pytest
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import ErrorPayload
from carbon_charge_mcp.server import create_server
from carbon_charge_mcp.tools.describe import SECTIONS
from mcp import Client

pytestmark = pytest.mark.anyio


async def call(server, name="describe_dataset", args=None):
    async with Client(server) as c:
        return await c.call_tool(name, args or {})


def payload(r) -> ErrorPayload:
    assert r.is_error and r.structured_content, "error results must carry structured content"
    return ErrorPayload.model_validate(r.structured_content)


async def test_mistyped_enum_gets_allowed_values_and_a_suggestion_not_a_stack_trace(results_db):
    r = await call(create_server(results_db), args={"section": "arm"})
    p = payload(r)
    assert (p.error_code, p.field, p.got) == ("INVALID_ARGUMENT", "section", "arm")
    assert p.allowed_values == list(SECTIONS) and p.did_you_mean == ["arms"] and p.retryable
    assert "Use one of" in p.hint
    text = r.content[0].text
    assert "did you mean: arms" in text
    for noise in ("pydantic", "errors.pydantic.dev", "input_type", "Traceback", "describe_datasetArguments"):
        assert noise not in text


async def test_wrong_type_is_a_structured_invalid_argument(results_db):
    p = payload(await call(create_server(results_db), args={"section": 5}))
    assert p.error_code == "INVALID_ARGUMENT" and p.field == "section"


async def test_several_problems_are_all_reported(results_db):
    server = create_server(results_db)

    @server.tool()
    def probe(n: int, mode: Literal["fast", "slow"] = "fast") -> dict:
        """A test-only tool with two arguments."""
        return {"n": n}

    p = payload(await call(server, "probe", {"n": "many", "mode": "fsat"}))
    assert [x.field for x in p.problems] == ["n", "mode"]
    assert p.problems[1].did_you_mean == ["fast"] and p.problems[1].allowed_values == ["fast", "slow"]
    assert "also: mode" in (await call(server, "probe", {"n": "many", "mode": "fsat"})).content[0].text
    p = payload(await call(server, "probe", {}))
    assert p.problems[0].message == "The required argument 'n' is missing."


async def test_a_missing_database_file_says_how_to_build_it(tmp_path):
    p = payload(await call(create_server(tmp_path / "nope.duckdb")))
    assert p.error_code == "NOT_AVAILABLE" and "carbon-charge" in p.hint and not p.retryable


async def test_a_database_without_the_results_tables_names_what_is_missing(tmp_path):
    path = tmp_path / "bare.duckdb"
    duckdb.connect(str(path)).execute("CREATE TABLE unrelated (a INT)")
    p = payload(await call(create_server(path)))
    assert p.error_code == "NOT_AVAILABLE" and "results_meta" in p.message and "carbon-charge report" in p.hint


async def test_unexpected_exceptions_are_contained_logged_and_never_leaked(results_db, monkeypatch, caplog):
    secret = "/Users/someone/private/path.duckdb"

    def boom(self, sql, *params):
        raise RuntimeError(f"disk exploded at {secret}")

    monkeypatch.setattr(ResultsDb, "require_tables", lambda self: None)
    monkeypatch.setattr(ResultsDb, "rows", boom)
    with caplog.at_level(logging.ERROR, logger="carbon_charge_mcp"):
        r = await call(create_server(results_db))
    p = payload(r)
    assert p.error_code == "INTERNAL" and re.fullmatch(r"[0-9a-f]{8}", p.correlation_id) and not p.retryable
    assert secret not in r.content[0].text and "exploded" not in r.content[0].text
    assert secret in caplog.text and p.correlation_id in caplog.text  # the detail stays server-side, findable by id
