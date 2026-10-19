"""Load the pipeline's results into DuckDB so they can be served read-only.

`carbon-charge report` writes CSVs; this module turns them into a small set of tidy tables:

  results_metrics  one long table (question_class, arm, metric, value, CI, n, ...)
  results_arms     what each model / baseline / strategy / pipeline is
  results_scenarios the charging scenarios
  results_meta     one row: when exported, from which commit, through which date
  data_issues      DATA_ISSUES.md parsed into rows

Everything is rebuilt from the CSVs and DATA_ISSUES.md, so it is idempotent. The MCP server reads
these tables and nothing else; the knowledge of what each CSV means lives here, in the pipeline.
"""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from carbon_charge import catalog, config
from carbon_charge.charging.scenarios import sensitivity_set

SCHEMA_VERSION = 1
METRIC_COLUMNS = [
    "question_class", "target", "objective", "arm", "vs_arm", "scenario", "eval_window",
    "group_key", "group_value", "metric", "value", "ci_low", "ci_high", "n", "n_unit", "unit",
]
ISSUE_SOURCES = {"CI": "carbon_intensity", "DEM": "demand_forecast", "WX": "weather", "PX": "prices",
                 "WIND": "wind", "X": "cross_source", "ENV": "environment"}


# --- tidy rows ----------------------------------------------------------------

def _rows(df: pd.DataFrame, *, metrics: dict[str, tuple[str, str]], base: dict, arm: str, n: str,
          n_unit: str, group: tuple[str, str] | None = None, ci: dict[str, tuple[str, str]] | None = None,
          vs: str | None = None) -> list[dict]:
    """One row per (input row, metric). `metrics` maps a column to (metric name, unit)."""
    out = []
    for r in df.to_dict("records"):
        for col, (name, unit) in metrics.items():
            if col not in r or pd.isna(r[col]):
                continue
            lo, hi = (None, None)
            if ci and col in ci:
                lo, hi = r.get(ci[col][0]), r.get(ci[col][1])
            out.append({
                **{k: None for k in METRIC_COLUMNS}, **base,
                "arm": str(r[arm]), "vs_arm": str(r[vs]) if vs else None,
                "group_key": group[0] if group else "overall",
                "group_value": str(r[group[1]]) if group else "all",
                "metric": name, "value": float(r[col]),
                "ci_low": None if lo is None or pd.isna(lo) else float(lo),
                "ci_high": None if hi is None or pd.isna(hi) else float(hi),
                "n": int(r[n]) if n in r and not pd.isna(r[n]) else None, "n_unit": n_unit, "unit": unit,
            })
    return out


def _accuracy(df, target, unit, group=None):
    return _rows(df, metrics={"mae": ("mae", unit), "rmse": ("rmse", unit)}, arm="model", n="n", n_unit="half_hours",
                 group=group, base={"question_class": f"{target}_forecast_accuracy", "target": target})


def _ranking(df, target):
    df = df.assign(metric_name=df["k"].map(lambda k: f"overlap_top{int(k)}"))
    rows = []
    for r in df.to_dict("records"):
        # The chance baseline appears in both the carbon and the price ranking: name them apart.
        arm = f"random_{target}" if r["source"] == "random" else str(r["source"])
        rows.append({**{k: None for k in METRIC_COLUMNS}, "question_class": "slot_ranking", "target": target,
                     "arm": arm, "group_key": "slots_chosen", "group_value": str(int(r["k"])),
                     "metric": r["metric_name"], "value": float(r["mean_overlap"]), "n": int(r["nights"]),
                     "n_unit": "nights", "unit": "fraction"})
    return rows


def _value(df, objective, question_class, group=None, extra_metrics=None):
    metrics = {"gap_captured": ("gap_captured", "fraction"), "saving_vs_timer_pct": ("saving_vs_timer_pct", "percent"),
               "saving_vs_arrival_pct": ("saving_vs_arrival_pct", "percent")}
    metrics.update(extra_metrics or {})
    ci = {"gap_captured": ("gap_ci_low", "gap_ci_high")}
    rows = []
    for scen, part in df.groupby("scenario"):
        rows += _rows(part, metrics=metrics, arm="strategy", n="nights", n_unit="nights", group=group, ci=ci,
                      base={"question_class": question_class, "target": "charging", "objective": objective,
                            "scenario": scen})
    return rows


def _experiment_rows(root: Path) -> list[dict]:
    rows: list[dict] = []
    for d in sorted(p for p in (root / "experiments").glob("*") if p.is_dir()):
        w = d.name
        base = {"question_class": "experiments", "target": "carbon", "eval_window": w}
        rd = lambda name: pd.read_csv(d / f"{name}.csv") if (d / f"{name}.csv").exists() else None  # noqa: E731
        if (df := rd("accuracy")) is not None:
            rows += _rows(df, metrics={"mae": ("mae", "gCO2/kWh"), "rmse": ("rmse", "gCO2/kWh"),
                                       "within_night_mae": ("within_night_mae", "gCO2/kWh"),
                                       "wind_coverage": ("wind_coverage", "fraction")},
                          arm="model", n="n", n_unit="half_hours", base=base)
        if (df := rd("accuracy_by_wind_availability")) is not None:
            df = df.assign(wind_available=df["wind_available"].astype(str).str.lower())
            rows += _rows(df, metrics={"mae": ("mae", "gCO2/kWh"), "within_night_mae": ("within_night_mae", "gCO2/kWh")},
                          arm="model", n="n", n_unit="half_hours", group=("wind_available", "wind_available"), base=base)
        if (df := rd("ranking")) is not None:
            for r in _ranking(df, "carbon"):
                rows.append({**r, "question_class": "experiments", "eval_window": w})
        if (df := rd("gap_captured")) is not None:
            rows += _rows(df, metrics={"gap_captured": ("gap_captured", "fraction"),
                                       "mean_g_per_night": ("mean_g_per_night", "gCO2")},
                          arm="strategy", n="nights", n_unit="nights", base={**base, "objective": "carbon"},
                          ci={"gap_captured": ("gap_ci_low", "gap_ci_high")})
        if (df := rd("paired_gap")) is not None:
            rows += _rows(df, metrics={"gap_diff": ("gap_captured_difference", "fraction"),
                                       "grams_saved_per_night": ("grams_saved_per_night", "gCO2")},
                          arm="model", vs="vs", n="nights", n_unit="nights", base={**base, "objective": "carbon"},
                          ci={"gap_diff": ("ci_low", "ci_high")})
        if (df := rd("paired_shape_error")) is not None:
            rows += _rows(df, metrics={"shape_mae_diff": ("shape_mae_difference", "gCO2/kWh"),
                                       "share_of_nights_better": ("share_of_nights_better", "fraction")},
                          arm="model", vs="vs", n="shape_mae_diff", n_unit="none", base=base)
    for r in rows:
        if r["n_unit"] == "none":
            r["n"], r["n_unit"] = None, None
    return rows


def build_metrics(root: Path) -> pd.DataFrame:
    """The long results table, built from the CSVs under `root` (the reports directory)."""
    rd = lambda name: pd.read_csv(root / f"{name}.csv")  # noqa: E731
    rows: list[dict] = []
    rows += _accuracy(rd("metrics_overall"), "carbon", "gCO2/kWh")
    rows += _accuracy(rd("metrics_by_season"), "carbon", "gCO2/kWh", ("season", "season"))
    rows += _accuracy(rd("metrics_by_time_of_day"), "carbon", "gCO2/kWh", ("time_of_day", "tod_block"))
    rows += _accuracy(rd("metrics_by_hour"), "carbon", "gCO2/kWh", ("local_hour", "local_hour"))
    rows += [r | {"group_key": "reference_sample"} for r in _accuracy(rd("metrics_reference_neso_latest_revision"), "carbon", "gCO2/kWh")]
    rows += _accuracy(rd("metrics_price_overall"), "price", "GBP/MWh")
    rows += _accuracy(rd("metrics_price_by_season"), "price", "GBP/MWh", ("season", "season"))
    rows += _accuracy(rd("metrics_price_by_time_of_day"), "price", "GBP/MWh", ("time_of_day", "tod_block"))
    rows += _accuracy(rd("metrics_price_by_hour"), "price", "GBP/MWh", ("local_hour", "local_hour"))
    rows += _ranking(rd("ranking"), "carbon") + _ranking(rd("metrics_price_ranking"), "price")
    rows += _value(rd("optimiser_summary"), "carbon", "charging_value",
                   extra_metrics={"mean_g_per_night": ("mean_g_per_night", "gCO2")})
    rows += _value(rd("optimiser_blend_cost_sensitivity"), "cost", "charging_value",
                   extra_metrics={"mean_cost_gbp": ("mean_cost_gbp", "GBP"), "mean_carbon_g": ("mean_carbon_g", "gCO2")})
    fr = rd("optimiser_blend_frontier").assign(lam=lambda d: d["lam"].astype(str))
    rows += _value(fr, "blend", "cost_carbon_tradeoff", group=("carbon_price_gbp_per_tonne", "lam"),
                   extra_metrics={"mean_cost_gbp": ("mean_cost_gbp", "GBP"), "mean_carbon_g": ("mean_carbon_g", "gCO2")})
    rows += _experiment_rows(root)
    return pd.DataFrame(rows, columns=METRIC_COLUMNS)


# --- data issues ----------------------------------------------------------------

_HEADING = re.compile(r"^###\s+([A-Z]+-\d+):\s*(.+?)\s*$")


def parse_data_issues(text: str) -> pd.DataFrame:
    """DATA_ISSUES.md -> one row per `### ID: title` entry."""
    issues, cur = [], None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "lines": []}
            issues.append(cur)
        elif line.startswith("## "):
            cur = None
        elif cur is not None:
            cur["lines"].append(line)
    rows = []
    for it in issues:
        body = "\n".join(it["lines"]).strip()
        bullets = re.split(r"\n(?=- )", body)
        pick = lambda key: next(  # noqa: E731
            (re.sub(r"\s+", " ", b[2 + len(key) + 1:]).strip() for b in bullets if b.startswith(f"- {key}:")), None)
        title = it["title"]
        flagged = "FLAGGED" in title
        title = re.sub(r"\s*[—-]+\s*FLAGGED\s*$", "", title).strip()
        rows.append({"id": it["id"], "source": ISSUE_SOURCES.get(it["id"].split("-")[0], "other"), "title": title,
                     "flagged": flagged, "handling": pick("Handling"), "effect": pick("Effect"), "status": pick("Status"),
                     "body": re.sub(r"[ \t]+\n", "\n", body)})
    return pd.DataFrame(rows, columns=["id", "source", "title", "flagged", "handling", "effect", "status", "body"])


# --- persistence ---------------------------------------------------------------

def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None


def persist(con: duckdb.DuckDBPyConnection, reports: Path | None = None, issues_path: Path | None = None) -> dict[str, int]:
    """Rebuild every results table from the CSVs and DATA_ISSUES.md. Returns row counts."""
    reports = reports or config.REPORTS_DIR
    issues_path = issues_path or Path("DATA_ISSUES.md")
    metrics = build_metrics(reports)
    arms = pd.DataFrame([{"name": a.name, "kind": a.kind, "target": a.target, "description": a.description,
                          "uses_sources": ",".join(a.uses_sources), "note": a.note} for a in catalog.arms()])
    scen = pd.DataFrame([{"name": s.name, "plug_in": s.plug_in.strftime("%H:%M"), "energy_kwh": s.energy_kwh,
                          "power_kw": s.power_kw, "deadline": s.deadline.strftime("%H:%M"), "nights": s.nights}
                         for s in sensitivity_set()])
    folds = pd.read_csv(reports / "backtest_folds.csv")
    data_through = con.execute(
        "SELECT max(settlement_date) FROM (SELECT settlement_date FROM ci_history GROUP BY 1 HAVING count(*) >= 46)").fetchone()[0]
    meta = pd.DataFrame([{
        "schema_version": SCHEMA_VERSION, "exported_at_utc": datetime.now(timezone.utc).replace(tzinfo=None),
        "exported_from_commit": _git_commit(), "test_start": pd.to_datetime(folds["test_start"]).min().date(),
        "test_end": pd.to_datetime(folds["test_end"]).max().date(), "data_through": data_through,
    }])
    issues = parse_data_issues(issues_path.read_text())
    for name, df in (("results_metrics", metrics), ("results_arms", arms), ("results_scenarios", scen),
                     ("results_meta", meta), ("data_issues", issues)):
        con.register("_df", df)
        try:
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
        finally:
            con.unregister("_df")
    return {t: len(d) for t, d in (("results_metrics", metrics), ("results_arms", arms), ("results_scenarios", scen),
                                    ("results_meta", meta), ("data_issues", issues))}
