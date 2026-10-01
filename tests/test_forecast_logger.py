import pandas as pd

from carbon_charge.ingest import carbon_intensity as ci


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _payload(*starts):
    return {
        "data": [
            {"from": s, "to": s, "intensity": {"forecast": 100 + i, "actual": None, "index": "low"}}
            for i, s in enumerate(starts)
        ]
    }


def test_snapshot_csv_has_issue_time_and_only_future_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ci.http, "get",
        lambda *a, **k: _Resp(_payload("2000-01-01T00:00Z", "2999-01-01T00:00Z", "2999-01-01T00:30Z")),
    )
    path = ci.write_forward_forecast_csv(tmp_path)
    df = pd.read_csv(path)
    assert list(df.columns) == ci.FORECAST_LOG_COLUMNS
    assert len(df) == 2 and df["issued_at_utc"].nunique() == 1
    assert (pd.to_datetime(df["ts_utc"]) > pd.to_datetime(df["issued_at_utc"])).all()
    assert path.name.startswith("ci_forecast_") and path.suffix == ".csv"


def test_csv_round_trip_into_duckdb(tmp_path, monkeypatch):
    from carbon_charge import db

    monkeypatch.setattr(ci.http, "get", lambda *a, **k: _Resp(_payload("2999-01-01T00:00Z")))
    ci.write_forward_forecast_csv(tmp_path / "2999")
    with db.connect(tmp_path / "t.duckdb") as con:
        assert ci.import_forecast_csvs(con, tmp_path) == 1
        assert ci.import_forecast_csvs(con, tmp_path) == 1  # idempotent
        assert con.execute("SELECT count(*) FROM ci_forecast_log").fetchone()[0] == 1


# --- wind snapshot -------------------------------------------------------------------

from carbon_charge.ingest import wind  # noqa: E402


def test_live_wind_snapshot_uses_nesos_refresh_time_as_issue_time(tmp_path, monkeypatch):
    recs = [{"Datetime_GMT": "2999-01-01T00:00:00", "Date": "2999-01-01", "Settlement_period": 1,
             "Capacity": 25000, "Incentive_forecast": 9000},
            {"Datetime_GMT": "2999-01-01T00:30:00", "Date": "2999-01-01", "Settlement_period": 2,
             "Capacity": 25000, "Incentive_forecast": 9100}]

    def fake_get(url, params=None, **k):
        if url.endswith("resource_show"):
            return _Resp({"result": {"last_modified": "2026-10-01T08:40:08.786390"}})
        return _Resp({"result": {"records": recs}})

    monkeypatch.setattr(wind.http, "get", fake_get)
    path = wind.write_live_csv(tmp_path)
    df = pd.read_csv(path, parse_dates=["issued_at_utc"])
    assert path.name == "wind_forecast_20261001T0840Z.csv"
    assert (df["issued_at_utc"] == pd.Timestamp("2026-10-01 08:40:08.786390")).all()  # NESO's time, not ours
    assert list(df["wind_forecast_mw"]) == [9000, 9100]


def test_wind_history_transform_rejects_misaligned_timestamps_and_keeps_vintages():
    raw = pd.DataFrame({
        "Datetime_GMT": ["2024-03-31T00:00:00", "2024-03-31T00:30:00", "2024-03-31T00:00:00", "2024-03-31T01:00:00"],
        "Date": ["2024-03-31"] * 4, "Settlement_period": [1, 99, 1, 1],
        "Capacity": [20000] * 4, "Incentive_forecast": [9000, 9100, 9500, 9200],
        "Forecast_Timestamp": ["2024-03-30T08:49:00", "2024-03-30T08:49:00", "2024-03-30T12:00:00", "2024-03-30T08:49:00"],
    })
    clean, rejected = wind.transform(raw)
    # period 99 is impossible; the last row's Datetime_GMT disagrees with (Date, period 1).
    assert len(rejected) == 2
    # Two vintages for the same target period are both kept (different issue times).
    assert len(clean) == 2 and clean["ts_utc"].nunique() == 1 and clean["issued_at_utc"].nunique() == 2
