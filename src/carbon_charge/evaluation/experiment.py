"""Pre-declared model experiments, scored on what the charger needs: the order of slots in a night.

Run on a development window, decide, and only then touch the real test period. Results are
written to reports/experiments/ and never overwrite the standard backtest tables.
"""

from datetime import date

import duckdb
import numpy as np
import pandas as pd

from carbon_charge import config
from carbon_charge.charging import value
from carbon_charge.charging.scenarios import DEFAULT
from carbon_charge.evaluation import backtest, ranking
from carbon_charge.features.build import build


def _nights(index: pd.DatetimeIndex) -> list:
    days = pd.Series(index).dt.date
    d0, d1 = days.min(), days.max()
    return [d0 + pd.Timedelta(days=i) for i in range((d1 - d0).days)]


def within_night_mae(forecast: pd.Series, actual: pd.Series, night: pd.Series) -> float:
    """MAE of the *shape*: forecast and actual both measured from their own night mean."""
    j = pd.concat([forecast.rename("f"), actual.rename("a"), night.rename("n")], axis=1).dropna()
    j["df"] = j["f"] - j.groupby("n")["f"].transform("mean")
    j["da"] = j["a"] - j.groupby("n")["a"].transform("mean")
    return float((j["df"] - j["da"]).abs().mean())


def run(con: duckdb.DuckDBPyConnection, start: date, end: date, refit_days: int = 14,
        models: list[backtest.ModelSpec] | None = None) -> dict[str, pd.DataFrame]:
    models = models or backtest.EXPERIMENTS
    fs = build(con, config.HISTORY_START, end)
    ci, _, folds = backtest.run(con, start, end, refit_days, fs=fs, models=models)
    names = [m.name for m in models if m.target != "price"]
    wide = {m: g.set_index("ts_utc")["forecast_gco2_kwh"] for m, g in ci.groupby("model") if m in names}
    actual = fs.y
    night, _ = backtest.night_labels(fs)

    # Common sample: window rows where every candidate has a forecast and the actual exists.
    idx = actual.dropna().index
    for s in wide.values():
        idx = idx.intersection(s.dropna().index)
    idx = idx.intersection(night.dropna().index)
    acc = pd.DataFrame([{
        "model": m,
        "n": len(idx),
        "mae": float((wide[m].reindex(idx) - actual.reindex(idx)).abs().mean()),
        "rmse": float(np.sqrt(((wide[m].reindex(idx) - actual.reindex(idx)) ** 2).mean())),
        "within_night_mae": within_night_mae(wide[m].reindex(idx), actual.reindex(idx), night.reindex(idx)),
        "wind_coverage": float(fs.X["wind_fc_mw"].reindex(idx).notna().mean()),
    } for m in names])

    # Diagnostic only (no model change): the same errors split by whether a wind forecast was usable.
    has_wind = fs.X["wind_fc_mw"].reindex(idx).notna()
    by_wind = pd.DataFrame([{
        "wind_available": bool(flag), "model": m, "n": int(mask.sum()),
        "mae": float((wide[m].reindex(idx)[mask] - actual.reindex(idx)[mask]).abs().mean()),
        "within_night_mae": within_night_mae(wide[m].reindex(idx)[mask], actual.reindex(idx)[mask], night.reindex(idx)[mask]),
    } for flag, mask in ((True, has_wind), (False, ~has_wind)) if mask.sum() for m in names])

    nights = _nights(idx)
    rk = ranking.ranking_accuracy(actual, wide, nights, DEFAULT)
    sim = value.simulate(actual, wide, DEFAULT, nights)
    gap = value.summarize(sim, names)
    base = "lgbm_price"
    paired = pd.DataFrame([value.paired_gap_difference(sim, m, base) for m in names if m != base])
    # Paired accuracy difference by night: negative = candidate has lower shape error than the base.
    j = pd.concat({m: wide[m].reindex(idx) for m in names} | {"a": actual.reindex(idx), "n": night.reindex(idx)}, axis=1)
    shape = {m: ((j[m] - j.groupby("n")[m].transform("mean")) - (j["a"] - j.groupby("a" if False else "n")["a"].transform("mean"))).abs()
             for m in names}
    by_night = pd.DataFrame({m: s.groupby(j["n"]).mean() for m, s in shape.items()})
    acc_pairs = pd.DataFrame([{"model": m, "vs": base, "shape_mae_diff": float((by_night[m] - by_night[base]).mean()),
                               "share_of_nights_better": float((by_night[m] < by_night[base]).mean())}
                              for m in names if m != base])
    return {"accuracy": acc, "accuracy_by_wind_availability": by_wind, "ranking": rk, "gap_captured": gap, "paired_gap": paired,
            "paired_shape_error": acc_pairs, "nights": sim, "folds": folds}
