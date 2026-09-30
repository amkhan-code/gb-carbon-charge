# Data issues log

Every data problem found, how it is handled, and its status. Newest last within each source.

## Carbon Intensity API (carbonintensity.org.uk)

### CI-1: the historical `forecast` field is NOT day-ahead  — FLAGGED
- Its MAE against actuals is ~9-10 gCO2/kWh in every year 2023-2026, versus ~39-43 for
  same-period-yesterday. A genuine day-ahead forecast could not be four times more accurate
  than that; this is a near-real-time estimate (last revision before delivery), and its issue
  time is not recorded.
- Handling: stored as `ci_history.neso_forecast_latest_gco2_kwh`. It must NOT be used as the
  "NESO forecast" baseline or as a feature. The daily logger (`carbon-charge log-forecast`,
  table `ci_forecast_log`) records the forward 48h forecast with its issue time; the NESO
  baseline can only be evaluated on the period the logger has run. Schedule it before 11:00 UK.
- Status: open until the logger has accumulated history. First snapshot: 2026-09-30.

### CI-2: missing `forecast` key on some records
- 2025-01-12 22:00Z-2025-01-13 (26 rows) and 2025-08-10/11 (~17 rows) have no `forecast` key
  at all (not null). Parser treats as NULL. 43 rows in total. `actual` is present.

### CI-3: missing actuals
- 5 half-hours on 2023-01-26 have null `actual`. Left NULL; no imputation at ingestion.

### CI-4: gaps in the series
- 2023-10-20 21:30Z -> 2023-10-22 19:30Z (46 h missing) and 2024-06-11 22:30Z -> 2024-06-12 14:30Z
  (16 h missing). Rows are absent, not null. Settlement days 2023-10-20/22 and 2024-06-12 are
  therefore incomplete. Backtest must skip target days with missing actuals.

### CI-5: API range and window semantics
- Requests over 31 days are rejected (400); chunked at 30 days. The API filters on period END time,
  so the range start is offset by 30 min and results are filtered to periods starting in the range.

## NESO day-ahead demand forecast

Source: "Day Ahead Half Hourly Demand Forecast Performance". Chosen because it has an explicit
`Publish_Datetime` per row. The `Demand_Outturn` column in the same file is deliberately not read.

### DEM-1: only one vintage per target day
- Exactly one forecast (published the day before) exists per target day; no history of revisions.
  A day whose vintage was published after the cutoff has no leak-free demand forecast (see DEM-3).

### DEM-2: publish timestamps are labelled UTC ("Z") but the time-of-day looks odd
- Typical publish time is 08:45Z Nov-Mar and 09:45Z Apr-Oct (08:33/09:33 in 2026). A fixed local
  publishing time would shift the other way with clock changes, so the label may be wrong.
- Handling: taken as UTC, as labelled. This is the conservative reading for leakage (later
  than the alternative). Under it all vintages are before the cutoff except DEM-3.
- Status: unresolved; ask NESO or confirm against the live feed.

### DEM-3: 3 target days published after the 11:00 UK cutoff
- 2026-09-02, 2026-09-09, 2026-09-14: published 10:03Z on D-1 = 11:03 BST, 3 minutes late.
- Handling: rows kept with true issue time; the feature builder must drop them for those days
  (demand features NULL), not silently use them.

### DEM-4: duplicated (date, period) keys in 2021-2022
- 2021-10-31 and 2022-10-30 (autumn clock change) have repeated periods with different values.
  Outside the project's history window (from 2023-01-01), so filtered out. 2023+ has no duplicates.

### DEM-5: the file's own `Datetime` column is ambiguous on clock-change days
- On 2024-10-27 periods 4-5 repeat the local time of periods 2-3; on 2024-03-31 it skips 01:00.
  UTC key is derived from settlement date + period instead. 46/50-period days verified.

### DEM-6: last day is partial
- 2026-09-30 has 19 of 48 periods (file is a rolling performance file). Not a problem for training.

## Open-Meteo weather (Previous Runs API)

### WX-1: the Historical Forecast API is unsuitable for "as issued" data
- It splices the first hours of successive runs (near-zero lead time) and returns no issue time.
  Using it would leak information a day-ahead forecaster would not have had.
- Handling: use the Previous Runs API (`<var>_previous_day1/2`), which gives values from a run
  initialised >= N days before the target hour. Stored with `lead_days`.

### WX-2: the exact model run and its availability time are not exposed
- `issued_at_utc` = target time - N days is an UPPER BOUND on run initialisation, not the run time,
  and a run only becomes available hours after initialisation (ECMWF ~6-8 h).
- Handling for the leakage layer: use lead_days=2 only for a 11:00 D-1 cutoff unless
  `ts - 1 day + publication lag <= cutoff` is shown. As stored, `previous_day1` for target hour H on
  day D can come from a run initialised as late as (H - 24h), which for H after ~11:00-lag on D is
  AFTER the cutoff. So `lead_days=1` is only usable for hours whose `issued_at_utc + lag <= cutoff`.
  The leakage test must enforce this per feature.

### WX-3: no archive before early 2024
- No `previous_day` data for ECMWF/GFS/ICON before Jan-Feb 2024 (the UKMO model is even shorter).
  Weather features exist only from 2024-02-04.
- Wind speed at 100m and shortwave radiation are also NULL until 2024-03-06 17:00Z (13.7k rows).
  Effective full-variable weather history starts 2024-03-07.
- Consequence: weather-based models train on ~2.5 years, while CI/demand-only baselines get 3.75.

### WX-4: `cloud_cover` NULL for April 2026
- 1,161 rows all in 2026-04. Left NULL (LightGBM handles missing values).

### WX-5: model choice
- Only ECMWF IFS 0.25 is stored. Other models (GFS, ICON) could be added as further features.

## Cross-source

### X-1: three sources, three publication/availability regimes
- Carbon intensity actuals: available only after the period ends; the latest usable actual at the
  11:00 D-1 cutoff is roughly the period ending ~11:00 D-1 (allow for publication lag; to verify).
- Demand forecast: one vintage per day, published ~08:45-09:45Z on D-1.
- Weather: see WX-2.
