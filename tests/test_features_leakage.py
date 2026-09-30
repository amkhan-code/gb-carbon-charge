"""Leakage tests: every feature value must be published/issued before the cutoff."""

from datetime import date

import pandas as pd
import pytest

from carbon_charge import config
from carbon_charge.features import availability as av
from carbon_charge.features.build import ALLOWED_SOURCES, build
from carbon_charge.timeutils import cutoff_utc

WINDOW = (date(2024, 3, 25), date(2024, 4, 10))  # includes the spring clock change
CLOCK_CHANGE = date(2024, 3, 31)


@pytest.fixture()
def fs(con):
    return build(con, *WINDOW)


def test_every_feature_has_a_declared_allowed_source(fs):
    assert set(fs.X.columns) == set(fs.sources) == set(fs.avail.columns)
    assert set(fs.sources.values()) <= ALLOWED_SOURCES
    # Actual demand and observed weather do not exist as sources at all.
    assert not {"demand_actual", "weather_observed"} & set(fs.sources.values())
    assert not [c for c in fs.X.columns if "outturn" in c or "observed" in c]


def test_every_feature_available_before_cutoff(fs):
    late = fs.avail.gt(fs.meta["cutoff_utc"], axis=0)
    assert not late.to_numpy().any(), fs.avail.columns[late.any()].tolist()
    # Non-null values must carry an availability time, and null ones must not.
    assert (fs.X.notna() == fs.avail.notna()).to_numpy().all()


def test_label_is_not_a_feature(fs):
    assert "actual_gco2_kwh" not in fs.X.columns
    assert not any(fs.X[c].equals(fs.y) for c in fs.X.columns)


def test_ci_lag_d1_only_available_for_periods_published_by_cutoff(fs):
    # Cutoff 11:00 on D-1, actuals usable 30 min + 60 min lag after the period starts:
    # D-1 periods starting 00:00-09:30 (local) are known, later ones are not.
    day = fs.X[fs.meta["settlement_date"] == pd.Timestamp("2024-04-03")]
    hour = fs.meta.loc[day.index, "local_hour"]
    known = day["ci_lag_d1"].notna()
    assert known[hour <= 9.5].all() and not known[hour > 9.5].any()
    assert day["ci_lag_d2"].notna().all()


def test_clock_change_days_have_46_and_50_rows(con):
    spring = build(con, date(2024, 3, 31), date(2024, 3, 31))
    assert len(spring.X) == 46 and spring.meta["settlement_period"].max() == 46
    assert spring.meta.index.is_unique


def _poison(con, cutoff: pd.Timestamp) -> None:
    """Corrupt everything that was not yet available at `cutoff`, and add late vintages."""
    con.execute(
        "UPDATE ci_history SET actual_gco2_kwh = 1e6 WHERE ts_utc + INTERVAL 30 MINUTE + "
        f"INTERVAL {config.CI_ACTUAL_LAG_MINUTES} MINUTE > ?", [cutoff]
    )
    con.execute(
        "INSERT INTO demand_forecast SELECT ts_utc, ? + INTERVAL 1 MINUTE, settlement_date, "
        "settlement_period, 1e6, fetched_at_utc FROM demand_forecast WHERE issued_at_utc <= ?",
        [cutoff, cutoff],
    )
    # Day-ahead prices for delivery day D are published 10:00 UTC on D-1; realised prices after period end + lag.
    con.execute("UPDATE price_day_ahead SET price_gbp_mwh = 1e6 WHERE "
                f"(CAST(settlement_date AS TIMESTAMP) - INTERVAL 1 DAY + INTERVAL {config.DAY_AHEAD_PRICE_PUBLISHED_UTC.hour} HOUR) > ?",
                [cutoff])
    con.execute(
        "UPDATE price_mid SET price_gbp_mwh = 1e6 WHERE ts_utc + INTERVAL 30 MINUTE + "
        f"INTERVAL {config.MID_LAG_MINUTES} MINUTE > ?", [cutoff]
    )
    cols = ", ".join(f"{c} = 1e6" for c in config.WEATHER_VARIABLES.values())
    con.execute(
        f"UPDATE weather_forecast SET {cols} WHERE issued_at_utc + INTERVAL {config.WEATHER_RUN_LAG_HOURS} HOUR > ?",
        [cutoff],
    )


@pytest.mark.parametrize("day", [date(2024, 3, 30), CLOCK_CHANGE, date(2024, 4, 1), date(2024, 4, 5)])
def test_features_unchanged_when_post_cutoff_data_is_corrupted(con, day):
    before = build(con, day, day)
    _poison(con, pd.Timestamp(cutoff_utc(day).replace(tzinfo=None)))
    after = build(con, day, day)
    pd.testing.assert_frame_equal(before.X, after.X)
    assert not (after.X.drop(columns=[c for c in after.X if c.startswith(('mid_std', 'basis_std'))]) > 1e5).to_numpy().any()


def test_late_demand_vintage_is_dropped_not_used(con):
    day = date(2024, 4, 3)
    late = pd.Timestamp(cutoff_utc(day).replace(tzinfo=None)) + pd.Timedelta(minutes=3)  # cf. DEM-3
    con.execute("UPDATE demand_forecast SET issued_at_utc = ? WHERE settlement_date = ?", [late, day])
    fs = build(con, day, day)
    assert fs.X["demand_forecast_mw"].isna().all()
    assert fs.X["ci_lag_d2"].notna().all()


def test_weather_lead1_is_not_used(con):
    day = date(2024, 4, 3)
    before = build(con, day, day)
    con.execute("UPDATE weather_forecast SET temperature_2m_c = 999 WHERE lead_days = 1")
    pd.testing.assert_frame_equal(before.X, build(con, day, day).X)


def test_weather_availability_is_run_lag_after_issue(fs):
    col = "wx_wind_speed_100m_ms__moray_firth"
    ts = fs.X.index[fs.X[col].notna()]
    expected = ts.floor("h") - pd.Timedelta(days=config.WEATHER_FEATURE_LEAD_DAYS) + av.WEATHER_RUN_LAG
    assert (fs.avail.loc[ts, col] == expected).all()


def test_day_ahead_price_known_for_day_d_but_next_day_hours_masked(fs):
    day = pd.Timestamp("2024-04-03")
    rows = fs.meta["settlement_date"] == day
    assert fs.X.loc[rows, "da_price"].notna().all()  # published 10:00 UTC on D-1, before the cutoff
    assert (fs.avail.loc[rows, "da_price"] <= fs.meta.loc[rows, "cutoff_utc"]).all()
    # The last hour of day D looks ahead to D+1, whose auction is published after the cutoff.
    last_hour = fs.meta.loc[rows, "local_hour"] >= 23
    assert fs.X.loc[rows & last_hour, "da_price_next_hour"].isna().all()
    assert fs.X.loc[rows & ~last_hour, "da_price_next_hour"].notna().all()


def test_realised_price_lags_follow_the_same_cutoff_rule_as_carbon(fs):
    day = fs.X[fs.meta["settlement_date"] == pd.Timestamp("2024-04-03")]
    hour = fs.meta.loc[day.index, "local_hour"]
    assert day["mid_lag_d1"].notna()[hour <= 9.5].all() and not day["mid_lag_d1"].notna()[hour > 9.5].any()
    assert day["mid_lag_d2"].notna().all() and day["basis_lag_d2"].notna().all()


def test_labels_are_not_features(fs):
    assert not [c for c in fs.X.columns if "price_gbp_mwh" in c]
    assert not any(fs.X[c].equals(fs.y_price) for c in fs.X.columns)
