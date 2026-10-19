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
