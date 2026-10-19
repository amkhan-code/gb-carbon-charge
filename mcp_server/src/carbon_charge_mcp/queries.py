"""Every SQL statement the server can run. Fixed text with `?` placeholders only.

No tool accepts SQL, and no identifier or keyword is ever built from an argument: values are bound,
never interpolated. That is what keeps the agent-facing boundary a boundary.
"""

REQUIRED_TABLES = (
    "results_meta", "results_arms", "results_scenarios", "results_metrics", "data_issues",
    "ci_history", "backtest_forecast", "backtest_price_forecast",
)

META = "SELECT * FROM results_meta"
ARMS = "SELECT name, kind, target, description, uses_sources, note FROM results_arms ORDER BY kind, name"
SCENARIOS = "SELECT name, plug_in, energy_kwh, power_kw, deadline, nights FROM results_scenarios"
ISSUE_COUNT = "SELECT count(*) AS n FROM data_issues"
ISSUE_IDS = "SELECT id FROM data_issues"

CARBON_ARM_COVERAGE = (
    "SELECT model AS arm, count(*) AS n_half_hours, min(ts_utc) AS first_ts, max(ts_utc) AS last_ts "
    "FROM backtest_forecast GROUP BY model"
)
PRICE_ARM_COVERAGE = (
    "SELECT model AS arm, count(*) AS n_half_hours, min(ts_utc) AS first_ts, max(ts_utc) AS last_ts "
    "FROM backtest_price_forecast GROUP BY model"
)
DATA_COVERAGE = (
    "SELECT min(settlement_date) AS first_day, max(settlement_date) AS last_day, count(*) AS n_half_hours FROM ci_history"
)
CLASS_ARMS = (
    "SELECT question_class, arm, count(*) AS n_rows FROM results_metrics GROUP BY question_class, arm ORDER BY 1, 2"
)
CLASS_GROUPS = (
    "SELECT question_class, group_key FROM results_metrics GROUP BY question_class, group_key ORDER BY 1, 2"
)

FORECAST_DAY = {
    "carbon": (
        "SELECT h.settlement_period AS period, h.ts_utc, h.actual_gco2_kwh AS actual, f.model, f.forecast_gco2_kwh AS forecast "
        "FROM ci_history h LEFT JOIN backtest_forecast f ON f.ts_utc = h.ts_utc "
        "WHERE h.settlement_date = ? ORDER BY h.settlement_period, f.model"
    ),
    "price": (
        "SELECT h.settlement_period AS period, h.ts_utc, h.price_gbp_mwh AS actual, f.model, f.forecast_gbp_mwh AS forecast "
        "FROM price_mid h LEFT JOIN backtest_price_forecast f ON f.ts_utc = h.ts_utc "
        "WHERE h.settlement_date = ? ORDER BY h.settlement_period, f.model"
    ),
}

SCENARIO = "SELECT name, plug_in, energy_kwh, power_kw, deadline, nights FROM results_scenarios WHERE name = ?"
CI_RANGE = "SELECT ts_utc, actual_gco2_kwh AS v FROM ci_history WHERE ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc"
PRICE_RANGE = "SELECT ts_utc, price_gbp_mwh AS v FROM price_mid WHERE ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc"
CARBON_FC_RANGE = (
    "SELECT ts_utc, forecast_gco2_kwh AS v FROM backtest_forecast WHERE model = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc"
)
PRICE_FC_RANGE = (
    "SELECT ts_utc, forecast_gbp_mwh AS v FROM backtest_price_forecast WHERE model = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc"
)

METRIC_ROWS = (
    "SELECT target, arm, vs_arm, group_key, group_value, metric, value, ci_low, ci_high, n, n_unit, unit FROM results_metrics "
    "WHERE question_class = ? AND group_key = ? AND (? IS NULL OR scenario = ?) AND (? IS NULL OR objective = ?) "
    "AND (? IS NULL OR eval_window = ?)"
)
CLASS_METRICS = "SELECT DISTINCT metric FROM results_metrics WHERE question_class = ? ORDER BY metric"
EVAL_WINDOWS = "SELECT DISTINCT eval_window FROM results_metrics WHERE eval_window IS NOT NULL ORDER BY eval_window"
ISSUES = "SELECT id, source, title, flagged, handling, effect, status, body FROM data_issues"
ISSUE_TITLES = "SELECT id, title, effect, handling FROM data_issues"
