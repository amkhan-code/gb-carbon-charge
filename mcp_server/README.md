# gb-carbon-charge MCP server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server over the
[gb-carbon-charge](../README.md) results. An agent can ask what the project found, with typed inputs and outputs,
without touching the database.

**Status: skeleton.** One tool is built; the rest of the surface follows.

| Tool | Status |
|---|---|
| `describe_dataset` | built |
| `get_forecast`, `compare_strategies`, `query_metrics`, `list_data_issues` | next |

## Design in four lines

- **Task-shaped tools, not tables.** There is no SQL tool and no per-table getter: each tool answers a question.
- **Read-only in layers.** The DuckDB connection is opened `read_only=True`; every statement is fixed text with bound
  parameters; tools carry read-only annotations; tests hash the database file before and after every call.
- **Errors an agent can act on.** Every failure is a structured result (code, field, allowed values, did-you-mean,
  hint, retryable). Unexpected exceptions are contained and logged server-side with a correlation id; nothing leaks.
- **Honest about absence.** There is no live forecast. The server says so, and says which arms have no data and why.

## Run

```bash
carbon-charge report                      # writes the results tables into data/carbon.duckdb
uv run python -m carbon_charge_mcp --db data/carbon.duckdb      # stdio
uv run pytest mcp_server/tests
```

Register with Claude Code:

```bash
claude mcp add gb-carbon-charge -- uv run python -m carbon_charge_mcp --db data/carbon.duckdb
```
