"""Command line interface."""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import typer

from carbon_charge import config, db
from carbon_charge import figures as figures_mod
from carbon_charge import report as report_mod
from carbon_charge.evaluation import backtest as backtest_mod
from carbon_charge.ingest import carbon_intensity, demand_forecast, prices, weather
from carbon_charge.timeutils import UTC

app = typer.Typer(no_args_is_help=True, add_completion=False)
ingest_app = typer.Typer(no_args_is_help=True, help="Pull source data into DuckDB.")
app.add_typer(ingest_app, name="ingest")


def _parse_date(value: str | None, default: date) -> date:
    return date.fromisoformat(value) if value else default


@ingest_app.command("carbon")
def ingest_carbon(
    start: str = typer.Option(None, help="First UTC date, YYYY-MM-DD."),
    end: str = typer.Option(None, help="Last UTC date, YYYY-MM-DD (default: now)."),
) -> None:
    """Carbon intensity actuals (and the API's non-day-ahead forecast field)."""
    a = datetime.combine(_parse_date(start, config.HISTORY_START), time(0), tzinfo=UTC)
    now = datetime.now(UTC)
    b = min(datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(0), tzinfo=UTC), now) if end else now
    with db.connect() as con:
        typer.echo(f"ci_history: {carbon_intensity.ingest_history(con, a, b)} rows")


@ingest_app.command("demand")
def ingest_demand() -> None:
    """NESO day-ahead half-hourly demand forecast."""
    with db.connect() as con:
        n, rejected = demand_forecast.ingest(con)
    typer.echo(f"demand_forecast: {n} rows, {len(rejected)} rejected")
    if len(rejected):
        typer.echo(rejected.to_string())


@ingest_app.command("weather")
def ingest_weather(
    start: str = typer.Option(None, help="First target date, YYYY-MM-DD."),
    end: str = typer.Option(None, help="Last target date, YYYY-MM-DD (default: today)."),
) -> None:
    """Open-Meteo archived weather forecasts."""
    a = _parse_date(start, config.WEATHER_HISTORY_START)
    b = _parse_date(end, datetime.now(UTC).date())
    with db.connect() as con:
        typer.echo(f"weather_forecast: {weather.ingest(con, a, b)} rows")


@ingest_app.command("prices")
def ingest_prices(
    start: str = typer.Option(None, help="First UTC date for MID, YYYY-MM-DD."),
    end: str = typer.Option(None, help="Last UTC date for MID (default: today)."),
) -> None:
    """N2EX day-ahead prices (hourly) and Elexon Market Index prices (half-hourly)."""
    a = _parse_date(start, config.HISTORY_START)
    b = _parse_date(end, datetime.now(UTC).date())
    with db.connect() as con:
        typer.echo(f"price_day_ahead: {prices.ingest_day_ahead(con)} rows")
        typer.echo(f"price_mid: {prices.ingest_mid(con, a, b)} rows")


@ingest_app.command("all")
def ingest_all() -> None:
    """Run every ingester over the full default history."""
    ingest_carbon(start=None, end=None)
    ingest_demand()
    ingest_weather(start=None, end=None)
    ingest_prices(start=None, end=None)


@app.command("log-forecast")
def log_forecast(
    out_dir: Path = typer.Option(None, help="Write a snapshot CSV here instead of touching DuckDB."),
) -> None:
    """Snapshot NESO's forward carbon intensity forecast. Run daily before the cutoff."""
    if out_dir:
        typer.echo(f"wrote {carbon_intensity.write_forward_forecast_csv(out_dir)}")
        return
    with db.connect() as con:
        typer.echo(f"ci_forecast_log: {carbon_intensity.log_forward_forecast(con)} rows")


@app.command("import-forecasts")
def import_forecasts(csv_dir: Path = typer.Argument(..., help="Directory of snapshot CSVs.")) -> None:
    """Load logger snapshot CSVs (a checkout of the data branch) into DuckDB."""
    with db.connect() as con:
        typer.echo(f"ci_forecast_log: {carbon_intensity.import_forecast_csvs(con, csv_dir)} rows")


@app.command()
def backtest(
    start: str = typer.Option("2025-01-01", help="First target day of the test period."),
    end: str = typer.Option(None, help="Last target day (default: last complete day with actuals)."),
    refit_days: int = typer.Option(14, help="Refit the model every N days."),
) -> None:
    """Rolling-origin backtest; stores out-of-sample forecasts in DuckDB (backtest_forecast)."""
    # Read-only while computing (minutes); the database is opened for writing only to save.
    with db.connect(read_only=True) as con:
        last = con.execute(
            "SELECT max(settlement_date) FROM ci_history GROUP BY settlement_date "
            "HAVING count(*) >= 46 ORDER BY 1 DESC LIMIT 1"
        ).fetchone()[0]
        end_d = date.fromisoformat(end) if end else last
        ci, price, folds = backtest_mod.run(con, date.fromisoformat(start), end_d, refit_days)
    with db.connect() as con:
        typer.echo(f"{len(folds)} folds, {len(ci)} carbon + {len(price)} price forecasts, "
                   f"{backtest_mod.save(con, ci, price)} saved")
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    folds.to_csv(config.REPORTS_DIR / "backtest_folds.csv", index=False)


@app.command()
def evaluate() -> None:
    """Error metrics (by season / time of day) and slot-ranking accuracy from stored backtest forecasts."""
    with db.connect(read_only=True) as con:
        tables = report_mod.evaluate(con)
    report_mod.write_csvs(tables)
    typer.echo(tables["overall"].to_string(index=False))


@app.command()
def optimise() -> None:
    """Simulate the four charging strategies over the backtest period, incl. the sensitivity set."""
    with db.connect(read_only=True) as con:
        tables = report_mod.optimise(con)
    report_mod.write_csvs(tables)
    s = tables["optimiser_summary"]
    typer.echo(s[s["scenario"] == "default"].round(2).to_string(index=False))


@app.command()
def report() -> None:
    """Run evaluate + optimise and write reports/RESULTS.md."""
    evaluate()
    optimise()
    with db.connect(read_only=True) as con:
        typer.echo(f"figures: {', '.join(figures_mod.make_all(con))}")
    path = config.REPORTS_DIR / "RESULTS.md"
    path.write_text(report_mod.render())
    typer.echo(f"wrote {path}")


@app.command()
def status() -> None:
    """Row counts and time coverage per table."""
    with db.connect(read_only=True) as con:
        for table in ("ci_history", "ci_forecast_log", "demand_forecast", "weather_forecast", "price_day_ahead", "price_mid"):
            n, lo, hi = con.execute(f"SELECT COUNT(*), MIN(ts_utc), MAX(ts_utc) FROM {table}").fetchone()
            typer.echo(f"{table:18} {n:>9} rows  {lo} -> {hi}")


if __name__ == "__main__":
    app()
