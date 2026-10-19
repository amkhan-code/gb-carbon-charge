"""Builds the MCP server. Stdio transport; read-only over the project's DuckDB results."""

import os
from pathlib import Path

from mcp.server import MCPServer

from carbon_charge_mcp import __version__
from carbon_charge_mcp.db import ResultsDb
from carbon_charge_mcp.errors import shape_errors
from carbon_charge_mcp.tools import describe, forecast, issues, metrics, strategies

INSTRUCTIONS = (
    "Read-only access to the results of the gb-carbon-charge project: day-ahead GB carbon-intensity forecasts, "
    "wholesale-price forecasts and EV-charging results from an out-of-sample backtest. "
    "Call describe_dataset first: it gives the valid date ranges and the names of models, strategies and scenarios. "
    "Use get_forecast to look at one day, compare_strategies for one charging night, query_metrics for results over the "
    "whole period, and list_data_issues to understand known data problems. "
    "There is no live forecast, and nothing here can change the data. Errors are structured: read error_code, "
    "allowed_values, valid_range and hint, then retry with corrected arguments."
)
DEFAULT_DB = "data/carbon.duckdb"


def resolve_db_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("CARBON_CHARGE_DB") or DEFAULT_DB)


def create_server(db_path: str | Path | None = None) -> MCPServer:
    db = ResultsDb(resolve_db_path(db_path))
    mcp = MCPServer("gb-carbon-charge", title="GB carbon charging results", version=__version__,
                    instructions=INSTRUCTIONS, middleware=[shape_errors])
    describe.register(mcp, db)
    forecast.register(mcp, db)
    strategies.register(mcp, db)
    metrics.register(mcp, db)
    issues.register(mcp, db)
    return mcp
