"""Turn stored backtest forecasts into metrics, optimiser results and reports/RESULTS.md."""

from datetime import timedelta

import duckdb
import pandas as pd

from carbon_charge import config
from carbon_charge.charging import value
from carbon_charge.charging.scenarios import DEFAULT, sensitivity_set
from carbon_charge.evaluation import metrics, ranking

CORE = metrics.CORE_MODELS
MIN_NESO_NIGHTS = 14


def _load(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.Series]:
    fc = con.execute("SELECT ts_utc, model, forecast_gco2_kwh FROM backtest_forecast").df()
    if fc.empty:
        raise SystemExit("backtest_forecast is empty: run `carbon-charge backtest` first")
    act = con.execute("SELECT ts_utc, actual_gco2_kwh a FROM ci_history").df().set_index("ts_utc")["a"]
    return fc, act


def _nights(fc: pd.DataFrame) -> list:
    days = pd.to_datetime(fc["ts_utc"]).dt.date
    d0, d1 = days.min(), days.max()
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days)]


def evaluate(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    fc, act = _load(con)
    df = metrics.eval_frame(fc, act)
    out = metrics.tables(df, CORE)
    ref = metrics.common_rows(df, CORE + ["neso_latest_revision"])
    out["reference_neso_latest_revision"] = metrics.summarize(ref, [])
    neso = metrics.common_rows(df, CORE + ["neso_logged"])
    if not neso.empty:
        out["neso_logged_comparison"] = metrics.summarize(neso, [])
    wide = {m: g.set_index("ts_utc")["forecast_gco2_kwh"] for m, g in fc.groupby("model")}
    out["ranking"] = ranking.ranking_accuracy(act, {m: wide[m] for m in CORE}, _nights(fc))
    if "neso_logged" in wide:
        r = ranking.ranking_accuracy(act, {m: wide[m] for m in CORE + ["neso_logged"]}, _nights(fc))
        if not r.empty:
            out["ranking_neso_nights"] = r
    return out


def optimise(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    fc, act = _load(con)
    wide = {m: g.set_index("ts_utc")["forecast_gco2_kwh"] for m, g in fc.groupby("model")}
    nights = _nights(fc)
    sources = {m: wide[m] for m in CORE}
    summaries, per_night = [], {}
    for sc in sensitivity_set():
        sim = value.simulate(act, sources, sc, nights)
        if sim.empty:
            continue
        summaries.append(value.summarize(sim, CORE).assign(scenario=sc.name))
        if sc is DEFAULT or sc.name == DEFAULT.name:
            per_night["optimiser_nights_default"] = sim
    out = {"optimiser_summary": pd.concat(summaries, ignore_index=True), **per_night}
    if "neso_logged" in wide:
        sim = value.simulate(act, {**sources, "neso_logged": wide["neso_logged"]}, DEFAULT, nights)
        out["optimiser_neso_nights_available"] = pd.DataFrame({"nights": [len(sim)]})
        if len(sim) >= MIN_NESO_NIGHTS:
            out["optimiser_neso"] = value.summarize(sim, CORE + ["neso_logged"]).assign(scenario=DEFAULT.name)
    return out


def write_csvs(tables: dict[str, pd.DataFrame]) -> None:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        name = name if name.startswith(("optimiser", "ranking")) else f"metrics_{name}"
        df.to_csv(config.REPORTS_DIR / f"{name}.csv", index=False)


# --- markdown ---------------------------------------------------------------

def _md(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    rows = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    rows += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def _pair(df: pd.DataFrame, index: str) -> pd.DataFrame:
    """Pivot to one row per `index`, one column per model, cells 'MAE / RMSE'."""
    d = df.assign(cell=df["mae"].map("{:.1f}".format) + " / " + df["rmse"].map("{:.1f}".format))
    order = [m for m in CORE if m in set(d["model"])]
    return d.pivot(index=index, columns="model", values="cell")[order].reset_index()


def render() -> str:
    read = lambda n: pd.read_csv(config.REPORTS_DIR / f"{n}.csv")  # noqa: E731
    exists = lambda n: (config.REPORTS_DIR / f"{n}.csv").exists()  # noqa: E731
    folds = pd.read_csv(config.REPORTS_DIR / "backtest_folds.csv")
    overall, season, tod = read("metrics_overall"), read("metrics_by_season"), read("metrics_by_time_of_day")
    ranking_t, opt = read("ranking"), read("optimiser_summary")
    names = {"lgbm": "LightGBM", "lgbm_noweather": "LightGBM without weather", "yesterday": "Same period yesterday*",
             "last_week": "Same period last week", "neso_logged": "NESO forecast (logged)",
             "arrival": "Charge on arrival", "timer": "Overnight timer (00:00)", "perfect": "Perfect foresight"}
    nm = lambda s: s.map(lambda x: names.get(x, x))  # noqa: E731

    o = overall.assign(model=nm(overall["model"]))[["model", "n", "mae", "rmse"]]
    default = opt[opt["scenario"] == "default"]
    forecast_label = lambda x: f"Forecast-optimised ({names[x]})" if x in CORE else names.get(x, x)  # noqa: E731
    order = ["arrival", "timer", *CORE, "perfect"]
    head = default.set_index("strategy").loc[order].reset_index()
    head["strategy"] = head["strategy"].map(forecast_label)
    head["gap"] = head.apply(lambda r: f"{100 * r['gap_captured']:.0f}%" + (
        "" if pd.isna(r["gap_ci_low"]) else f" ({100 * r['gap_ci_low']:.0f} to {100 * r['gap_ci_high']:.0f}%)"), axis=1)
    head = head[["strategy", "nights", "mean_g_per_night", "saving_vs_timer_pct", "gap"]].round(1)
    head.columns = ["Strategy", "Nights", "gCO2 per night", "Saving vs timer (%)", "Gap captured (95% CI)"]

    sens = opt[opt["strategy"].isin(["lgbm", "yesterday", "last_week"])].copy()
    sens["cell"] = (100 * sens["gap_captured"]).round(0).astype(int).astype(str) + "%"
    sens = sens.pivot(index="scenario", columns="strategy", values="cell")[["lgbm", "yesterday", "last_week"]]
    sens = sens.reindex([s.name for s in sensitivity_set() if s.name in sens.index]).reset_index()
    sens.columns = ["Scenario", "LightGBM", "Yesterday*", "Last week"]

    rk = ranking_t.copy()
    rk["source"] = nm(rk["source"].replace({"random": "Random choice"}))
    rk["cell"] = (100 * rk["mean_overlap"]).round(0).astype(int).astype(str) + "%"
    rk = rk.pivot(index="source", columns="k", values="cell").reset_index()
    rk.columns = ["Forecast"] + [f"Greenest {c} slots" for c in rk.columns[1:]]

    ts, te = folds["test_start"].min(), folds["test_end"].max()
    nights = int(default["nights"].max())
    parts = [
        "# Layer 1 results: day-ahead GB carbon intensity and EV charging\n",
        "_Generated by `carbon-charge report`; do not edit by hand._\n",
        f"Out-of-sample period: **{ts} to {te}** ({len(folds)} folds, model refit every "
        f"{(pd.to_datetime(folds['test_end']) - pd.to_datetime(folds['test_start'])).dt.days.max() + 1} days, "
        "rolling origin). Every forecast for day D uses only data published before 11:00 UK time on D-1.\n",
        "## Headline: share of the timer-to-perfect-foresight gap captured\n",
        f"Default car: plug in 18:00, 8 kWh by 07:00, 7 kW charger, every night ({nights} nights). "
        "Emissions are priced at actual carbon intensity. 'Gap captured' = (timer − strategy) / (timer − perfect foresight), "
        "totalled over nights; the CI is a 7-night moving-block bootstrap.\n",
        _md(head), "",
        "## Forecast accuracy (MAE / RMSE, gCO2/kWh)\n",
        "All models scored on exactly the same half-hours.\n",
        _md(o.rename(columns={"model": "Model", "n": "Half-hours", "mae": "MAE", "rmse": "RMSE"})), "",
        "### By season\n", _md(_pair(season, "season").rename(columns=names)), "",
        "### By time of day (UK local, 4-hour blocks)\n", _md(_pair(tod, "tod_block").rename(columns=names)), "",
        "## Slot-ranking accuracy in the charging window (18:00-07:00)\n",
        "Overlap between the k slots the forecast ranks greenest and the k actually greenest. "
        "k=3 is the number of slots the default car needs.\n",
        _md(rk), "",
        "## Sensitivity: gap captured by scenario\n",
        "One-at-a-time variations around the default car.\n", _md(sens), "",
        "## Notes and caveats\n",
        "- \\*'Same period yesterday' obeys the cutoff: at 11:00 on D-1 only the first part of D-1 is published, "
        "so periods after ~09:30 fall back to the same period on D-2. About 58% of periods use the fallback.",
    ]
    if exists("optimiser_neso") and exists("metrics_neso_logged_comparison"):
        n = read("optimiser_neso")
        parts.append(f"- NESO's own day-ahead forecast is scored on the {int(n['nights'].max())} nights the daily logger has covered; "
                     "see `optimiser_neso.csv` and `metrics_neso_logged_comparison.csv`.")
    else:
        parts.append("- **NESO's forecast is not scored yet.** Its historical `forecast` field is not day-ahead "
                     "(`DATA_ISSUES.md` CI-1), so the baseline comes from the daily logger, which only started on 2026-09-30. "
                     "Rerun `carbon-charge report` after the logger has accumulated a few weeks of snapshots.")
    parts += [
        "- `metrics_reference_neso_latest_revision.csv` scores that non-day-ahead field for context only; it is not a valid baseline.",
        "- Weather features exist only from 2024-03-07 (`DATA_ISSUES.md` WX-3), so early folds train on little weather data.",
        "- The optimiser plans each half-hour with the forecast for its own settlement day (issued 11:00 on the day before that day).",
        "- Publication lags for carbon intensity actuals (1 h) and ECMWF runs (8 h) are assumptions; see `DATA_ISSUES.md` X-1, WX-2.",
    ]
    return "\n".join(parts) + "\n"
