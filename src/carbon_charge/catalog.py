"""What each 'arm' (model, baseline, strategy, pipeline) is, in words an agent can use.

One source of truth for the names that appear in results tables. Feature use for the LightGBM
variants is derived from the real model specs (see `uses_sources`), so the descriptions cannot
silently drift from what is trained; a test checks this.
"""

from dataclasses import dataclass

from carbon_charge.evaluation import backtest
from carbon_charge.features.build import ALLOWED_SOURCES


@dataclass(frozen=True)
class Arm:
    name: str
    kind: str  # carbon_model | price_model | baseline | reference | strategy | pipeline | experiment_variant | chance
    target: str  # carbon | price | charging
    description: str
    uses_sources: tuple[str, ...] = ()
    note: str = ""


def _uses(spec: backtest.ModelSpec) -> tuple[str, ...]:
    return tuple(sorted(ALLOWED_SOURCES - set(spec.exclude_sources)))


_SPECS = {s.name: s for s in backtest.MODELS + backtest.EXPERIMENTS}
_LGBM = "LightGBM, refit every 14 days on the past only"


def arms() -> list[Arm]:
    s = _SPECS
    return [
        Arm("lgbm", "carbon_model", "carbon", f"{_LGBM}. Day-ahead carbon intensity from calendar, lagged actuals, NESO demand forecast and ECMWF weather forecasts. No price or wind features.", _uses(s["lgbm"])),
        Arm("lgbm_noweather", "carbon_model", "carbon", f"{_LGBM}, with weather features removed (an ablation).", _uses(s["lgbm_noweather"])),
        Arm("lgbm_price", "carbon_model", "carbon", f"{_LGBM}. The `lgbm` features plus day-ahead and realised wholesale price features.", _uses(s["lgbm_price"])),
        Arm("yesterday", "baseline", "carbon", "Same settlement period yesterday where published by the 11:00 cutoff, else the day before.", ("ci_actual",), "About 58% of periods use the fallback; see DATA_ISSUES X-2."),
        Arm("last_week", "baseline", "carbon", "Same settlement period 7 days earlier.", ("ci_actual",)),
        Arm("neso_logged", "baseline", "carbon", "NESO's own forward carbon forecast, as recorded by the daily logger, latest snapshot before the cutoff.", (), "Only exists for days the logger has run (from 2026-09-30); see DATA_ISSUES X-3."),
        Arm("neso_latest_revision", "reference", "carbon", "The Carbon Intensity API's historical `forecast` field. NOT day-ahead: a near-real-time estimate. Context only, never a valid baseline.", (), "DATA_ISSUES CI-1."),
        Arm("price_lgbm", "price_model", "price", f"{_LGBM}. Predicts realised Market Index price minus the day-ahead price (the basis), then adds the day-ahead price back.", _uses(s["price_lgbm"])),
        Arm("price_da", "baseline", "price", "The known day-ahead auction price used directly as the forecast of the realised price.", ("price_day_ahead",)),
        Arm("price_da_basis7d", "baseline", "price", "Day-ahead price plus the trailing 7-day mean gap between realised and day-ahead prices.", ("price_day_ahead", "price_mid")),
        Arm("price_yesterday", "baseline", "price", "Realised price for the same period yesterday where published by the cutoff, else the day before.", ("price_mid",)),
        Arm("price_last_week", "baseline", "price", "Realised price for the same period 7 days earlier.", ("price_mid",)),
        Arm("random_carbon", "chance", "carbon", "Chance level for slot ranking: the expected overlap if the slots were chosen at random.", (), "Not a forecast; only appears in slot_ranking."),
        Arm("random_price", "chance", "price", "Chance level for price slot ranking: the expected overlap if the cheapest slots were chosen at random.", (), "Not a forecast; only appears in slot_ranking."),
        Arm("arrival", "strategy", "charging", "Charge at full power from plug-in time until the energy is delivered."),
        Arm("timer", "strategy", "charging", "Charge at full power from 00:00 UK time until the energy is delivered."),
        Arm("perfect", "strategy", "charging", "Charge in the slots that turn out to be best, using the actual values. An upper bound no real system can reach."),
        Arm("model", "pipeline", "charging", "Forecast-optimised charging using LightGBM carbon (with price features) and LightGBM price forecasts."),
        Arm("day_ahead", "pipeline", "charging", "Forecast-optimised charging using LightGBM carbon and the known day-ahead price."),
        Arm("da_basis", "pipeline", "charging", "Forecast-optimised charging using LightGBM carbon and day-ahead price plus 7-day basis."),
        Arm("naive", "pipeline", "charging", "Forecast-optimised charging using last week's carbon and last week's realised price."),
        Arm("lgbm_wind", "experiment_variant", "carbon", "Experiment: `lgbm_price` plus NESO's day-ahead wind forecast.", _uses(s["lgbm_wind"]), "Wind history has holes; see DATA_ISSUES WIND-1, WIND-6."),
        Arm("lgbm_night", "experiment_variant", "carbon", "Experiment: trained on each slot's deviation from its 18:00-07:00 night mean, plus the `lgbm_price` night level.", _uses(s["lgbm_night"])),
        Arm("lgbm_both", "experiment_variant", "carbon", "Experiment: the wind forecast and the night-ordering target together.", _uses(s["lgbm_both"])),
    ]
