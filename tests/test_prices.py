from datetime import date

import pandas as pd

from carbon_charge.ingest.prices import transform_day_ahead, transform_mid


def _da(date_, hours, price=50.0):
    return pd.DataFrame({
        "Date": date_, "Delivery Period": [f"{h:02d}:00 - {(h + 1) % 24:02d}:00" for h in hours],
        "Price": [str(price + h) for h in hours],
    })


def test_day_ahead_hours_are_utc_and_settlement_columns_follow():
    out = transform_day_ahead(_da("2024-07-10", range(24)))
    assert out["ts_utc"].is_unique and len(out) == 24
    assert out["ts_utc"].iloc[0] == pd.Timestamp("2024-07-10 00:00")
    # In summer 00:00 UTC is 01:00 BST = settlement period 3; 23:00 UTC belongs to the next day.
    assert (out["settlement_date"].iloc[0], out["settlement_period"].iloc[0]) == (date(2024, 7, 10), 3)
    assert (out["settlement_date"].iloc[-1], out["settlement_period"].iloc[-1]) == (date(2024, 7, 11), 1)


def test_day_ahead_24_rows_on_clock_change_days_map_to_distinct_instants():
    for day in ("2024-03-31", "2024-10-27"):
        out = transform_day_ahead(_da(day, range(24)))
        assert len(out) == 24 and out["ts_utc"].is_unique
        assert out["price_gbp_mwh"].notna().all()


def test_day_ahead_duplicate_hours_keep_last_and_bad_prices_become_null():
    raw = pd.concat([_da("2024-01-10", [0, 1]), _da("2024-01-10", [1], price=99.0)], ignore_index=True)
    raw.loc[0, "Price"] = "n/a"
    out = transform_day_ahead(raw)
    assert len(out) == 2
    assert pd.isna(out["price_gbp_mwh"].iloc[0]) and out["price_gbp_mwh"].iloc[1] == 100.0


def test_mid_uses_apx_only_and_nulls_zero_volume_placeholder():
    raw = pd.DataFrame({
        "dataProvider": ["APXMIDP", "N2EXMIDP", "APXMIDP", "APXMIDP"],
        "startTime": ["2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z", "2024-01-01T00:30:00Z", "2024-01-01T01:00:00Z"],
        "price": [80.0, 0.0, 0.0, -5.0], "volume": [1000.0, 0.0, 0.0, 300.0],
    })
    out = transform_mid(raw).set_index("ts_utc")
    assert len(out) == 3  # N2EXMIDP row dropped
    assert out.loc["2024-01-01 00:00", "price_gbp_mwh"] == 80.0
    assert pd.isna(out.loc["2024-01-01 00:30", "price_gbp_mwh"])  # zero volume: placeholder, not a price
    assert out.loc["2024-01-01 01:00", "price_gbp_mwh"] == -5.0  # negative prices are real
