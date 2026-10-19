### Did adding the NESO wind forecast improve the model? Be careful about which evaluation window you trust.

**Agent:** Let me look at the experiments results and dataset overview together.

**Tool call:** `query_metrics`
```json
{
  "question_class": "experiments"
}
```

**Tool result:** `query_metrics` (10,804 characters)
```
{"question_class":"experiments","evaluation_period":"2025-01-01 to 2026-09-29: 637 days (about 21 months)","filters_applied":{"group_by":"overall","eval_window":"2025-01-01_2026-09-29"},"rows":[{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":-2.942142646930156,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":1242.242924528302,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":0.3820939684862753,"ci_low":0.3197954570486918,"ci_high":0.4349982893653649,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"gap_captured_difference","value":-0.0169976853877174,"ci_low":-0.0792505257082165,"ci_high":0.0436673882736821,"n":636,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"grams_saved_per_night","value":-1.6800314465408803,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":19.13508989600199,"ci_low":null,"ci_high":null,"n":16534,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":913.6792452830188,"ci_low":null,"ci_high":null,"n":636,"n_unit":"nights","unit":"gCO2"}
... [9,004 more characters in the raw transcript]
```

**Tool call:** `describe_dataset`
```json
{
  "section": "arms"
}
```

**Tool result:** `describe_dataset` (8,069 characters)
```
{"summary":"Day-ahead GB carbon-intensity and wholesale-price forecasts, and EV-charging results, from a rolling-origin backtest over 2025-01-01 to 2026-09-29. Results are out-of-sample.","forecast_window":{"first":"2025-01-01","last":"2026-09-29"},"data_window":{"first":"2023-01-01","last":"2026-09-30"},"sections_returned":["arms"],"overview":null,"arms":[{"name":"last_week","kind":"baseline","target":"carbon","description":"Same settlement period 7 days earlier.","uses_sources":["ci_actual"],"note":null,"forecast_coverage":{"n_half_hours":30568,"first":"2025-01-01T00:00:00","last":"2026-09-29T22:30:00"}},{"name":"neso_logged","kind":"baseline","target":"carbon","description":"NESO's own forward carbon forecast, as recorded by the daily logger, latest snapshot before the cutoff.","uses_sources":[],"note":"Only exists for days the logger has run (from 2026-09-30); see DATA_ISSUES X-3.","forecast_coverage":null},{"name":"price_da","kind":"baseline","target":"price","description":"The known day-ahead auction price used directly as the forecast of the realised price.","uses_sources":["price_day_ahead"],"note":null,"forecast_coverage":{"n_half_hours":30574,"first":"2025-01-01T00:00:00","last":"2026-09-29T22:30:00"}},{"name":"price_da_basis7d","kind":"baseline","target":"price","description":"Day-ahead price plus the trailing 7-day mean gap between realised and day-ahead prices.","uses_sources":["price_day_ahead","price_mid"],"note":null,"forecast_coverage":{"n_half_hours":30574,"first":"2025-01-01T00:00:00","last":"2026-09-29T22:30:00"}},{"name":"price_last_week","kind":"baseline","target":"price","description":"Realised price for the same period 7 days earlier.","uses_sources":["price_mid"],"note":null,"forecast_coverage":{"n_half_hours":30545,"first":"2025-01-01T00:00:00"
... [6,269 more characters in the raw transcript]
```

**Agent:** Now let me check the wind data issues in detail, and also try querying the development window for comparison.

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

**Tool call:** `query_metrics`
```json
{
  "question_class": "experiments",
  "eval_window": "development"
}
```

**Tool result:** `list_data_issues` (1,885 characters)
```
{"count":1,"total_matching":1,"issues":[{"id":"WIND-1","source":"wind","title":"most recent months have no usable day-ahead vintage","flagged":false,"status":"partly fixable going forward: see WIND-4.","handling":"every vintage is stored with its true issue time; the feature builder keeps only vintages issued by the cutoff, so unusable days get NULL wind features (LightGBM handles this). Evaluating a wind feature over May-Sep 2026 therefore says nothing about wind.","effect":null,"body":"- The \"Historic Day Ahead Wind Forecasts\" file stores one vintage per target day with its issue time.\n  For 2026-05-01 onward every vintage was issued on the target day itself (median 22.8 h after the\n  11:00 D-1 cutoff), so zero of 153 days (May-Sep 2026) pass the leakage gate. Earlier: 2023 126 days\n  and 2025 50 days were back-filled long after delivery (issued after the target day started).\n  Overall 1,012 of 1,370 days (74%) are usable. By quarter: 2024 ~99%, 2025 Q1-Q3 96-100% but Q4 50%,\n  2026 Q1 80%, Q2 25%, Q3 0%; 2023 Q3 0%. Over the 2025-01 to 2026-09 test period about 65% of rows have wind.\n- Handling: every vintage is stored with its true issue time; the feature builder keeps only vintages\n  issued by the cutoff, so unusable days get NULL wind features (LightGBM handles this). Evaluating a\n  wind feature over May-Sep 2026 therefore says nothing about wind.\n- Status: partly fixable going forward: see WIND-4."}],"detail_returned":"full","by_source":{"wind":1},"notes":[],"provenance":{"data_through":"2026-09-29","results_exported_at":"2026-10-19T09:54:37.274109","exported_from_commit":"0ed3382f1fde8822548a9747b14e2429c6c88d6f","schema_version":1,"out_of_sample":true,"note":"All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00
... [85 more characters in the raw transcript]
```

**Tool result:** `list_data_issues` (1,449 characters)
```
{"count":1,"total_matching":1,"issues":[{"id":"WIND-6","source":"wind","title":"a model trained with wind degrades badly when wind is missing at prediction time","flagged":false,"status":null,"handling":null,"effect":null,"body":"- 2025-01 to 2026-09 test period, wind available on 64% of rows. Where wind WAS available the wind model beat\n  the baseline by 3% (MAE 17.45 vs 18.01; shape error -5%). Where it was missing it was 16% WORSE (23.65 vs\n  20.31), because the missing months (May-Sep 2026, and holes in 2023-25) were seen in training with real values.\n  Net effect on the whole test period: slightly worse (19.66 vs 18.83). On the 2024 development window, where\n  coverage was 99%, the gain looked like 10% (16.6 vs 18.4); the gain does not generalise at that size.\n- Implication: a wind feature is only safe if it is reliably present at prediction time (live capture, WIND-4)\n  or the pipeline falls back to a no-wind model on days it is missing. Not promoted into the standard models."}],"detail_returned":"full","by_source":{"wind":1},"notes":[],"provenance":{"data_through":"2026-09-29","results_exported_at":"2026-10-19T09:54:37.274109","exported_from_commit":"0ed3382f1fde8822548a9747b14e2429c6c88d6f","schema_version":1,"out_of_sample":true,"note":"All forecasts are rolling-origin backtest forecasts, as they would have been issued at 11:00 UK time on the day before the day they forecast. Nothing here is a live forecast."}}
```

**Tool result (error):** `query_metrics` (248 characters)
```
UNKNOWN_NAME: Unknown evaluation window: 'development'.
  field: eval_window
  got: development
  allowed values: 2024-07-01_2024-12-31, 2025-01-01_2026-09-29
  hint: Valid names are listed in describe_dataset (section='overview').
  retryable: yes
```

**Tool call:** `query_metrics`
```json
{
  "question_class": "experiments",
  "eval_window": "2024-07-01_2024-12-31"
}
```

**Tool result:** `query_metrics` (10,785 characters)
```
{"question_class":"experiments","evaluation_period":"2024-07-01 to 2024-12-31: 184 days (about 6 months)","filters_applied":{"group_by":"overall","eval_window":"2024-07-01_2024-12-31"},"rows":[{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":-5.116457116457116,"ci_low":null,"ci_high":null,"n":183,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"arrival","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":1253.9535519125684,"ci_low":null,"ci_high":null,"n":183,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"gap_captured","value":0.3684453684453684,"ci_low":0.2458074371522071,"ci_high":0.4746537759735855,"n":183,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"gap_captured_difference","value":0.1101871101871102,"ci_low":-0.0089633853681786,"ci_high":0.2333045911318297,"n":183,"n_unit":"nights","unit":"fraction"},{"target":"carbon","arm":"lgbm_both","vs_arm":"lgbm_price","group_key":"overall","group_value":"all","metric":"grams_saved_per_night","value":9.12295081967213,"ci_low":null,"ci_high":null,"n":183,"n_unit":"nights","unit":"gCO2"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":17.980102967395673,"ci_low":null,"ci_high":null,"n":4760,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_both","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mean_g_per_night","value":799.8306010928961,"ci_low":null,"ci_high":null,"n":183,"n_unit":"nights","unit":"gCO2"},{"t
... [8,985 more characters in the raw transcript]
```

**Agent:** Here's the complete picture — the evaluation window matters enormously.

---

## Does the NESO wind forecast improve the model?

**Short answer: No, not in a deployable sense** — despite looking promising in development.

---

### The trap: the development window is contaminated

| Window | Wind coverage | `lgbm_wind` MAE | `lgbm_price` MAE | Δ MAE | Gap-captured diff (CI) |
|---|---|---|---|---|---|
| **Dev** (2024-H2) | **99.5%** | 16.63 | 18.43 | **−10%** | +7.4 pp (−1.9 to +19) |
| **Test** (2025-01–2026-09) | **64%** | 19.66 | 18.83 | **+4% worse** | +0.85 pp (−3.5 to +5.4) |

The development window (Jul–Dec 2024) had near-perfect wind coverage, which is why the gain looked like **10% MAE improvement**. But that window was also used to *decide to test* the wind feature — so you can't trust it as a neutral evaluation. The test window is what to draw conclusions from.

---

### Why the test window flips negative: WIND-6

The model trained with wind **degrades badly when wind data is missing at prediction time**, because it saw real values during training for those periods:

- When wind **is** available → beats `lgbm_price` by **3% MAE** (17.45 vs 18.01)
- When wind **is missing** → is **16% worse** (23.65 vs 20.31)

And in the test period, wind is missing for **36% of half-hours** — mostly because May–Sep 2026 have zero usable day-ahead vintages (WIND-1: the NESO file issued all 2026 Q2/Q3 vintages on the target day itself, after the 11:00 cutoff).

---

### Bottom line

The wind feature is not in the standard models for exactly this reason. It would only be safe to use if:
1. Wind data is reliably captured live before 11:00 (WIND-4 is a partial fix), **or**
2. The pipeline falls back to a no-wind model on days the feature is missing.

Without one of those safeguards, adding wind makes the model net worse over any period where coverage degrades.

_7 turns, 47.3 s._
