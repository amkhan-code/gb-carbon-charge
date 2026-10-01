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

## Found while building Layer 1

### X-2: "same period yesterday" cannot be a strict day-ahead baseline
- At the 11:00 D-1 cutoff only D-1's early periods are published (with the 1 h actuals lag,
  periods starting up to 09:30). About 58% of periods have no D-1 value available.
- Handling: the baseline uses D-1 where published and falls back to the same period on D-2
  otherwise (`models/baselines.py`). This is a deliberate reading of the spec, not a data
  fault; a naive "D-1 regardless" baseline would leak and is not computed.

### X-3: NESO forecast baseline has no history yet
- Because of CI-1, the only valid NESO baseline is the logger's, which began 2026-09-30. The
  first snapshots taken locally that day were issued after that day's cutoff, so they qualify
  for no target day. `carbon-charge report` shows the NESO row once >= 14 nights are covered.

### X-4: publication lags are assumptions
- CI actuals: 1 h after period end. ECMWF runs: 8 h after (upper-bound) issue time. Neither
  is published by the sources. Both are in `config.py`; results should be re-run if they change.

### ENV-1: LightGBM needs the OpenMP runtime on macOS
- `libomp` is normally installed with Homebrew, which is absent on the development machine. Local
  runs borrowed the copy bundled in scikit-learn's wheel via DYLD_LIBRARY_PATH. Linux (Docker, CI)
  installs `libgomp1` and is unaffected.

## Wholesale prices (Layer 2)

### PX-1: the N2EX day-ahead dataset is labelled in UTC, not UK local time
- NESO's "N2EX GB Day-Ahead Price" has `Date` + `Delivery Period` ("HH:00 - HH+1:00") and exactly
  24 rows on every day, including the 23- and 25-hour clock-change days. A UK-local dataset could
  not do that. Cross-correlating with Elexon's half-hourly UTC-stamped index over winter, summer and
  post-change weeks peaks at zero hour shift in every case (r ~0.82-0.87), so the labels are UTC hours.
- Handling: `ts_utc` = Date + label hour. Settlement date/period are derived from that UTC instant, so
  a UTC date does NOT equal a settlement date (in summer the 23:00 UTC hour belongs to the next day).
- Status: inferred, not documented by NESO. Re-check if NESO changes the file.

### PX-2: the NESO day-ahead dataset lags the auction
- The auction publishes by 10:00 GMT on D-1 (Nord Pool), yet at 12:35 UTC on 2026-09-30 the dataset
  stopped at 2026-09-30 21:00 UTC: tomorrow's prices were not there and the last hours of today's were
  missing. The resource `last_modified` field is also stale (2026-09-21).
- Handling: for backtests the price is treated as available at 10:00 UTC on D-1 (when the exchange
  published it). For LIVE use, the NESO feed cannot be relied on before the 11:00 UK cutoff.
- Status: open; a live pipeline would need the exchange feed or to log the dataset's update times.

### PX-3: day-ahead publication time is an assumption
- Nord Pool documents gate closure 09:50 and results by 10:00 GMT (the search result was ambiguous
  about GMT vs UK local). Modelled as 10:00 UTC on D-1, which equals the cutoff in summer (11:00 BST)
  and is an hour early in winter. Delays happen (a 4 Jan 2025 notice shows results at 10:17).

### PX-4: MID zero-volume rows carry a placeholder price
- 38 half-hours (2023-01, 2023-08, 2025-06, 2026-05/07/09 ...) have volume 0 and price 0.00. Stored
  with a NULL price. The `N2EXMIDP` provider is zero in every row and is ignored; `APXMIDP` is used.

### PX-5: MID gaps
- 6 half-hours missing entirely (2023-05-26 11:30, 2023-06-07 17:30, 2023-07-31 02:30, 2023-11-11 07:30,
  2023-12-12 19:00, 2024-04-13 07:00 UTC). Left absent.

### PX-6: negative and spiky prices are real
- Day-ahead: 555 negative hours (107/176/177/95 in 2023-26), min -54.2, max 980. MID: 1,753 negative
  half-hours, min -102.9, max 1,352.9. These are market outcomes, not errors; not clipped.

### PX-7: MID is not a day-ahead price
- MID is a within-day traded index published after delivery. It can only be a lagged feature or an
  outturn for scoring, never a value known at the cutoff for the target day. Day-ahead vs MID: MAE
  10-15 GBP/MWh, correlation 0.73-0.89 by year.

## NESO day-ahead wind forecast

### WIND-1: most recent months have no usable day-ahead vintage
- The "Historic Day Ahead Wind Forecasts" file stores one vintage per target day with its issue time.
  For 2026-05-01 onward every vintage was issued on the target day itself (median 22.8 h after the
  11:00 D-1 cutoff), so zero of 153 days (May-Sep 2026) pass the leakage gate. Earlier: 2023 126 days
  and 2025 50 days were back-filled long after delivery (issued after the target day started).
  Overall 1,012 of 1,370 days (74%) are usable. By quarter: 2024 ~99%, 2025 Q1-Q3 96-100% but Q4 50%,
  2026 Q1 80%, Q2 25%, Q3 0%; 2023 Q3 0%. Over the 2025-01 to 2026-09 test period about 65% of rows have wind.
- Handling: every vintage is stored with its true issue time; the feature builder keeps only vintages
  issued by the cutoff, so unusable days get NULL wind features (LightGBM handles this). Evaluating a
  wind feature over May-Sep 2026 therefore says nothing about wind.
- Status: partly fixable going forward: see WIND-4.

### WIND-2: several vintages for the same target period on 7 days
- 2023-10-29, 2024-10-27, 2025-10-26 (50-period days) and 2026-07-29/30, 2026-08-01/02 carry more than one
  vintage (including a re-issue on 2026-08-18). Kept via a (target, issue time) key; the builder uses the
  latest vintage issued by the cutoff.

### WIND-3: Forecast_Timestamp has no timezone label
- Read as UTC, the conservative choice for a cutoff rule. Typical usable vintage is issued about 08:49,
  a median 105 minutes before the cutoff (minimum 15 minutes).

### WIND-4: the live feed is not being captured
- NESO's "Day Ahead Wind Forecast" (tomorrow's 48 periods) refreshes around 08:40 UTC daily and has no issue
  time column. The carbon-intensity logger runs at 07:30 UTC, before that refresh. A snapshot after ~09:00 UTC
  using the resource's `last_modified` as the issue time would fill the gap from today onward.

### WIND-5: installed capacity grows over time
- Capacity is 15.9 GW (2022) to 24.3 GW (2026), so wind MW is not comparable across years. The builder adds
  forecast as a share of capacity alongside MW. Datetime_GMT matches the UTC period start for every row.

### WIND-6: a model trained with wind degrades badly when wind is missing at prediction time
- 2025-01 to 2026-09 test period, wind available on 64% of rows. Where wind WAS available the wind model beat
  the baseline by 3% (MAE 17.45 vs 18.01; shape error -5%). Where it was missing it was 16% WORSE (23.65 vs
  20.31), because the missing months (May-Sep 2026, and holes in 2023-25) were seen in training with real values.
  Net effect on the whole test period: slightly worse (19.66 vs 18.83). On the 2024 development window, where
  coverage was 99%, the gain looked like 10% (16.6 vs 18.4); the gain does not generalise at that size.
- Implication: a wind feature is only safe if it is reliably present at prediction time (live capture, WIND-4)
  or the pipeline falls back to a no-wind model on days it is missing. Not promoted into the standard models.
