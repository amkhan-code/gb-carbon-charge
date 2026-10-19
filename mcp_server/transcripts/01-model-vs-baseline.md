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
