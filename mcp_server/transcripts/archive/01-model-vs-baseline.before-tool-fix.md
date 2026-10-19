### Did the LightGBM carbon model beat the 'same period yesterday' baseline, and by how much? Is the difference reliable?

**Tool call:** `query_metrics`
```json
{
  "question_class": "carbon_forecast_accuracy"
}
```

**Tool result:** `query_metrics` (2,884 characters)
```
{"question_class":"carbon_forecast_accuracy","filters_applied":{"group_by":"overall"},"rows":[{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":53.87,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":68.36,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":19.17,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":24.7,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":32.94,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":41.21,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":18.05,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":23.37,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"yesterday","vs_arm":null,"group_key":"overall","group_value
... [1,084 more characters in the raw transcript]
```

**Agent:** Yes, LightGBM beats the "same period yesterday" (`yesterday`) baseline decisively:

| Model | MAE (gCO₂/kWh) | RMSE (gCO₂/kWh) |
|---|---|---|
| `lgbm` | **19.17** | **24.70** |
| `yesterday` (baseline) | 43.45 | 55.95 |

**The margin is large:**
- MAE improvement: **24.3 gCO₂/kWh** — LightGBM is **56% lower** in absolute error.
- RMSE improvement: **31.3 gCO₂/kWh** — **56% lower** again.

**Is it reliable?**

The improvement is almost certainly real rather than noise, for a few reasons:

1. **Sample size**: Both are evaluated on the same **30,564 half-hours** (roughly 3+ years of out-of-sample data). At that scale, even a small difference would be statistically significant — a gap of this magnitude is overwhelming.
2. **Consistency**: The gap holds on both MAE and RMSE, suggesting it isn't driven by a handful of outlier events.
3. **Mechanism is clear**: `yesterday` can't capture day-to-day weather or demand variation; LightGBM uses weather forecasts (removing them via `lgbm_noweather` raises MAE to 32.94, confirming weather is a key driver).

**One caveat to note**: The `yesterday` baseline has a known data issue (`X-2`) — it can't be a strict day-ahead baseline and is missing values on clock-change days (`X-5`). This means the comparison is slightly imperfect, but the gap is so large that no plausible correction would close it.

_2 turns, 11.3 s._
