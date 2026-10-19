import ast
import hashlib
import re
from pathlib import Path

import duckdb
import pytest
from carbon_charge_mcp import db as dbmod
from carbon_charge_mcp import queries
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.server import create_server
from mcp import Client

pytestmark = pytest.mark.anyio
SRC = Path(__file__).resolve().parents[1] / "src" / "carbon_charge_mcp"


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() + str(path.stat().st_mtime_ns)


async def test_calling_every_tool_leaves_the_database_file_untouched(results_db):
    before = fingerprint(results_db)
    async with Client(create_server(results_db)) as c:
        for tool in (await c.list_tools()).tools:
            for args in ({}, {"section": "arms"}, {"section": "bogus"}):  # successes and failures
                await c.call_tool(tool.name, args if tool.name == "describe_dataset" else {})
    assert fingerprint(results_db) == before


async def test_the_only_connection_ever_opened_is_read_only(results_db, monkeypatch):
    calls = []
    real = duckdb.connect
    monkeypatch.setattr(dbmod.duckdb, "connect", lambda *a, **k: (calls.append(k), real(*a, **k))[1])
    async with Client(create_server(results_db)) as c:
        await c.call_tool("describe_dataset", {})
    assert calls and all(k.get("read_only") is True for k in calls)


def test_a_write_fails_inside_the_engine_whatever_the_server_does(results_db):
    handle = ResultsDb(results_db)
    con = handle._connect()
    for stmt in ("CREATE TABLE sneaky (a INT)", "DELETE FROM results_meta", "DROP TABLE results_arms",
                 "INSERT INTO results_meta SELECT * FROM results_meta"):
        with pytest.raises(duckdb.Error, match="read-only|read only"):
            con.execute(stmt)
    handle.close()


async def test_no_tool_takes_sql_or_any_free_text_that_could_become_a_query(results_db):
    async with Client(create_server(results_db)) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
            assert not {"sql", "query", "statement", "where", "table"} & set(tool.input_schema["properties"])


def test_every_statement_is_fixed_text_with_no_interpolation_or_stacked_statements():
    stmts = {k: v for k, v in vars(queries).items() if k.isupper() and isinstance(v, str)}
    assert len(stmts) >= 8
    for name, sql in stmts.items():
        assert not re.search(r"[{}%]|;\s*\S", sql), name  # no format placeholders, no second statement
        assert sql.lstrip().upper().startswith("SELECT"), name  # nothing but reads


def test_the_server_imports_only_what_it_is_allowed_to_from_the_pipeline():
    allowed = {"carbon_charge.timeutils", "carbon_charge.charging.strategies", "carbon_charge.charging.scenarios",
               "carbon_charge.charging.blend", "carbon_charge.config"}
    seen = set()
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            seen |= {n for n in names if n.split(".")[0] == "carbon_charge"}
    assert seen <= allowed, f"forbidden imports from the pipeline: {sorted(seen - allowed)}"
    # Spelled out: the server never reaches ingestion, model training, evaluation, features or the writer.
    assert not any(re.search(r"ingest|models|evaluation|features|results_store|\.db$", s) for s in seen)
