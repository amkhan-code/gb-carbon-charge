"""Plain-language descriptions of the result classes (the data tells us which exist and how they split)."""

QUESTION_CLASS_TEXT = {
    "carbon_forecast_accuracy": "MAE and RMSE of day-ahead carbon-intensity forecasts (gCO2/kWh). Every arm is scored on the same half-hours.",
    "price_forecast_accuracy": "MAE and RMSE of forecasts of the realised half-hourly Market Index price (GBP/MWh).",
    "slot_ranking": "Overlap between the slots a forecast ranks best and the slots that were actually best, in the 18:00-07:00 window. 'random' is chance.",
    "charging_value": "Share of the timer-to-perfect-foresight gap captured by each charging strategy, with intervals, for the carbon or the cost objective, per scenario.",
    "cost_carbon_tradeoff": "Cost and carbon per night when plans weigh cost against carbon at different carbon prices (GBP per tonne), default scenario.",
    "experiments": "Pre-declared model experiments, on a development window and the test period, with paired comparisons against the base model.",
}
UNDESCRIBED = "(no description recorded)"

# Known data issues that qualify each result class (only those present in the issue log are returned).
CLASS_CAVEATS = {
    "carbon_forecast_accuracy": ["X-2", "X-5", "CI-1"],
    "price_forecast_accuracy": ["PX-3", "PX-4", "PX-7"],
    "slot_ranking": ["X-5"],
    "charging_value": ["X-5", "PX-4", "PX-7"],
    "cost_carbon_tradeoff": ["PX-4", "PX-7"],
    "experiments": ["WIND-1", "WIND-6"],
}
SEASON_ORDER = {"DJF": 0, "MAM": 1, "JJA": 2, "SON": 3}
DEFAULT_GROUP = {"slot_ranking": "slots_chosen", "cost_carbon_tradeoff": "carbon_price_gbp_per_tonne"}


def group_sort_key(group_key: str, value: str):
    """Natural order for a breakdown: seasons by year order, numbers numerically, 'carbon only' last."""
    if group_key == "season":
        return (SEASON_ORDER.get(value, 9), value)
    try:
        return (0, float(value), "")
    except ValueError:
        return (1, 0.0, value)
