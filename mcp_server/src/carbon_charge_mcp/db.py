"""The only place a DuckDB connection is opened, and it is always read-only."""

from pathlib import Path

import duckdb

from carbon_charge_mcp import queries
from carbon_charge_mcp.errors import failure


class ResultsDb:
    """A read-only handle on the results database.

    The connection is opened with ``read_only=True``, so a write fails inside the engine regardless of
    what any tool does. Tools call :meth:`rows` with one of the fixed statements in ``queries``.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._con: duckdb.DuckDBPyConnection | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            if not self.path.exists():
                raise failure("NOT_AVAILABLE", "The results database was not found.",
                              hint="Build it with `carbon-charge ingest all`, `carbon-charge backtest` and "
                                   "`carbon-charge report`, or start the server with --db PATH.")
            self._con = duckdb.connect(str(self.path), read_only=True)
        return self._con

    def rows(self, sql: str, *params) -> list[dict]:
        cur = self._connect().cursor()  # a cursor per call: connections are not shared across threads
        try:
            res = cur.execute(sql, list(params))
            cols = [d[0] for d in res.description]
            return [dict(zip(cols, r, strict=True)) for r in res.fetchall()]
        finally:
            cur.close()

    def require_tables(self) -> None:
        """Fail helpfully if the pipeline has not produced the tables the server reads."""
        have = {r["table_name"] for r in self.rows(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'")}
        missing = [t for t in queries.REQUIRED_TABLES if t not in have]
        if missing:
            raise failure("NOT_AVAILABLE", f"The results database is missing tables: {', '.join(missing)}.",
                          hint="Run `carbon-charge report` (it also writes the results tables), then retry.")

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
