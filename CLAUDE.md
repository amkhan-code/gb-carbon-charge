# GB carbon intensity forecasting for EV charging

Day-ahead forecasting of half-hourly GB carbon intensity, used to optimise an
EV charging schedule and measure the value over simple baselines.

The project is built in layers. **Layer 1 (done): carbon intensity, end to end.**
**Layer 2 (done): wholesale prices** (see the Layer 2 section below).

## Working style

- Work in small steps and pause for inspection between them. Ingestion comes
  first; stop after it so the data can be inspected before features/modelling.
- Propose structure before writing code when starting a new area.
- Log every data problem found in `DATA_ISSUES.md` (what, where, date range
  affected, how it is handled). Never silently patch data.

## Stack

- Python 3.11+, packaged with `pyproject.toml`, `src/` layout.
- CLI via typer. Code lives in modules, not notebooks.
- Storage: DuckDB.
- Model: LightGBM.
- Tests: pytest.
- Docker for running everything; GitHub Actions runs tests on push.

## Time and storage conventions

- Every row is keyed on a UTC timestamp (start of the half-hour).
- Settlement date and settlement period are stored alongside. Periods run
  1-48 on normal days, 1-46 on the spring clock-change day and 1-50 on the
  autumn clock-change day. Settlement date is the UK local date.
- Every forecast row also stores its issue time (UTC). Forecast tables are
  keyed on (target timestamp, issue time) so multiple vintages can coexist.

## Data sources

- **NESO Carbon Intensity API** (carbonintensity.org.uk): actual and forecast
  national carbon intensity.
- **Open-Meteo Historical Forecast API**: archived weather forecasts as
  issued. Never use observed/reanalysis weather for training.
- **NESO day-ahead demand forecast**.

## Leakage rules

- Daily cutoff: 11:00 UK local time on D-1, for forecasting all periods of
  day D.
- Features may only use data PUBLISHED or ISSUED before the cutoff. Respect
  publication lags: a value for time T is not available at T, only once it
  has been published.
- No actual demand and no observed weather as features.
- Fit any scaling/encoding on training data only.

## Evaluation

- Rolling-origin backtest. Never a random split.
- Baselines:
  - same period yesterday,
  - same period last week,
  - NESO's own forecast. Check whether the API's historical `forecast` field
    is genuinely day-ahead. If it is not, flag it in `DATA_ISSUES.md` and add
    a daily logger that records forward forecasts as issued.
  - Persistence baselines obey the same cutoff as the model (use the latest
    values published before 11:00 on D-1).
- Metrics:
  - MAE and RMSE, broken down by season and time of day.
  - Slot-ranking accuracy: overlap between forecast-greenest and
    actual-greenest slots within the charging window.

## Charging optimiser

- Default case: car plugs in at 18:00, needs ~8 kWh by 07:00, 7 kW charger.
- Sensitivity set (small): plug-in 17:00-21:00, 4-20 kWh, not every night.
- Strategies:
  1. charge on arrival,
  2. overnight timer from 00:00,
  3. forecast-optimised,
  4. perfect foresight.
- Headline metric: share of the timer-to-perfect-foresight gap captured by
  the forecast-optimised strategy.

## Required tests

- Clock-change days: 46 and 50 settlement periods handled correctly.
- Leakage: assert every feature's publication/issue time is before the
  cutoff.
- Optimiser correctness: energy delivered, power limit respected, ready on
  time.

## Layer 2: wholesale prices

Decisions so far (from the user): forecast prices as well as ingest them; use both price series;
the charging optimiser reports cost-only, carbon-only and a weighted trade-off.

- Sources: N2EX day-ahead auction prices (NESO data portal, hourly, GBP/MWh) and Elexon Market
  Index Data (`APXMIDP`, half-hourly). Same storage rules: UTC key plus settlement date/period.
- The N2EX source labels hours in UTC (DATA_ISSUES PX-1). Never assume UK local time for it.
- Leakage: the day-ahead price for day D is published by 10:00 GMT on D-1, before the 11:00 UK
  cutoff, so it is KNOWN when planning and is a legitimate feature. MID prices are published after
  delivery: only usable once the period has ended plus the lag in `config.py`.
- Because day-ahead prices are known at the cutoff, the price forecast target is the REALISED
  Market Index price, predicted as day-ahead price + a learned basis (user's choice). Cost is
  wholesale only, priced at the realised Market Index price.
- Layer 1 models (`lgbm`, `lgbm_noweather`) must keep excluding price features so their results do
  not change; price features go only into `lgbm_price` and `price_lgbm`.
- The blended objective is `price/1000 + lam * carbon/1e6` (GBP/kWh), lam in GBP per tonne;
  lam=0 is cost only, lam=None is carbon only (identical to Layer 1).
- Same rules as Layer 1: rolling-origin backtests, tests for clock-change days and leakage,
  every data problem logged in `DATA_ISSUES.md`. Pause for inspection after ingestion.
