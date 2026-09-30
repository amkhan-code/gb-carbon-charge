import pandas as pd

from carbon_charge.ingest.demand_forecast import transform


def _raw(rows):
    return pd.DataFrame(rows, columns=["Date", "Settlement_Period", "Demand_Forecast", "Publish_Datetime"])


def test_utc_key_and_issue_time_on_autumn_clock_change():
    raw = _raw([("2024-10-27", p, 20000 + p, "2024-10-26T09:45:00Z") for p in range(1, 51)])
    clean, rejected = transform(raw)
    assert len(clean) == 50 and rejected.empty
    assert clean["ts_utc"].is_unique
    assert clean["ts_utc"].iloc[0] == pd.Timestamp("2024-10-26 23:00")
    assert clean["ts_utc"].iloc[-1] == pd.Timestamp("2024-10-27 23:30")
    assert (clean["issued_at_utc"] == pd.Timestamp("2024-10-26 09:45")).all()


def test_out_of_range_period_is_rejected_on_spring_clock_change():
    raw = _raw([("2024-03-31", p, 20000, "2024-03-30T08:45:00Z") for p in (1, 46, 47)])
    clean, rejected = transform(raw)
    assert list(clean["settlement_period"]) == [1, 46]
    assert list(rejected["Settlement_Period"]) == [47]
