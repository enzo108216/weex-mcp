from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError


class T6TradeCompatibilityTests(unittest.TestCase):
    def test_futures_batch_schema_accepts_attached_tp_sl_but_spot_rejects_it(self) -> None:
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        item = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "position_side": "LONG",
            "order_type": "LIMIT",
            "quantity": "0.001",
            "price": "100",
            "time_in_force": "GTC",
            "client_order_id": "batch-1",
            "tp_trigger_price": "120",
            "sl_trigger_price": "80",
            "tp_working_type": "MARK_PRICE",
            "sl_working_type": "CONTRACT_PRICE",
        }
        preview = {
            "market": "futures",
            "trading_mode": "live",
            "orders": [item],
            "credential": credential,
        }
        validate_tool_arguments(
            preview,
            tools["trade_preview_batch_order"]["inputSchema"],
        )
        validate_tool_arguments(
            preview
            | {
                "preview_id": "preview",
                "confirmation": {"accepted": True},
            },
            tools["trade_confirm_batch_order"]["inputSchema"],
        )

        spot_item = dict(item)
        spot_item.pop("position_side")
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {
                    "market": "spot",
                    "trading_mode": "live",
                    "orders": [spot_item],
                    "credential": credential,
                },
                tools["trade_preview_batch_order"]["inputSchema"],
            )

    def test_futures_batch_preview_and_confirm_preserve_attached_tp_sl_body(self) -> None:
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.tenant_context import TenantContext
        from weex_mcp.trade_execution import TradeExecution

        class RulesAdapter:
            def get_exchange_info(self, *, market, symbol=None):
                return {
                    "market": market,
                    "symbols": [
                        {
                            "symbol": symbol,
                            "minTradeAmount": "0.001",
                            "maxTradeAmount": "100",
                            "stepSize": "0.001",
                            "tickSize": "0.1",
                            "pricePrecision": 1,
                            "quantityPrecision": 3,
                        }
                    ],
                }

        calls: list[dict[str, object]] = []

        def transport(prepared, _timeout):
            calls.append(prepared)
            return b'[{"clientOrderId":"batch-1","orderId":"1","success":true}]'

        provider = RequestSecretProvider()
        credential_argument = {
            "type": "plaintext",
            "api_key": "AK_TEST",
            "api_secret": "SK_TEST",
            "api_passphrase": "PP_TEST",
        }
        context = TenantContext(
            tenant_id="tenant",
            user_id="user",
            session_id="session",
        )
        resolved = provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="TRADE",
        )
        adapter = WeexTradeRestAdapter(
            secret_provider=provider,
            market_adapter=RulesAdapter(),
            transport=transport,
            time_func=lambda: 1.0,
        )
        arguments = {
            "market": "futures",
            "trading_mode": "live",
            "orders": [
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "LIMIT",
                    "quantity": "0.001",
                    "price": "100",
                    "time_in_force": "GTC",
                    "client_order_id": "batch-1",
                    "tp_trigger_price": "120",
                    "sl_trigger_price": "80",
                    "tp_working_type": "MARK_PRICE",
                    "sl_working_type": "CONTRACT_PRICE",
                }
            ],
        }
        preview = adapter.preview_mutation(
            action="batch_order",
            arguments=arguments,
            credential=resolved,
            credential_argument=credential_argument,
            context=context,
        )
        execution = TradeExecution(
            action="batch_order",
            market="futures",
            trading_mode="live",
            summary=preview["summary"],
            risk=preview["risk"],
            risk_signature="signature",
        )

        result = adapter.confirm_mutation(
            execution=execution,
            credential=resolved,
            credential_argument=credential_argument,
            context=context,
        )

        body = calls[0]["body"]["batchOrders"][0]
        self.assertEqual(body["tpTriggerPrice"], "120")
        self.assertEqual(body["slTriggerPrice"], "80")
        self.assertEqual(body["TpWorkingType"], "MARK_PRICE")
        self.assertEqual(body["SlWorkingType"], "CONTRACT_PRICE")
        self.assertTrue(result["success"])

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
