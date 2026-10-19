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
