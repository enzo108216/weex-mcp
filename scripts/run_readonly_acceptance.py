#!/usr/bin/env python3
"""Run request-scoped, read-only Account MCP acceptance without persisting credentials."""

from __future__ import annotations

import getpass
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def read_service_token() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("WEEX_SERVICE_AUTH_TOKEN="):
            return line.split("=", 1)[1]
    raise RuntimeError("WEEX_SERVICE_AUTH_TOKEN is missing")


def read_credentials() -> list[tuple[str, dict[str, str]]]:
    credentials = []
    for label in ("K1", "K2", "K3", "K4"):
        api_key = getpass.getpass(f"{label} api_key: ")
        api_secret = getpass.getpass(f"{label} api_secret: ")
        api_passphrase = getpass.getpass(f"{label} api_passphrase: ")
        credentials.append(
            (
                label,
                {
                    "type": "plaintext",
                    "api_key": api_key,
                    "api_secret": api_secret,
                    "api_passphrase": api_passphrase,
                },
            )
        )
    return credentials


def call_tool(token: str, name: str, arguments: dict[str, Any], request_id: int) -> dict[str, Any]:
    mcp_url = os.environ.get("WEEX_MCP_URL", "http://127.0.0.1:8080/mcp")
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    request = Request(
        mcp_url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2025-06-18",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)["result"]


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    summary: dict[str, Any] = {
        "ok": result.get("ok") is True,
        "error_code": error.get("code"),
        "error_message": error.get("message"),
        "source": data.get("source"),
    }
    for key, value in data.items():
        if isinstance(value, list):
            summary["data_field"] = key
            summary["items"] = len(value)
            break
        if isinstance(value, dict) and key not in {"raw"}:
            summary["data_field"] = key
            summary["data_type"] = "object"
            break
    return summary


def main() -> None:
    now = int(time.time() * 1000)
    start = now - 24 * 60 * 60 * 1000
    cases = [
        ("balance_spot", "account_get_balance", {"market": "spot", "trading_mode": "live"}),
        ("balance_futures", "account_get_balance", {"market": "futures", "trading_mode": "live"}),
        ("balance_futures_demo", "account_get_balance", {"market": "futures", "trading_mode": "demo"}),
        ("positions_futures", "account_get_positions", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}),
        ("positions_futures_demo", "account_get_positions", {"market": "futures", "trading_mode": "demo", "symbol": "BTCUSDT"}),
        ("orders_spot_open", "account_get_orders", {"market": "spot", "trading_mode": "live", "status": "open", "symbol": "BTCUSDT"}),
        ("orders_spot_history", "account_get_orders", {"market": "spot", "trading_mode": "live", "status": "history", "symbol": "BTCUSDT", "limit": "10", "page": "1"}),
        ("orders_futures_open", "account_get_orders", {"market": "futures", "trading_mode": "live", "status": "open", "symbol": "BTCUSDT"}),
        ("orders_futures_history", "account_get_orders", {"market": "futures", "trading_mode": "live", "status": "history", "symbol": "BTCUSDT", "start_time": str(start), "end_time": str(now), "limit": "10", "page": "0"}),
        ("orders_futures_demo_history", "account_get_orders", {"market": "futures", "trading_mode": "demo", "status": "history", "symbol": "BTCUSDT", "limit": "10", "page": "0"}),
        ("trades_spot", "account_get_trades", {"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "limit": "10"}),
        ("trades_futures", "account_get_trades", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "start_time": str(start), "end_time": str(now), "limit": "10"}),
        ("bills_spot_account", "account_get_bills", {"market": "spot", "trading_mode": "live", "bill_type": "account", "limit": "10"}),
        ("bills_spot_funding", "account_get_bills", {"market": "spot", "trading_mode": "live", "bill_type": "funding", "limit": "10", "page": "1"}),
        ("bills_spot_tax", "account_get_bills", {"market": "spot", "trading_mode": "live", "bill_type": "tax", "month": time.strftime("%Y-%m"), "limit": "10", "page": "1"}),
        ("bills_futures_income", "account_get_bills", {"market": "futures", "trading_mode": "live", "bill_type": "income", "start_time": str(start), "end_time": str(now), "limit": "10"}),
        ("config_spot", "account_get_config", {"market": "spot", "trading_mode": "live"}),
        ("config_futures", "account_get_config", {"market": "futures", "trading_mode": "live"}),
        ("commission_futures", "account_get_commission_rate", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}),
        ("symbol_config_futures", "account_get_symbol_config", {"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}),
        ("conditional_open_futures", "account_get_conditional_orders", {"market": "futures", "trading_mode": "live", "status": "open", "symbol": "BTCUSDT", "limit": "10", "page": "1"}),
        ("conditional_history_futures", "account_get_conditional_orders", {"market": "futures", "trading_mode": "live", "status": "history", "symbol": "BTCUSDT", "start_time": str(start), "end_time": str(now), "limit": "10"}),
        ("transfer_records_spot", "account_get_transfer_records", {"market": "spot", "trading_mode": "live", "limit": "10"}),
    ]
    token = read_service_token()
    credentials = read_credentials()
    output = []
    request_id = 1000
    for case, tool, arguments in cases:
        attempts = []
        for label, credential in credentials:
            request_id += 1
            try:
                result = call_tool(token, tool, arguments | {"credential": credential}, request_id)
                attempt = {"credential": label} | summarize(result)
            except Exception as exc:
                attempt = {"credential": label, "ok": False, "transport_error": f"{type(exc).__name__}: {exc}"}
            attempts.append(attempt)
            if attempt["ok"]:
                break
        output.append({"case": case, "tool": tool, "arguments": arguments, "attempts": attempts})
        print(json.dumps(output[-1], ensure_ascii=False), flush=True)
    Path("/tmp/wwld-15863-account.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"total": len(output), "passed": sum(any(a["ok"] for a in row["attempts"]) for row in output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
