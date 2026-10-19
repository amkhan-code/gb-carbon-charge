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
