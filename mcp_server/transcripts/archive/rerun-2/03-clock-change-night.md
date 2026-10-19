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

**Agent:** Here's a breakdown of what happened on that clock-change night (window: 18:00 BST → 07:00 GMT, **28 slots** instead of the usual 26 because of the extra hour):

---

### When each strategy charged (UK local time)

| Strategy | Slots charged | Carbon (gCO₂) |
|---|---|---|
| **Arrival** | 18:00, 18:30, 19:00 BST | 558.5 |
| **Timer (00:00)** | 00:00, 00:30, 01:00 BST | 476.0 |
| **Forecast-optimised** | 01:00 BST, 03:00 GMT, 03:30 GMT | 462.0 |
| **Perfect foresight** | 22:00, 23:00 BST, 02:30 GMT | 426.0 |

---

### Forecast-optimised vs. timer

The forecast-optimised plan saved **14 gCO₂ (2.9%)** over the timer — a modest gain on this particular night. It skipped the midnight block entirely and shifted charging to just before the clock change (01:00 BST, UTC 00:00) then resumed at 03:00–03:30 GMT.

The perfect-foresight upper bound saved 50 gCO₂ (10.5%), and the forecast only captured **28% of that possible gap** — which is below average for a single night (single nights are noisy).

### The clock-change effect

- The **timer** fired at 00:00–01:00 BST, all in the final hour before the clocks turned back.
- The **forecast** correctly looked past the repeated 01:00 hour toward lower-intensity slots in the early GMT morning (03:00–03:30), rather than exploiting the doubled-up period.
- **Perfect foresight** identified 22:00–23:00 BST as unusually clean, which no forecast could reliably predict the day before.

> Note: for a robust picture of forecast value across all nights, `query_metrics` with `question_class='charging_value'` is more reliable than any single night.

_2 turns, 27.4 s._
