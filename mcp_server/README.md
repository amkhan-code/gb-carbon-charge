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
Each transcript is a real run: a headless Claude Code agent with every built-in tool disabled, so the MCP server was its only way to find anything out. The raw event streams are in `transcripts/*.jsonl`; local paths were replaced with placeholders. Generated by `scripts/run_transcripts.py`.

### Did the LightGBM carbon model beat the 'same period yesterday' baseline, and by how much? Is the difference reliable?

**Tool call:** `query_metrics`
```json
{
  "question_class": "carbon_forecast_accuracy"
}
```

**Tool result:** `query_metrics` (3,910 characters)
```
{"question_class":"carbon_forecast_accuracy","evaluation_period":"2025-01-01 to 2026-09-29: 637 days (about 21 months)","filters_applied":{"group_by":"overall"},"rows":[{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":53.87,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":68.36,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":19.17,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":24.7,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":32.94,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":41.21,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":18.05,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":23.37,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":
... [2,110 more characters in the raw transcript]
```

**Agent:** Yes, LightGBM (`lgbm`) clearly beat the "same period yesterday" (`yesterday`) baseline, and by a large margin:

| Model | MAE (gCO2/kWh) | RMSE (gCO2/kWh) |
|---|---|---|
| `lgbm` | 19.17 | 24.70 |
| `yesterday` | 43.45 | 55.95 |

**LightGBM reduces MAE by ~56% and RMSE by ~56%** relative to the yesterday baseline. That's a substantial improvement — the model roughly halves the raw error.

---

### Is the difference reliable?

**Directionally, yes — but the formal statistics are limited:**

- No confidence intervals are reported for these per-half-hour error metrics. The caveat in the results notes that half-hours within a single night are not independent, so the n=30,564 half-hours overstate the true effective sample size.
- The evaluation period is ~21 months (637 days), which is a reasonably large out-of-sample window, and the gap (24 vs 43 MAE) is large enough that it would almost certainly survive any reasonable correction for within-night correlation.
- For reliability on what actually matters downstream — whether the forecast improves charging decisions — per-night confidence intervals *do* exist in the `charging_value` metrics. That's the more trustworthy measure of practical significance.

**One caveat to keep in mind (issue X-2):** the `yesterday` baseline is actually slightly *weaker* than a true same-period-yesterday would be (it can't use data from the target day at 11:00 cutoff), so the comparison is directionally fair — it doesn't flatter the baseline at the model's expense — but the gap might narrow slightly against a tighter baseline.

**Bottom line:** the ~56% error reduction is large and robust in absolute terms, but for a statistically rigorous reliability estimate, the `charging_value` question class (scored per night, with confidence intervals) is the right place to look.

_2 turns, 11.6 s._

---

### How accurate were the carbon forecasts for 12 October 2026?

**Tool call:** `get_forecast`
```json
{
  "date": "2026-10-12",
  "target": "carbon",
  "detail": "summary"
}
```

**Tool result (error):** `get_forecast` (383 characters)
```
OUT_OF_RANGE: The day 2026-10-12 is outside the stored forecast window (2025-01-01 to 2026-09-29). There is no live forecast, so dates after the window cannot be answered.
  field: date
  got: 2026-10-12
  valid range: 2025-01-01 to 2026-09-29
  did you mean: 2026-09-29
  hint: Call describe_dataset (section='overview') for the window. Retry with a date inside it.
  retryable: yes
```

**Agent:** Unfortunately, 12 October 2026 is outside the available forecast window. The stored backtest data only covers **2025-01-01 to 2026-09-29**, and there is no live forecast system — only pre-computed out-of-sample backtest results.

To look at carbon forecast accuracy, you'd need to pick a date on or before **29 September 2026**.

_2 turns, 5.0 s._

---

### On the night of 25 October 2025, when the clocks went back, when did each charging strategy charge, and how much carbon did the forecast-optimised plan save compared with the overnight timer?

**Tool call:** `compare_strategies`
```json
{
  "night": "2025-10-25",
  "objective": "carbon"
}
```

**Tool result:** `compare_strategies` (2,852 characters)
```
{"night":"2025-10-25","scenario":{"name":"default","plug_in":"18:00","deadline":"07:00","energy_kwh":8,"power_kw":7,"nights":"every"},"window_start_local":"2025-10-25T18:00:00","window_end_local":"2025-10-26T07:00:00","window_slots":28,"objective":"carbon","objective_unit":"gCO2","carbon_price_gbp_per_tonne":null,"forecast":{"selected":"model","carbon_arm":"lgbm_price","price_arm":null},"outcomes":[{"strategy":"arrival","slots":[{"start_utc":"2025-10-25T17:00:00","start_local":"2025-10-25T18:00:00","kwh":3.5},{"start_utc":"2025-10-25T17:30:00","start_local":"2025-10-25T18:30:00","kwh":3.5},{"start_utc":"2025-10-25T18:00:00","start_local":"2025-10-25T19:00:00","kwh":1}],"energy_kwh":8,"carbon_g":558.5,"cost_gbp":0.69626,"objective_value":558.5,"vs_timer_objective_pct":-17.33,"vs_timer_carbon_pct":-17.33,"vs_timer_cost_pct":-274.67},{"strategy":"timer","slots":[{"start_utc":"2025-10-25T23:00:00","start_local":"2025-10-26T00:00:00","kwh":3.5},{"start_utc":"2025-10-25T23:30:00","start_local":"2025-10-26T00:30:00","kwh":3.5},{"start_utc":"2025-10-26T00:00:00","start_local":"2025-10-26T01:00:00","kwh":1}],"energy_kwh":8,"carbon_g":476,"cost_gbp":0.185835,"objective_value":476,"vs_timer_objective_pct":0,"vs_timer_carbon_pct":0,"vs_timer_cost_pct":0},{"strategy":"forecast_optimised","slots":[{"start_utc":"2025-10-26T00:00:00","start_local":"2025-10-26T01:00:00","kwh":1},{"start_utc":"2025-10-26T03:00:00","start_local":"2025-10-26T03:00:00","kwh":3.5},{"start_utc":"2025-10-26T03:30:00","start_local":"2025-10-26T03:30:00","kwh":3.5}],"energy_kwh":8,"carbon_g":462,"cost_gbp":0.030155,"objective_value":462,"vs_timer_objective_pct":2.94,"vs_timer_carbon_pct":2.94,"vs_timer_cost_pct":83.77},{"strategy":"perfect_foresight","slots":[{"start_utc":"2025-10-25T21:00:00","start_local":"202
... [1,052 more characters in the raw transcript]
```

**Agent:** Here is what happened on the clock-change night. The window ran from **18:00 BST on 25 Oct** to **07:00 GMT on 26 Oct** — an extra hour compared to a normal night, giving 28 half-hour slots instead of the usual 26.

---

### When each strategy charged

| Strategy | Slots (local time) | kWh | Carbon (gCO₂) |
|---|---|---|---|
| **Arrive** | 18:00–18:30, 18:30–19:00, 19:00–19:30 BST | 8 | 558.5 |
| **Timer** (00:00) | 00:00–00:30, 00:30–01:00, 01:00–01:30 BST | 8 | 476 |
| **Forecast-optimised** | 01:00–01:30 BST *(partial)*, 03:00–03:30 GMT, 03:30–04:00 GMT | 8 | 462 |
| **Perfect foresight** | 22:00–22:30, 23:00–23:30 BST, 02:30–03:00 GMT | 8 | 426 |

The clock change at 02:00 BST → 01:00 GMT is visible: the forecast-optimised plan split its load around the changeover, picking up a partial slot just before clocks went back and then the two lowest-carbon slots at 03:00–04:00 GMT.

---

### Forecast-optimised vs overnight timer

| | Timer | Forecast-optimised | Saving |
|---|---|---|---|
| Carbon | 476 gCO₂ | 462 gCO₂ | **14 gCO₂ (2.9%)** |

The maximum possible saving over the timer (to perfect foresight) was 50 gCO₂, so the forecast captured **28%** of that gap on this particular night. Single nights are noisy — the long-run figure across all nights is available via `charging_value` metrics if you want the aggregate picture.

_2 turns, 27.1 s._

---

### Did adding the NESO wind forecast improve the model? Be careful about which evaluation window you trust.

**Agent:** Let me check the dataset overview and experiments metrics together.

**Tool call:** `describe_dataset`
```json
{
  "section": "overview"
}
```

**Tool result:** `describe_dataset` (1,768 characters)
```
{"summary":"Day-ahead GB carbon-intensity and wholesale-price forecasts, and EV-charging results, from a rolling-origin backtest over 2025-01-01 to 2026-09-29. Results are out-of-sample.","forecast_window":{"first":"2025-01-01","last":"2026-09-29"},"data_window":{"first":"2023-01-01","last":"2026-09-30"},"sections_returned":["overview"],"overview":{"arm_names_by_kind":{"baseline":["last_week","neso_logged","price_da","price_da_basis7d","price_last_week","price_yesterday","yesterday"],"carbon_model":["lgbm","lgbm_noweather","lgbm_price"],"chance":["random_carbon","random_price"],"experiment_variant":["lgbm_both","lgbm_night","lgbm_wind"],"pipeline":["da_basis","day_ahead","model","naive"],"price_model":["price_lgbm"],"reference":["neso_latest_revision"],"strategy":["arrival","perfect","timer"]},"scenario_names":["default","plug_in_17:00","plug_in_19:00","plug_in_20:00","plug_in_21:00","energy_4kWh","energy_12kWh","energy_16kWh","energy_20kWh","mon_wed_fri_only"],"question_classes":["carbon_forecast_accuracy","charging_value","cost_carbon_tradeoff","experiments","price_forecast_accuracy","slot_ranking"],"limitation_count":6,"next_step":"Pass these names to the other tools. Call describe_dataset again with section='arms', 'question_classes' or 'limitations' for details."},"arms":null,"scenarios":null,"question_classes":null,"limitations":null,"data_issue_count":35,"provenance":{"data_through":"2026-09-29","results_exported_at":"2026-10-19T09:26:59.079126","exported_from_commit":"23496c501c92682ee812be321e5767ca2bb87da0","schema_version":1,"out_of_sample":true,"note":"All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00 UK time on the day before the day they forecast. Nothing here is a live forecast."}}
```

**Tool call:** `query_metrics`
```json
{
  "question_class": "experiments"
}
```

**Tool result:** `query_metrics` (10,523 characters)
```
{"question_class":"experiments","evaluation_period":"2025-01-01 to 2026-09-29: 637 days (about 21 months)","filters_applied":{"group_by":"overall","eval_window":"2025-01-01_2026-09-29"},"rows":[{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":-2.942142646930156,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":1242.242924528302,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":0.3820939684862753,"ci_low":0.3197954570486918,"ci_high":0.4349982893653649,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"gap_captured_difference","value":-0.0169976853877174,"ci_low":-0.0792505257082165,"ci_high":0.0436673882736821,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"grams_saved_per_night","value":-1.6800314465408803,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":19.13508989600199,"ci_low":null,"ci_high":null,"n":16534,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":913.6792452830188,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"}
... [8,723 more characters in the raw transcript]
```

**Agent:** Now let me check the data issues flagged in the results.

**Tool call:** `list_data_issues`
```json
{
  "id": "WIND-1",
  "detail": "full"
}
```

**Tool call:** `list_data_issues`
```json
{
  "id": "WIND-6",
  "detail": "full"
}
```

**Tool result:** `list_data_issues` (1,871 characters)
```
{"count":1,"total_matching":1,"issues":[{"id":"WIND-1","source":"wind","title":"most recent months have no usable day-ahead vintage","flagged":false,"status":"partly fixable going forward: see WIND-4.","handling":"every vintage is stored with its true issue time; the feature builder keeps only vintages issued by the cutoff, so unusable days get NULL wind features (LightGBM handles this). Evaluating a wind feature over May-Sep 2026 therefore says nothing about wind.","body":"- The \"Historic Day Ahead Wind Forecasts\" file stores one vintage per target day with its issue time.\n  For 2026-05-01 onward every vintage was issued on the target day itself (median 22.8 h after the\n  11:00 D-1 cutoff), so zero of 153 days (May-Sep 2026) pass the leakage gate. Earlier: 2023 126 days\n  and 2025 50 days were back-filled long after delivery (issued after the target day started).\n  Overall 1,012 of 1,370 days (74%) are usable. By quarter: 2024 ~99%, 2025 Q1-Q3 96-100% but Q4 50%,\n  2026 Q1 80%, Q2 25%, Q3 0%; 2023 Q3 0%. Over the 2025-01 to 2026-09 test period about 65% of rows have wind.\n- Handling: every vintage is stored with its true issue time; the feature builder keeps only vintages\n  issued by the cutoff, so unusable days get NULL wind features (LightGBM handles this). Evaluating a\n  wind feature over May-Sep 2026 therefore says nothing about wind.\n- Status: partly fixable going forward: see WIND-4."}],"detail_returned":"full","by_source":{"wind":1},"notes":[],"provenance":{"data_through":"2026-09-29","results_exported_at":"2026-10-19T09:26:59.079126","exported_from_commit":"23496c501c92682ee812be321e5767ca2bb87da0","schema_version":1,"out_of_sample":true,"note":"All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00 UK time on th
... [71 more characters in the raw transcript]
```

**Tool result:** `list_data_issues` (1,435 characters)
```
{"count":1,"total_matching":1,"issues":[{"id":"WIND-6","source":"wind","title":"a model trained with wind degrades badly when wind is missing at prediction time","flagged":false,"status":null,"handling":null,"body":"- 2025-01 to 2026-09 test period, wind available on 64% of rows. Where wind WAS available the wind model beat\n  the baseline by 3% (MAE 17.45 vs 18.01; shape error -5%). Where it was missing it was 16% WORSE (23.65 vs\n  20.31), because the missing months (May-Sep 2026, and holes in 2023-25) were seen in training with real values.\n  Net effect on the whole test period: slightly worse (19.66 vs 18.83). On the 2024 development window, where\n  coverage was 99%, the gain looked like 10% (16.6 vs 18.4); the gain does not generalise at that size.\n- Implication: a wind feature is only safe if it is reliably present at prediction time (live capture, WIND-4)\n  or the pipeline falls back to a no-wind model on days it is missing. Not promoted into the standard models."}],"detail_returned":"full","by_source":{"wind":1},"notes":[],"provenance":{"data_through":"2026-09-29","results_exported_at":"2026-10-19T09:26:59.079126","exported_from_commit":"23496c501c92682ee812be321e5767ca2bb87da0","schema_version":1,"out_of_sample":true,"note":"All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00 UK time on the day before the day they forecast. Nothing here is a live forecast."}}
```

**Agent:** **Short answer: No — it looked promising on the development window but does not hold up on the test period, and the "be careful" warning is precisely this.**

---

### What the dev window said (misleading)

The 2024 development window had ~99% wind coverage, where the wind model showed a **10% MAE improvement** (16.6 vs 18.4 gCO2/kWh). That's what made it worth testing. But this is the window used to *decide what to test*, so it's optimistic by construction.

### What the test window shows (the truth)

Over the real test period (2025-01 to 2026-09), wind data was only available for **64% of half-hours** due to WIND-1 (NESO's historic file switched to same-day vintage in May 2026, making the last 5 months entirely unusable at the 11:00 D-1 cutoff).

The split performance (WIND-6) explains everything:

| Condition | `lgbm_wind` MAE | `lgbm_price` MAE | Difference |
|---|---|---|---|
| Wind present (64% of rows) | 17.45 | 18.01 | **−3% (better)** |
| Wind missing (36% of rows) | 23.65 | 20.31 | **+16% (worse)** |
| **Overall** | **19.66** | **18.83** | **+4% (net worse)** |

The model was trained seeing wind as a real feature on most days. When it's missing at prediction time, it degrades badly rather than gracefully falling back.

### Charging value

The `lgbm_wind` gap_captured (0.408) is marginally higher than `lgbm_price` (0.399), but the difference CI is **−0.035 to +0.054** — entirely spanning zero. The +0.8 g CO2 per night saving is noise.

### Bottom line

The wind feature is **conditionally useful** (it works when present) but **not safe to ship** without either reliable live capture (WIND-4) or a clean no-wind fallback model. It was not promoted into the standard pipeline for this reason.

_5 turns, 38.6 s._
<!-- TRANSCRIPTS:END -->

### What the transcripts changed

Running a real agent against the server found three things my tests had not, and each led to a change in the tool
or the project, never to an edit of a transcript. The earlier runs of question 1 are kept unedited in
[`transcripts/archive/`](transcripts/archive/).

1. **First run:** the agent said the data covered "roughly 3+ years" (it is 21 months) and called a difference
   "statistically significant" because of the sample size, although those accuracy rows have no confidence intervals.
   Both traced to information the tool had not given. `query_metrics` now returns the evaluation period and says when
   a class has no intervals and where to find them (`charging_value`, scored per night).
2. **Second run:** the agent read the "same period yesterday" caveat backwards, saying the baseline was flattered
   when it is the opposite (it is weaker than an impossible true yesterday, so comparisons favour the model). The
   cause was a caveat that was only a title, and an issue log that never stated the consequence. The log now has an
   explicit effect for it, the export carries effects, and every caveat in a result shows its effect.
3. **Third run (the one above):** the direction is now right, and the agent correctly points to `charging_value` for
   intervals. Two small imprecisions remain that the tool output neither supports nor contradicts: it calls the
   effect of the weaker baseline "slight" (its size is unknown) and describes the cutoff loosely. I did not
   chase them further, and the run is unedited.
4. **Across runs:** the agent twice guessed `eval_window="test"` and was corrected by the structured error, which
   is the recovery path working as designed.

Question 1 above is from the latest server. Questions 2 to 4 were run on the server before the last change (which adds
a sentence of effect to each caveat) and were not rerun. The agent's words are never edited.

### Run log: every run, including the ones not shown

Showing one run per question is a selection, so this is what I found in all of them. Nothing is edited. The runs shown
above are the first complete set I reviewed; a second complete set, run on the latest server, is in
[`transcripts/archive/rerun-2/`](transcripts/archive/rerun-2/).

| Question | Run | Shown? | What I found |
|---|---|---|---|
| 1 model vs baseline | 1 (before any fix) | archived | Numbers right. Invented "roughly 3+ years" (it is 21 months); called the gap "significant" from sample size alone. |
| 1 | 2 | archived | Read caveat X-2 backwards ("flatters the baseline"); the log had no stated effect. |
| 1 | 3 | **shown** | Direction of X-2 right, intervals caveat respected, points to `charging_value`. Calls the effect "slight"; its size is unknown. |
| 1 | 4 (rerun-2) | archived | Numbers right, and it went further: pulled the per-night intervals from `charging_value`, all correct. But read X-2 backwards again, saying a weaker baseline "makes the comparison more favourable to the baseline", the opposite of the effect line it was shown. |
| 2 date outside window | 2 runs | one shown, one archived | Both handled the structured error correctly and did not substitute another date. |
| 3 clock-change night | shown | **shown** | Every slot, time and figure right, including the BST-to-GMT changeover. |
| 3 | rerun-2 | archived | Figures right, but says the forecast plan's first slot is "the repeated 01:00 hour after clocks went back". It is the first 01:00 (BST); the repeated hour comes an hour later in UTC. |
| 4 wind experiment | 2 runs | one shown, one archived | Both reach the right conclusion and recover from a wrong `eval_window` via the structured error. The rerun calls the development window "contaminated", which overstates it. |

What this says about the tool: it fixed the gaps that were mine (missing period, missing interval warning, missing
effect) and the agent's answers improved. What remains is the agent misreading a subtle directional caveat in about
half the runs, even when the effect is spelled out. A tool can state facts clearly and give structured recovery paths;
it cannot make an agent read them. The place to fix the rest would be the agent's prompt or a structured field such
as `direction: favours_model`, not more prose in the tool.

## Limits

- **stdio only.** No HTTP transport and no authentication; it is meant to run as a local subprocess of the client.
- **It serves stored results, not live data.** Nothing here forecasts tomorrow. The results are as fresh as the last
  `carbon-charge report`, and the provenance on every answer says when that was.
- **Model names are data, not part of the tool schema.** They are validated at call time against `describe_dataset`, so
  the schema cannot go stale, at the cost of a less specific schema.
