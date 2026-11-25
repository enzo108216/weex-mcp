from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


class _SecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id=str(credential_argument["key_id"]),
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset({required_permission}),
            fingerprint="fp_test",
        )


class T5ReadCompatibilityTests(unittest.TestCase):
    def test_settings_wire_request_locale_to_market_and_account_adapters(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        settings = Settings(request_locale="zh-CN")
        service = build_default_service(settings=settings)

        self.assertEqual(settings.request_locale, "zh-CN")
        self.assertEqual(service.market_adapter.locale, "zh-CN")
        self.assertEqual(service.account_adapter.locale, "zh-CN")

        self.assertEqual(
            Settings.from_environment({"WEEX_LOCALE": ""}).request_locale,
            "en-US",
        )

    def test_default_transports_send_locale_and_return_http_status_without_redirects(self):
        from weex_mcp.adapters import account, market

        class Response:
            status = 206

            def read(self):
                return b'{"data":[]}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        response = Response()
        with patch.object(market._NO_REDIRECT_OPENER, "open", return_value=response) as market_open:
            market_result = market._default_transport(
                "https://api-spot.weex.com/api/v3/coins",
                2.5,
                locale="zh-CN",
            )
        market_request = market_open.call_args.args[0]
        self.assertEqual(market_request.get_header("Locale"), "zh-CN")
        self.assertEqual(market_result.status, 206)
        self.assertEqual(market_result.body, b'{"data":[]}')

        prepared = {
            "url": "https://api-spot.weex.com/api/v3/account",
            "method": "GET",
            "data": None,
            "headers": {"locale": "zh-CN", "ACCESS-KEY": "secret"},
        }
        with patch.object(account._NO_REDIRECT_OPENER, "open", return_value=response) as account_open:
            account_result = account._default_transport(prepared, 3.0)
        account_request = account_open.call_args.args[0]
        self.assertEqual(account_request.get_header("Locale"), "zh-CN")
        self.assertEqual(account_result.status, 206)

    def test_default_transport_converts_http_redirect_to_safe_status_result(self):
        from weex_mcp.adapters import account

        http_error = HTTPError(
            "https://api-spot.weex.com/api/v3/account",
            302,
            "Found",
            {},
            io.BytesIO(b'{"code":"redirected","msg":"blocked"}'),
        )
        prepared = {
            "url": "https://api-spot.weex.com/api/v3/account",
            "method": "GET",
            "data": None,
            "headers": {"locale": "en-US", "ACCESS-KEY": "secret"},
        }
        with patch.object(account._NO_REDIRECT_OPENER, "open", side_effect=http_error):
            result = account._default_transport(prepared, 3.0)

        self.assertEqual(result.status, 302)
        self.assertEqual(json.loads(result.body), {"code": "redirected", "msg": "blocked"})

    def test_account_result_and_error_preserve_safe_transport_metadata(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexHttpResponse, WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        context = TenantContext(tenant_id="tenant", user_id="user", session_id="session")
        credential = {"type": "credential_ref", "ref": "ref", "key_id": "key"}
        success = WeexAccountRestAdapter(
            secret_provider=_SecretProvider(),
            transport=lambda _prepared, _timeout: WeexHttpResponse(
                status=200,
                body=b'[{"asset":"USDT","balance":"1"}]',
            ),
        ).get_balance(
            market="futures",
            trading_mode="live",
            credential_argument=credential,
            context=context,
        )
        self.assertEqual(success["http_status"], 200)
        self.assertEqual(success["raw"], [{"asset": "USDT", "balance": "1"}])

        adapter = WeexAccountRestAdapter(
            secret_provider=_SecretProvider(),
            transport=lambda _prepared, _timeout: WeexHttpResponse(
                status=429,
                body=b'{"code":"-1000","msg":"rate limited"}',
            ),
        )
        with self.assertRaises(WeexRestBusinessError) as raised:
            adapter.get_balance(
                market="futures",
                trading_mode="live",
                credential_argument=credential,
                context=context,
            )
        self.assertEqual(
            raised.exception.safe_details(),
            {
                "upstream_code": "-1000",
                "http_status": 429,
                "upstream_payload": {"code": "-1000", "msg": "rate limited"},
            },
        )

        from weex_mcp.tools.account import get_balance

        tool_result = get_balance(
            {
                "market": "futures",
                "trading_mode": "live",
                "credential": credential,
            },
            TenantContext(tenant_id="tenant", user_id="user", session_id="session"),
            adapter=adapter,
        )
        self.assertFalse(tool_result["ok"])
        self.assertEqual(tool_result["error"]["details"]["http_status"], 429)
        self.assertEqual(tool_result["error"]["details"]["upstream_payload"]["code"], "-1000")

    def test_success_status_with_non_json_body_is_a_typed_invalid_response(self):
        from weex_mcp.adapters.rest_errors import (
            WeexHttpResponse,
            WeexRestBusinessError,
            decode_json_transport,
        )

        with self.assertRaises(WeexRestBusinessError) as raised:
            decode_json_transport(WeexHttpResponse(status=200, body=b"not-json"))

        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertEqual(
            raised.exception.safe_details(),
            {
                "upstream_code": "invalid_response",
                "http_status": 200,
                "upstream_payload": {"raw": "not-json"},
            },
        )

    def test_spot_ticker_and_book_tools_support_multi_symbol_without_fanout(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        requested_urls: list[str] = []
        payloads = {
            "24hr": [
                {"symbol": "BTCUSDT", "lastPrice": "1"},
                {"symbol": "ETHUSDT", "lastPrice": "2"},
            ],
            "bookTicker": [
                {"symbol": "BTCUSDT", "bidPrice": "1", "bidQty": "1", "askPrice": "2", "askQty": "1"},
                {"symbol": "ETHUSDT", "bidPrice": "3", "bidQty": "1", "askPrice": "4", "askQty": "1"},
            ],
        }

        def transport(url: str, _timeout: float) -> bytes:
            requested_urls.append(url)
            key = "bookTicker" if "bookTicker" in url else "24hr"
            return json.dumps(payloads[key]).encode()

        adapter = WeexMarketRestAdapter(transport=transport, max_attempts=1)
        symbols = ["BTCUSDT", "ETHUSDT"]
        ticker = adapter.get_ticker_24h(market="spot", symbols=symbols)
        book = adapter.get_book_ticker(market="spot", symbols=symbols)

        self.assertEqual(len(requested_urls), 2)
        self.assertTrue(all("symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%5D" in url for url in requested_urls))
        self.assertEqual(ticker["symbols"], symbols)
        self.assertEqual(book["symbols"], symbols)
        with self.assertRaises(ValueError):
            adapter.get_ticker_24h(market="futures", symbols=symbols)

    def test_spot_multi_symbol_is_exposed_by_tool_schema_and_dispatch(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        class Adapter:
            def __init__(self):
                self.calls: list[dict[str, object]] = []

            def get_ticker_24h(self, **kwargs):
                self.calls.append(kwargs)
                return {"raw": []}

            def get_book_ticker(self, **kwargs):
                self.calls.append(kwargs)
                return {"raw": []}

        adapter = Adapter()
        service = build_default_service(settings=Settings(), market_adapter=adapter)
        tools = {
            item["name"]: item
            for item in service.handle_jsonrpc(
                {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}
            )["result"]["tools"]
        }
        for name in ("market_get_ticker_24h", "market_get_book_ticker"):
            schema = tools[name]["inputSchema"]
            self.assertIn("symbols", schema["properties"])
            response = service.handle_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": name,
                    "method": "tools/call",
                    "params": {
                        "name": name,
                        "arguments": {"market": "spot", "symbols": ["BTCUSDT", "ETHUSDT"]},
                    },
                }
            )
            self.assertTrue(response["result"]["structuredContent"]["ok"])

        self.assertEqual(
            adapter.calls,
            [
                {"market": "spot", "symbol": None, "symbols": ["BTCUSDT", "ETHUSDT"]},
                {"market": "spot", "symbol": None, "symbols": ["BTCUSDT", "ETHUSDT"]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
