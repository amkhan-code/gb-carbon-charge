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
