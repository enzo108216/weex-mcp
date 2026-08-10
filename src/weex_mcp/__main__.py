from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .server import smoke_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WEEX Trader MCP Service")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument("--smoke", action="store_true", help="Run a local tools/list smoke request.")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="Run the cloud ASGI server (requires the server extra).")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("WEEX_MCP_PORT", "8080"),
        help="Bind port (default: WEEX_MCP_PORT or 8080).",
    )
    serve_parser.add_argument("--workers", type=int, default=1, help="Worker process count (default: 1).")
    subparsers.add_parser("stdio", help="Run a local MCP server over stdin/stdout.")
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0
    if args.smoke:
        print(json.dumps(smoke_request(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "serve":
        from .cloud import run_cloud_server

        try:
            run_cloud_server(host=args.host, port=args.port, workers=args.workers)
        except (RuntimeError, ValueError) as exc:
            print(f"weex-mcp serve: error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "stdio":
        from .stdio_server import run_stdio_server

        return run_stdio_server()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
