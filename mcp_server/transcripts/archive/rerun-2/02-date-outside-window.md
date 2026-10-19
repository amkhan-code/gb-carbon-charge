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

**Agent:** Unfortunately, 12 October 2026 is outside the stored forecast window. The data only covers **2025-01-01 to 2026-09-29**, so any date after 29 September 2026 cannot be answered — there is no live forecast.

If you'd like, I can look up accuracy for a date within that range instead.

_2 turns, 5.5 s._
