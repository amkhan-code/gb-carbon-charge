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
