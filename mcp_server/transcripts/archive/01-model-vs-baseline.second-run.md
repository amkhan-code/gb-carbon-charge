### Did the LightGBM carbon model beat the 'same period yesterday' baseline, and by how much? Is the difference reliable?

**Tool call:** `query_metrics`
```json
{
  "question_class": "carbon_forecast_accuracy"
}
```

**Tool result:** `query_metrics` (3,218 characters)
```
{"question_class":"carbon_forecast_accuracy","evaluation_period":"2025-01-01 to 2026-09-29: 637 days (about 21 months)","filters_applied":{"group_by":"overall"},"rows":[{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":53.87,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"last_week","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":68.36,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":19.17,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":24.7,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":32.94,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_noweather","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":41.21,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"mae","value":18.05,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":"carbon","arm":"lgbm_price","vs_arm":null,"group_key":"overall","group_value":"all","metric":"rmse","value":23.37,"ci_low":null,"ci_high":null,"n":30564,"n_unit":"half_hours","unit":"gCO2/kWh"},{"target":
... [1,418 more characters in the raw transcript]
```

**Agent:** Yes, LightGBM beat the "same period yesterday" (`yesterday`) baseline by a substantial margin. Here are the numbers:

| Model | MAE (gCO₂/kWh) | RMSE (gCO₂/kWh) |
|---|---|---|
| `lgbm` | 19.17 | 24.70 |
| `yesterday` (baseline) | 43.45 | 55.95 |

**LightGBM is ~56% better on MAE** (43.45 → 19.17, a reduction of 24.3 gCO₂/kWh) and **~56% better on RMSE** (55.95 → 24.70). The gap is large in absolute terms — roughly a 2× improvement.

---

**Is the difference reliable?**

Cautiously yes, but with caveats:

1. **No confidence intervals are reported** for these per-half-hour scores. The note explicitly warns that *half-hours within a night are not independent*, so the nominal n=30,564 overstates the effective sample size, meaning a naive significance test would be overconfident.

2. **The gap is so large** (24+ gCO₂/kWh on MAE) that it almost certainly isn't a statistical fluke over 637 days — it would require a systematic and implausible bias in the data to disappear.

3. **Known caveats apply** (flagged in the results):
   - `X-2`: the `yesterday` baseline isn't a strict day-ahead forecast (it can peek slightly into the day), which actually *flatters* the baseline — so the true gap may be even larger.
   - `X-5`: the baseline has missing values on clock-change days.

4. For a reliability estimate tied to actual charging outcomes, `query_metrics` with `question_class='charging_value'` provides per-night scoring with confidence intervals — that's the more rigorous test.

**Bottom line:** LightGBM's ~56% MAE improvement over `yesterday` is a robust, practically large result. The lack of CIs on raw error scores is a presentation limitation, not a sign the difference is marginal.

_2 turns, 12.0 s._
