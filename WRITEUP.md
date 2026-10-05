# Can you charge an EV on cleaner electricity by forecasting tomorrow's grid?

*Mostly no, and the reasons are more useful than the answer.*

Written 1 October 2026 from data downloaded on 30 September. The test period runs from 1 January 2025 to
29 September 2026, the last complete day: 637 days, 636 charging nights and 30,574 half-hours (see "How the
counts fit together"). Code, data log and every table: this repository (`reports/RESULTS.md` has the full set).

## The short version

- I built a day-ahead forecast of Great Britain's half-hourly carbon intensity and used it to schedule a
  car's overnight charge (plug in 18:00, 8 kWh by 07:00, 7 kW charger).
- The forecast is **good**: it misses by about 19 gCO2/kWh on average, against 43 for "same as yesterday".
- The *value* is **small**. A perfect fortune-teller would cut a night's emissions by only about 10% compared
  with a plain overnight timer. My forecast captures roughly a third to two-fifths of that: a carbon-focused
  plan saves about 35 to 40 g per night, a cost-focused plan about 5p of wholesale electricity, and a blend of
  the two gets both (about 46 g and 5p). That is roughly 13 to 17 kg of CO2 and £20 a year.
- Almost all the benefit comes from not charging at 6pm, which any timer already does. Charging on arrival
  emits about 30% more than the timer.
- Wholesale prices were a more interesting surprise: **my price model only tied the published day-ahead
  price**, and planning on price alone cut carbon almost as well as planning on the carbon forecast.
- Two obvious improvements (a NESO wind forecast, and training for night ordering) looked promising on
  development data and **did not hold up on the test period**.

## What was built

A pipeline in Python that ingests half-hourly carbon intensity (NESO), weather forecasts as issued
(Open-Meteo, ECMWF), NESO's day-ahead demand and wind forecasts, and wholesale prices (N2EX day-ahead and
the Elexon Market Index). Features feed a LightGBM model. A rolling-origin backtest scores it, and a charging
simulator turns forecasts into plans and prices them at what actually happened.

## The rule that matters most: no peeking

Every forecast for day D is made at **11:00 UK time on D-1** and may only use data published by then.
Getting this wrong is the usual way forecasting results become too good to be true, so it is enforced in one
place: every feature value carries the time it became available, and anything not yet published at the
cutoff is blanked out. Some consequences:

- "Same period yesterday" is only known for the first part of the day at 11:00, so for about 58% of periods
  the honest baseline falls back to the day before.
- Weather is an archive of forecasts as issued, never observed weather.
- Tests corrupt every value published after the cutoff and assert the features do not change. I checked these
  tests fail when I deliberately broke the gate.

Evaluation is a rolling backtest: refit every 14 days on the past only, forecast the next 14, never a random
split. The test period is 636 charging nights over 21 months, covering every season.

### How the counts fit together

Different analyses use slightly different numbers of nights, because each keeps only the nights where every
forecast it compares is complete. Nothing is hidden: the nights dropped are listed in `DATA_ISSUES.md` (X-5).

| Analysis | Nights | Why |
|---|---|---|
| Test period | 636 | 637 days; a night starts in the evening and needs the next morning, so the last day gives no night |
| Headline carbon comparison | 631 | 5 nights lost because a "yesterday" or "last week" baseline has no value for a period that does not exist on a clock-change day |
| Cost and cost/carbon comparison | 627 | 9 nights lost: 3 of the clock-change cases (the other 2 involve only the "yesterday" baseline, which this comparison does not use), 3 with no realised price (no trading volume) and 3 where last week's price is missing |
| Model experiments | 636 | compares only LightGBM variants, none of which has gaps |

The forecast accuracy charts count half-hours instead: 30,574 in the period, 30,564 scored for carbon (10 lost to
the same clock-change gaps) and 30,510 for price (64 lost to missing realised prices or baseline values).

## Results

### The forecast beats simple baselines clearly

![Carbon forecast error](reports/figures/carbon_accuracy.png)

Weather does most of the work: without it the error is 33. Adding price features helps a little (18.1).

### But the prize is small, and a timer already captures most of it

![Share of the possible saving captured](reports/figures/gap_captured.png)

The headline metric is the share of the gap between an overnight timer and perfect foresight. LightGBM
captures **34%** (95% interval 28 to 39%), or **40%** with price features, over the 631 comparable nights. "Same as yesterday" is worse than
just using the timer (-21%), and last week's pattern is indistinguishable from it.

![One typical night](reports/figures/sample_night.png)

On a typical night the forecast finds the cleaner early-morning hours but misjudges the shape, so it saves
some, though not all, of what perfect knowledge would.

### Prices: the market already did the forecasting

The day-ahead auction result is public by 10:00, before my cutoff, so tomorrow's price is *known*. I
forecast the realised Market Index price as the day-ahead price plus a learned gap.

![Price forecast error](reports/figures/price_accuracy.png)

The learned model ties the published price (MAE 12.22 against 12.27 £/MWh). That is a useful negative result:
the auction already contains almost everything predictable.

### Cheap and clean mostly go together

![Cost and carbon per night](reports/figures/cost_carbon_frontier.png)

Over the 627 nights with complete prices I planned on a blend of cost and carbon, with carbon valued at £0 to £2,500 per tonne. Planning on cost alone
emits about as much carbon as planning on the carbon forecast itself (913 g against 914 g per night), and a
modest £250/t carbon price adds almost nothing to the bill while shaving a little more carbon.

## What did not work, and why that is the interesting part

I tried two sensible improvements: NESO's own day-ahead wind forecast as a feature, and training the model on
each slot's deviation from the night's average (since the charger only needs the *order* of slots).

I set the rules first: develop on July to December 2024, then run the 2025 to 2026 test period once for
variants declared in advance, reporting all of them.

| | Development window (183 nights) | Test period (636 nights) |
|---|---|---|
| Night-ordering target, change in gap captured | **+12.8 points** (+1.0 to +24.9) | -1.1 points (-7.4 to +5.4) |
| Wind forecast, change in gap captured | +7.4 points (-1.9 to +19.0) | +0.9 points (-3.5 to +5.4) |

The development gain was noise. Had I tuned on the test period I would have reported a win that does not
exist. The wind result has a second lesson: where a wind forecast was available it helped (3% lower error),
but where it was missing the model was 16% *worse*. NESO's history file has lost its true day-ahead values
for the most recent months, so a model that learned to rely on wind fails quietly when it disappears.

## Data problems that mattered

Everything is logged in `DATA_ISSUES.md`. The ones that would have changed the conclusions:

- NESO's historical carbon "forecast" field is **not day-ahead**. Its error is about 10, a quarter of
  yesterday's, which no day-ahead forecast could achieve. Using it as a baseline would have made any model
  look worse than it is.
- The weather forecast archive only starts in early 2024, limiting training data for the best features.
- NESO's day-ahead price file is labelled in UTC (it has 24 rows on 23- and 25-hour days). Treating it as UK
  time silently misaligns every price by an hour in summer.
- Forecast publication times, such as the 1-hour lag on carbon actuals, are assumptions I could not verify.

## Honest limits

- **NESO's own forecast is not scored.** It is the right competitor, and I only began recording it on
  30 September 2026. A comparison needs a month or two of snapshots. Until then the claim is "beats simple
  baselines", not "beats the operator".
- **Costs are wholesale only**: no network charges, levies or supplier margin.
- **Average carbon is not marginal carbon.** Shifting one car changes which plant runs, and the average grid
  mix is only a proxy for that.
- **One car is a small load.** Roughly £20 and 13 to 17 kg of CO2 a year does not justify a forecasting system
  on its own.

## What would make it matter

Bigger flexible loads (home batteries, heat pumps, fleets), longer planning windows where tomorrow's price is
not yet known, and a live service that produces the plan and talks to the charger. The method carries over;
the honest summary is that for a single car, a timer gets you most of the way.

## Reproduce it

```bash
uv sync
uv run carbon-charge ingest all
uv run carbon-charge backtest        # about 20 to 25 minutes
uv run carbon-charge report          # tables, charts, reports/RESULTS.md
uv run pytest
```
