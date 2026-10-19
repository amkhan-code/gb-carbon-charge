"""Entry point: `python -m carbon_charge_mcp --db data/carbon.duckdb` (stdio)."""

import argparse
import logging
import sys

from carbon_charge_mcp.server import create_server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Read-only MCP server over the gb-carbon-charge results (stdio).")
    ap.add_argument("--db", help="Path to the DuckDB file (default: $CARBON_CHARGE_DB or data/carbon.duckdb).")
    args = ap.parse_args(argv)
    # stdout carries the protocol, so every log line goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_server(args.db).run(transport="stdio")


if __name__ == "__main__":
    main()
