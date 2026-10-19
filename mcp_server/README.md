# gb-carbon-charge MCP server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server over the results of
[gb-carbon-charge](../README.md). An agent can ask what the project found (forecast accuracy, charging strategies,
the cost/carbon trade-off, known data problems) through five typed tools, and never touches the database.

It uses the official Python SDK (2.3), stdio transport, and Pydantic models for every input and output.

## Quick start

```bash
carbon-charge report            # also writes the results tables into data/carbon.duckdb
uv sync --all-packages
uv run python -m carbon_charge_mcp --db data/carbon.duckdb      # speaks MCP on stdio
uv run pytest mcp_server/tests
```

Register it with Claude Code:

```bash
claude mcp add gb-carbon-charge -- uv run --directory /path/to/gb-carbon-charge python -m carbon_charge_mcp --db data/carbon.duckdb
```

or with any client that takes a JSON config (Claude Desktop, Cursor, and so on):

```json
{"mcpServers": {"gb-carbon-charge": {
  "command": "/path/to/gb-carbon-charge/.venv/bin/python",
  "args": ["-m", "carbon_charge_mcp", "--db", "/path/to/gb-carbon-charge/data/carbon.duckdb"]}}}
```

## The tools

| Tool | Question it answers |
|---|---|
| `describe_dataset` | What is here, and what are the valid names? Date ranges, models, strategies, scenarios, known limitations. Call first. |
| `get_forecast` | How did each model's forecast compare with what happened on one day? Carbon or price; per-model MAE, RMSE, bias, and whether it picked the day's cheapest or greenest periods. |
| `compare_strategies` | What did the four charging strategies do on one night? Which half-hours, how much carbon and cost, for a carbon, cost or blended objective. |
| `query_metrics` | What do the results say over the whole period? Accuracy by season or time of day, slot ranking, share of the saving captured (with intervals), the cost/carbon trade-off, experiments. |
| `list_data_issues` | What is known to be wrong with the data, and how is it handled? Search or fetch by id. |

## Design decisions

**The tools are shaped like questions, not tables.** There is no SQL tool and no per-table getter. An agent that
could run SQL would need the schema, could write anything, and would have to join the project's tables itself. Here
each tool answers one kind of question and returns a typed result. The things I rejected, and why: a raw SQL tool
(no boundary), one getter per table (pushes the joins onto the agent), tools that run a backtest or an optimiser
(expensive and not read-only), and a live forecast tool (the project has none, and the server says so).

**Answers are small by default.** `describe_dataset` returns a short overview (about 2,400 characters) and the
detail on request; `get_forecast` returns scores, with the 46 to 50 half-hours only if asked; `list_data_issues`
limits full entries to five at a time; `query_metrics` caps rows and says how to narrow. An agent pays for every
character it reads.

**Every number carries its provenance and its caveats.** Results say they are out-of-sample backtest forecasts, when
and from which commit they were exported, the sample size behind them, and the data issues that affect how to read
them. Where arms were scored on different samples (carbon on 631 nights, cost on 627), the response says so.

**Absence is reported, not hidden.** There is no live forecast; NESO's own forecast has no stored results yet. Both
are stated in the answer, with the reason, instead of returning nothing.

### Errors an agent can act on

Every failure is an MCP error result with structured content: a stable code, the offending field, what would be
valid, close matches, and a hint. What an agent actually receives, from the real database:

```
OUT_OF_RANGE: The day 2026-10-12 is outside the stored forecast window (2025-01-01 to 2026-09-29). There is no live
forecast, so dates after the window cannot be answered.
  field: date
  got: 2026-10-12
  valid range: 2025-01-01 to 2026-09-29
  did you mean: 2026-09-29
  hint: Call describe_dataset (section='overview') for the window. Retry with a date inside it.
  retryable: yes
```

```
UNKNOWN_NAME: Unknown arm: 'lgbmprice'.
  field: arms
  got: lgbmprice
  allowed values: arrival, da_basis, day_ahead, last_week, lgbm, lgbm_both, ... timer, yesterday
  did you mean: lgbm, lgbm_price
  hint: Valid names are listed in describe_dataset (section='overview').
  retryable: yes
```

```
INCOMPLETE_DATA: Not enough data to compare strategies on 2025-06-28 for objective 'cost': realised price: 7 of 26
slots missing (first: 28 Jun 23:00, 28 Jun 23:30, 29 Jun 00:00).
  hint: Try another night. For objective='carbon' only carbon data is needed, so a night with missing prices can still
  be answered. Known issue PX-4: list_data_issues(id='PX-4').
```

That last night still works with `objective='carbon'`: the tool degrades gracefully, returning carbon results with
`cost_gbp` null and a note, instead of refusing the whole question.

`retryable` means "correcting the arguments and calling again can succeed". Unexpected exceptions are contained: the
agent gets an `INTERNAL` error with a correlation id and no detail; the traceback goes to stderr, keyed by that id.

### Read-only, in layers

1. The DuckDB connection is opened with `read_only=True`, so a write fails inside the engine whatever a tool does.
2. Every statement is fixed text with bound parameters (`queries.py`); no tool takes SQL, and no identifier is ever
   built from an argument.
3. Every tool is annotated read-only and idempotent.
4. Tests hash the database file before and after calling every tool, check that no write connection is ever opened,
   check that writes fail in the engine, and scan the source so the server imports only the pure parts of the pipeline
   (time handling and charging plans), never ingestion, training or the results writer.

The knowledge of what each result file means lives in the pipeline (`carbon-charge export-results`), which writes one
tidy table of results into DuckDB. The server is a thin typed reader over it.

## Tests

About 110 tests, run with `uv run pytest mcp_server/tests`:

- **Tool contracts** through the SDK's in-process client: output validates against the published schema; scores equal
  hand-computed values on synthetic days with exact forecast errors; results equal the pipeline's own report files.
- **Agreement with the pipeline:** `compare_strategies` reproduces the pipeline's own simulators to the gram and the penny.
- **Clock changes:** 46- and 50-period days, and nights with 24 and 28 half-hours, including local times that repeat.
- **Failure cases**, one per kind: out-of-range dates, malformed arguments, unknown names, nights a scenario skips,
  incomplete data, empty results, oversized results, unexpected exceptions.
- **A real stdio round trip** with the server running as a subprocess.
- **Canaries for the SDK**: the middleware hook used to reshape the SDK's own validation errors is marked provisional in
  2.x, so the minor version is pinned and tests fail loudly if its behaviour changes.

I checked that the safety tests are not vacuous by deliberately breaking each safeguard (opening the database
read-write, removing the error middleware and the exception guard, planting a forbidden import, making "perfect
foresight" plan with the forecast) and confirming the suite fails each time.

## Transcripts: a real agent answering through the server

<!-- TRANSCRIPTS:START -->
_Not generated yet. Run `uv run python mcp_server/scripts/run_transcripts.py --embed`: it asks a headless agent four
questions (using only this server), saves the raw event streams and puts the readable transcripts here._
<!-- TRANSCRIPTS:END -->

### What the transcripts changed

The first run of the first question (kept unedited in [`transcripts/archive/`](transcripts/archive/)) had two flaws
in the agent's answer. It said the data covered "roughly 3+ years", when 30,564 half-hours is 21 months, and it
called a difference "statistically significant" on the strength of the sample size alone, although those accuracy
rows carry no confidence intervals and half-hours within a night are not independent. Both traced to information the
tool had not given, so I fixed the tool rather than the transcript: `query_metrics` now states the evaluation period
and how long it is, and says plainly when a result class has no intervals and where to find them
(`charging_value`, scored per night). The transcripts above are from a rerun after that change; the agent's words are
never edited. The other three first-run answers matched the data and are unchanged in substance.

## Limits

- **stdio only.** No HTTP transport and no authentication; it is meant to run as a local subprocess of the client.
- **It serves stored results, not live data.** Nothing here forecasts tomorrow. The results are as fresh as the last
  `carbon-charge report`, and the provenance on every answer says when that was.
- **Model names are data, not part of the tool schema.** They are validated at call time against `describe_dataset`, so
  the schema cannot go stale, at the cost of a less specific schema.
