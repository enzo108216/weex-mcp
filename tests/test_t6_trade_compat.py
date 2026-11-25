from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError


class T6TradeCompatibilityTests(unittest.TestCase):
    def test_settings_wire_request_locale_to_trade_adapter(self) -> None:
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        service = build_default_service(
            settings=Settings(
                trade_enabled=True,
                approval_token_secret="a" * 32,
                request_locale="zh-CN",
            )
        )

        self.assertEqual(service.trade_adapter.locale, "zh-CN")

    def test_trade_adapter_uses_effective_locale_for_signed_precheck(self) -> None:
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider

        prepared_requests: list[dict[str, object]] = []
        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            locale="zh-CN",
            transport=lambda prepared, _timeout: (
                prepared_requests.append(prepared) or b'{"canTrade":true}'
            ),
            time_func=lambda: 1.0,
        )

        payload = adapter._request(
            market="spot",
            method="GET",
            path="/api/v3/account",
            query={},
            body={},
            credential=SimpleNamespace(
                api_key="AK_TEST",
                api_secret="SK_TEST",
                api_passphrase="PP_TEST",
            ),
        )

        self.assertEqual(payload, {"canTrade": True})
        self.assertEqual(prepared_requests[0]["headers"]["locale"], "zh-CN")

    def test_default_trade_transport_uses_no_redirect_opener(self) -> None:
        from weex_mcp.adapters import trade_rest

        class Response:
            def read(self) -> bytes:
                return b'{"code":"0","data":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        prepared = {
            "url": "https://api-spot.weex.com/api/v3/account",
            "method": "GET",
            "data": None,
            "headers": {"locale": "zh-CN", "ACCESS-KEY": "secret"},
        }
        with patch.object(
            trade_rest._NO_REDIRECT_OPENER,
            "open",
            return_value=Response(),
        ) as opener:
            result = trade_rest._default_transport(prepared, 3.0)

        self.assertEqual(result, b'{"code":"0","data":{}}')
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Locale"), "zh-CN")
        self.assertEqual(request.get_header("Access-key"), "secret")

    def test_mutation_redirect_remains_unknown_transport_evidence(self) -> None:
        from weex_mcp.adapters import trade_rest

        prepared = {
            "url": "https://api-spot.weex.com/api/v3/order",
            "method": "POST",
            "data": b"{}",
            "headers": {"locale": "en-US", "ACCESS-KEY": "secret"},
            "_mutation_action": "order",
        }
        redirect = HTTPError(
            prepared["url"],
            302,
            "Found",
            {},
            io.BytesIO(b'{"code":"redirected","msg":"blocked"}'),
        )
        with patch.object(
            trade_rest._NO_REDIRECT_OPENER,
            "open",
            side_effect=redirect,
        ):
            with self.assertRaises(HTTPError) as raised:
                trade_rest._default_transport(prepared, 3.0)

        self.assertEqual(raised.exception.code, 302)


if __name__ == "__main__":
    unittest.main()
