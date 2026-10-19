import sys

import mcp.types as types
import pytest
from carbon_charge_mcp.models import DatasetDescription
from carbon_charge_mcp.server import create_server
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer

pytestmark = pytest.mark.anyio


async def test_tool_list_is_the_published_contract(results_db):
    async with Client(create_server(results_db)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == {"describe_dataset", "get_forecast", "compare_strategies", "query_metrics", "list_data_issues"}
        g = tools["get_forecast"]
        props = g.input_schema["properties"]
        assert list(props) == ["date", "target", "models", "detail"] and g.input_schema["required"] == ["date"]
        assert props["date"]["format"] == "date" and props["target"]["enum"] == ["carbon", "price"]
        assert props["detail"]["default"] == "summary" and "no live forecast" in g.description
        assert {"scores", "unavailable_models", "periods_in_day", "provenance"} <= set(g.output_schema["properties"])
        cs = tools["compare_strategies"]
        cp = cs.input_schema["properties"]
        assert list(cp) == ["night", "scenario", "objective", "carbon_price_gbp_per_tonne", "forecast"] and cs.input_schema["required"] == ["night"]
        assert cp["objective"]["enum"] == ["carbon", "cost", "blend"] and cp["scenario"]["default"] == "default"
        assert cs.annotations.read_only_hint is True and "perfect foresight" in cs.description
        assert {"outcomes", "gap_captured_this_night", "window_slots"} <= set(cs.output_schema["properties"])
        qm = tools["query_metrics"]
        assert qm.input_schema["required"] == ["question_class"] and qm.input_schema["properties"]["limit"]["maximum"] == 500
        assert qm.input_schema["properties"]["question_class"]["enum"] == [
            "carbon_forecast_accuracy", "price_forecast_accuracy", "slot_ranking", "charging_value", "cost_carbon_tradeoff", "experiments"]
        ld = tools["list_data_issues"]
        assert ld.input_schema.get("required", []) == [] and "weather" in str(ld.input_schema["properties"]["source"])
        assert all(t.annotations.read_only_hint and t.annotations.idempotent_hint for t in tools.values())
        assert all(t.output_schema for t in tools.values())              # every tool publishes a typed output schema
        t = tools["describe_dataset"]
        assert list(t.input_schema["properties"]) == ["section"]  # flat: no wrapper object around the arguments
        assert t.input_schema["properties"]["section"]["enum"] == ["overview", "arms", "scenarios", "question_classes", "limitations", "all"]
        assert "Call this first" in t.description and "does not return any forecast" in t.description
        assert t.input_schema["properties"]["section"]["default"] == "overview"
        assert {"forecast_window", "arms", "provenance"} <= set(t.output_schema["properties"])
        assert "describe_dataset" in c.instructions and "no live forecast" in c.instructions.lower()


async def test_real_stdio_round_trip(results_db):
    """The server as a subprocess over stdio, the way an MCP client launches it."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "carbon_charge_mcp", "--db", str(results_db)])
    async with Client(params) as c:
        r = await c.call_tool("describe_dataset", {"section": "limitations"})
        assert not r.is_error
        assert DatasetDescription.model_validate(r.structured_content).limitations
        bad = await c.call_tool("describe_dataset", {"section": "nope"})
        assert bad.is_error and bad.structured_content["error_code"] == "INVALID_ARGUMENT"


# --- canaries: behaviour of the SDK that the server relies on (middleware is provisional in 2.x) ----------------

async def test_sdk_canary_validation_failures_reach_middleware_as_a_dict_with_pydantic_text():
    seen = {}

    async def spy(ctx, call_next):
        result = await call_next(ctx)
        if ctx.method == "tools/call":
            seen["result"] = result
        return result

    mcp = MCPServer("canary", middleware=[spy])

    @mcp.tool()
    def f(n: int) -> int:
        return n

    async with Client(mcp) as c:
        await c.call_tool("f", {"n": "x"})
    r = seen["result"]
    assert isinstance(r, dict) and r["isError"] is True and "structuredContent" not in r
    text = r["content"][0]["text"]
    assert "validation error for" in text and "[type=int_parsing" in text and "input_value=" in text


async def test_sdk_canary_a_tool_can_return_a_structured_error_result_directly():
    mcp = MCPServer("canary")

    @mcp.tool()
    def f() -> dict:
        return types.CallToolResult(content=[types.TextContent(type="text", text="x")],
                                    structured_content={"error_code": "X"}, is_error=True)

    async with Client(mcp) as c:
        r = await c.call_tool("f", {})
    assert r.is_error and r.structured_content == {"error_code": "X"}
