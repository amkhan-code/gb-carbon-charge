"""Static charts for reports/RESULTS.md and the README.

Rules: one accent hue against neutral gray for emphasis, direct labels instead of legends where
there are few series, no dual axes, thin marks, recessive grid. Colours are the validated
categorical slots 1-2 (blue, orange) plus neutrals.
"""

from datetime import date

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from carbon_charge import config  # noqa: E402
from carbon_charge.charging import strategies as st  # noqa: E402
from carbon_charge.charging.scenarios import DEFAULT  # noqa: E402
from carbon_charge.timeutils import UK  # noqa: E402

SURFACE, INK, INK2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e5e4e0", "#b8b7b1"
BLUE, ORANGE = "#2a78d6", "#eb6834"
DPI = 160

CI_NAMES = {"lgbm_price": "LightGBM + price features", "lgbm": "LightGBM", "lgbm_noweather": "LightGBM, no weather",
            "yesterday": "Same period yesterday", "last_week": "Same period last week"}
PRICE_NAMES = {"price_lgbm": "LightGBM (basis model)", "price_da": "Day-ahead price used directly",
               "price_da_basis7d": "Day-ahead + 7-day basis", "price_yesterday": "Same period yesterday",
               "price_last_week": "Same period last week"}


def figures_dir():
    d = config.REPORTS_DIR / "figures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=10, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _fig(w=9.0, h=4.2):
    fig, ax = plt.subplots(figsize=(w, h), facecolor=SURFACE)
    _style(ax)
    return fig, ax


def _title(fig, title, subtitle):
    fig.text(0.02, 0.965, title, fontsize=13, fontweight="bold", color=INK, va="top")
    fig.text(0.02, 0.905, subtitle, fontsize=10, color=INK2, va="top")


def _save(fig, name):
    path = figures_dir() / name
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


def _hbars(ax, labels, values, colors, fmt, xerr=None, pad=0.02):
    y = np.arange(len(labels))[::-1]
    ax.barh(y, values, height=0.6, color=colors, zorder=3)
    if xerr is not None:
        ax.errorbar(values, y, xerr=xerr, fmt="none", ecolor=INK2, elinewidth=1.2, capsize=3, zorder=4)
    ax.set_yticks(y, labels, color=INK, fontsize=10.5)
    span = max(abs(v) for v in values) or 1
    for yi, v, c in zip(y, values, colors):
        hi = v + (xerr[1][list(y[::-1]).index(yi) if False else 0] if False else 0)
        ax.text(v + (pad * span if v >= 0 else -pad * span) + (0 if xerr is None else 0), yi, fmt(v),
                va="center", ha="left" if v >= 0 else "right", fontsize=10, color=INK, zorder=5)
    return y


def gap_captured(summary: pd.DataFrame) -> None:
    d = summary[(summary["scenario"] == "default") & summary["strategy"].isin(CI_NAMES)].copy()
    d = d.sort_values("gap_captured", ascending=False)
    fig, ax = _fig(9.2, 3.9)
    labels = [CI_NAMES[s] for s in d["strategy"]]
    accent = {"lgbm_price", "lgbm"}
    colors = [BLUE if s in accent else MUTED for s in d["strategy"]]
    y = np.arange(len(d))[::-1]
    vals = 100 * d["gap_captured"].to_numpy()
    lo, hi = 100 * d["gap_ci_low"].to_numpy(), 100 * d["gap_ci_high"].to_numpy()
    ax.barh(y, vals, height=0.6, color=colors, zorder=3)
    ax.errorbar(vals, y, xerr=[vals - lo, hi - vals], fmt="none", ecolor=INK2, elinewidth=1.2, capsize=3, zorder=4)
    ax.axvline(0, color=INK2, linewidth=1, zorder=5)
    ax.set_yticks(y, labels, color=INK, fontsize=10.5)
    for yi, v, h in zip(y, vals, hi):
        ax.text(max(h, v) + 1.5, yi, f"{v:.0f}%", va="center", fontsize=10, color=INK, zorder=6)
    ax.set_xlim(min(-30, np.nanmin(lo) - 6), max(60, np.nanmax(hi) + 8))
    ax.set_xlabel("Share of the gap between an overnight timer and perfect foresight (%)", color=INK2, fontsize=10)
    _title(fig, "How much of the possible carbon saving does each forecast capture?",
           "Default car: 8 kWh from 18:00 to 07:00. Whiskers are 95% confidence intervals. 0% = no better than the timer.")
    fig.subplots_adjust(left=0.27, right=0.97, top=0.80, bottom=0.17)
    _save(fig, "gap_captured.png")


def _mae_bars(df, names, name_file, title, subtitle, unit, accent: dict[str, str]):
    d = df[df["model"].isin(names)].sort_values("mae")
    fig, ax = _fig(9.2, 3.6)
    y = np.arange(len(d))[::-1]
    colors = [accent.get(m, MUTED) for m in d["model"]]
    ax.barh(y, d["mae"], height=0.6, color=colors, zorder=3)
    ax.set_yticks(y, [names[m] for m in d["model"]], color=INK, fontsize=10.5)
    for yi, v in zip(y, d["mae"]):
        ax.text(v + d["mae"].max() * 0.015, yi, f"{v:.1f}", va="center", fontsize=10, color=INK, zorder=6)
    ax.set_xlim(0, d["mae"].max() * 1.12)
    ax.set_xlabel(f"Average miss, MAE ({unit}). Lower is better.", color=INK2, fontsize=10)
    _title(fig, title, subtitle)
    fig.subplots_adjust(left=0.27, right=0.97, top=0.78, bottom=0.18)
    _save(fig, name_file)


def carbon_accuracy(overall: pd.DataFrame) -> None:
    _mae_bars(overall, CI_NAMES, "carbon_accuracy.png", "Carbon forecast: how far off is each guess?",
              "Day-ahead forecasts scored on the same 30,564 half-hours, Jan 2025 to Sep 2026.", "gCO2/kWh",
              {"lgbm_price": BLUE, "lgbm": BLUE})


def price_accuracy(overall: pd.DataFrame) -> None:
    _mae_bars(overall, PRICE_NAMES, "price_accuracy.png", "Price forecast: the model only ties the published price",
              "Realised half-hourly Market Index price, same 30,510 half-hours for every forecast.", "GBP/MWh",
              {"price_lgbm": BLUE, "price_da": ORANGE})


def frontier(fr: pd.DataFrame) -> None:
    fr = fr.assign(lam=fr["lam"].astype(str))
    order = ["0", "50", "100", "250", "500", "1000", "2500", "carbon only"]
    get = lambda strat: fr[fr["strategy"] == strat].set_index("lam").loc[order]  # noqa: E731
    model, perfect, timer = get("model"), get("perfect"), get("timer").iloc[0]
    fig, ax = _fig(9.2, 4.9)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    for series, color, label, dx in ((perfect, ORANGE, "Perfect foresight (upper bound)", -0.001),
                                     (model, BLUE, "LightGBM pipeline", 0.0)):
        ax.plot(series["mean_cost_gbp"], series["mean_carbon_g"], color=color, linewidth=2, zorder=3)
        ax.scatter(series["mean_cost_gbp"], series["mean_carbon_g"], s=42, color=color, edgecolor=SURFACE,
                   linewidth=1.5, zorder=4)
    ax.scatter([timer["mean_cost_gbp"]], [timer["mean_carbon_g"]], s=70, color=INK2, edgecolor=SURFACE,
               linewidth=1.5, zorder=5)
    ax.annotate("Overnight timer", (timer["mean_cost_gbp"], timer["mean_carbon_g"]), xytext=(-10, 0),
                textcoords="offset points", ha="right", va="center", color=INK, fontsize=10.5, fontweight="bold")
    for series, txt, key, off, ha in ((model, "LightGBM pipeline", "2500", (6, -34), "center"),
                                      (perfect, "Perfect foresight", "500", (12, 8), "left")):
        r = series.loc[key]
        ax.annotate(txt, (r["mean_cost_gbp"], r["mean_carbon_g"]), xytext=off, textcoords="offset points",
                    ha=ha, color=INK, fontsize=10.5, fontweight="bold")
    for lam, lab, off, ha in (("0", "cost only", (-10, 6), "right"), ("250", "GBP 250/t", (0, -18), "center"),
                              ("carbon only", "carbon only", (10, 12), "center")):
        r = model.loc[lam]
        ax.annotate(lab, (r["mean_cost_gbp"], r["mean_carbon_g"]), xytext=off, textcoords="offset points",
                    ha=ha, color=INK2, fontsize=9.5)
    ax.margins(x=0.06, y=0.10)
    ax.set_xlabel("Wholesale cost per night (GBP)  →  worse", color=INK2, fontsize=10)
    ax.set_ylabel("Carbon per night (g CO2)  →  worse", color=INK2, fontsize=10)
    _title(fig, "Cheap and clean mostly go together",
           "Each dot is a different price on carbon in the plan (cost only → carbon only). Down and to the left is better.")
    fig.subplots_adjust(left=0.10, right=0.97, top=0.85, bottom=0.13)
    _save(fig, "cost_carbon_frontier.png")


def sample_night(con: duckdb.DuckDBPyConnection, nights: pd.DataFrame) -> None:
    """One representative night: what the forecast said, what happened, and which slots each plan used."""
    n = nights.copy()
    n["night"] = pd.to_datetime(n["night"]).dt.date
    n = n[(n["timer"] - n["perfect"]) > 100]
    if n.empty:
        return
    share = ((n["timer"] - n["lgbm_price"]) / (n["timer"] - n["perfect"]))
    target = float(np.nanmedian(share))
    night = n.loc[(share - target).abs().idxmin(), "night"]
    slots = st.window_slots(night, DEFAULT)
    ci = con.execute("SELECT ts_utc, actual_gco2_kwh a FROM ci_history WHERE ts_utc BETWEEN ? AND ?",
                     [slots[0], slots[-1]]).df().set_index("ts_utc")["a"].reindex(slots)
    fc = con.execute("SELECT ts_utc, forecast_gco2_kwh f FROM backtest_forecast WHERE model='lgbm_price' "
                     "AND ts_utc BETWEEN ? AND ?", [slots[0], slots[-1]]).df().set_index("ts_utc")["f"].reindex(slots)
    plans = {"Overnight timer": st.overnight_timer(night, DEFAULT),
             "Forecast-optimised": st.optimised(night, DEFAULT, fc),
             "Perfect foresight": st.optimised(night, DEFAULT, ci)}
    colors = {"Overnight timer": INK2, "Forecast-optimised": BLUE, "Perfect foresight": ORANGE}
    local = pd.DatetimeIndex(slots).tz_localize("UTC").tz_convert(UK)
    x = np.arange(len(slots))
    fig, ax = _fig(9.2, 5.0)
    ax.plot(x, ci.to_numpy(), color=INK, linewidth=2, zorder=3)
    ax.plot(x, fc.to_numpy(), color=BLUE, linewidth=2, linestyle=(0, (4, 2)), zorder=3)
    lo = float(np.nanmin([ci.min(), fc.min()]))
    hi = float(np.nanmax([ci.max(), fc.max()]))
    rng = hi - lo
    lane = lo - rng * 0.22
    for i, (name, plan) in enumerate(plans.items()):
        yl = lane - i * rng * 0.13
        for t in plan.index:
            j = list(slots).index(t)
            ax.add_patch(plt.Rectangle((j - 0.42, yl - rng * 0.045), 0.84, rng * 0.09, color=colors[name], zorder=3))
        g = st.emissions_g(plan, ci)
        ax.text(-0.3, yl, f"{name}: {g:,.0f} g", va="center", ha="left", fontsize=9.5, color=INK)
    ax.axhline(lane + rng * 0.12, color=GRID, linewidth=1.2, zorder=2)
    ax.set_ylim(lane - 2 * rng * 0.13 - rng * 0.12, hi + rng * 0.08)
    ticks = [i for i, t in enumerate(local) if t.minute == 0 and t.hour % 2 == 0]
    ax.set_xticks(ticks, [f"{local[i].hour:02d}:00" for i in ticks])
    ax.set_xlim(-0.6, len(slots) - 0.4)
    ax.text(0.2, hi + rng * 0.02, "What actually happened", color=INK, fontsize=10, fontweight="bold")
    ax.text(0.2, lo + rng * 0.04, "The forecast (dashed)", color=BLUE, fontsize=10, fontweight="bold")
    ax.set_ylabel("Carbon intensity (gCO2/kWh)", color=INK2, fontsize=10)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_yticks([v for v in ax.get_yticks() if lo - rng * 0.05 <= v <= hi + rng * 0.05])
    _title(fig, f"One typical night: {night:%d %b %Y}, plug in 18:00, ready by 07:00",
           "Boxes are the half-hours each plan actually charges in (8 kWh = 2.3 slots at 7 kW). Grams are the carbon emitted.")
    fig.subplots_adjust(left=0.09, right=0.97, top=0.85, bottom=0.10)
    _save(fig, "sample_night.png")


def make_all(con: duckdb.DuckDBPyConnection) -> list[str]:
    r = config.REPORTS_DIR
    gap_captured(pd.read_csv(r / "optimiser_summary.csv"))
    carbon_accuracy(pd.read_csv(r / "metrics_overall.csv"))
    price_accuracy(pd.read_csv(r / "metrics_price_overall.csv"))
    frontier(pd.read_csv(r / "optimiser_blend_frontier.csv"))
    sample_night(con, pd.read_csv(r / "optimiser_nights_default.csv"))
    return sorted(p.name for p in figures_dir().glob("*.png"))
