import json
import unittest


class FakeSecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id=str(credential_argument["key_id"]),
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset([required_permission]),
            fingerprint="fp_test",
        )


class FakeMarketRulesAdapter:
    def get_exchange_info(self, *, market, symbol=None):
        return {
            "market": market,
            "symbols": [
                {
                    "symbol": symbol or "BTCUSDT",
                    "status": "TRADING",
                    "enableTrade": True,
                    "stepSize": "0.001",
                    "tickSize": "0.1",
                    "minTradeAmount": "0.001",
                }
            ],
        }


class CompletionGapTests(unittest.TestCase):
    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a", request_id="request_a")

    def _trade_credential(self):
        return {
            "type": "credential_ref",
            "ref": "credref_trade",
            "key_id": "key_trade",
            "permissions": ["TRADE"],
        }

    def test_default_trade_preview_with_credential_ref_requires_provider_and_creates_no_preview(self):
        from weex_mcp.service import build_default_service

        response = build_default_service(trade_enabled=True).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "default_preview",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "position_side": "LONG",
                        "order_type": "MARKET",
                        "quantity": "0.001",
                        "client_order_id": "default-preview-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "secret_provider_unconfigured")
        self.assertNotIn("preview_id", response["result"])

    def test_trade_preview_resolves_provider_with_read_only_access_probe(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.service import build_default_service

        network_calls = []
        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=lambda prepared, timeout: (
                network_calls.append(prepared)
                or json.dumps({"canTrade": True, "balances": []}).encode()
            ),
        )
        response = build_default_service(trade_enabled=True, trade_adapter=adapter).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "provider_preview",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "provider-preview-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(len(network_calls), 1)
        self.assertEqual(network_calls[0]["method"], "GET")
        self.assertEqual(network_calls[0]["url"], "https://api-spot.weex.com/api/v3/account")

    def test_trade_preview_fails_when_provider_is_unconfigured(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.service import build_default_service

        adapter = WeexTradeRestAdapter(secret_provider=UnconfiguredSecretProvider())
        response = build_default_service(trade_enabled=True, trade_adapter=adapter).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "provider_missing",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "provider-missing-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "secret_provider_unconfigured")
        self.assertNotIn("preview_id", response["result"])

    def test_trade_preview_rejects_non_positive_quantity(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.service import build_default_service

        adapter = WeexTradeRestAdapter(secret_provider=FakeSecretProvider())
        response = build_default_service(trade_enabled=True, trade_adapter=adapter).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "quantity_invalid",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "0",
                        "client_order_id": "invalid-quantity-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "invalid_arguments")

    def test_market_adapters_reject_malformed_success_payloads(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(transport=lambda url, timeout: b'{"code":"0","data":{}}')
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_depth(market="spot", symbol="BTCUSDT")
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_klines(market="spot", symbol="BTCUSDT", interval="1m")
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_exchange_info(market="spot")

    def test_account_adapters_reject_malformed_success_payloads(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: b'{"code":"0","data":{}}',
        )
        credential = {"type": "credential_ref", "ref": "ref", "key_id": "key"}
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_balance(market="spot", trading_mode="live", credential_argument=credential, context=self._context())
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_positions(
                market="futures", trading_mode="live", credential_argument=credential, context=self._context()
            )

    def test_trade_rejection_is_an_outer_error_and_is_not_cached(self):
        from weex_mcp.service import build_default_service

        class RejectingAdapter:
            def resolve_credential(self, *, credential_argument, context):
                from weex_mcp.secret_provider import ResolvedWeexCredential

                return ResolvedWeexCredential(
                    key_id="rejecting-key",
                    api_key="AK_FAKE",
                    api_secret="SK_FAKE",
                    api_passphrase="PP_FAKE",
                    permissions=frozenset(["TRADE"]),
                    fingerprint="rejecting-fp",
                )

            def preview_order(self, *, arguments, credential, credential_argument, context):
                return {
                    "risk_signature": "rejecting",
                    "summary": {
                        "market": "spot",
                        "trading_mode": "live",
                        "action": "order",
                        "endpoint": "/api/v3/order",
                        "request_body": {
                            "symbol": "BTCUSDT",
                            "side": "BUY",
                            "type": "MARKET",
                            "quantity": "1",
                            "newClientOrderId": arguments["client_order_id"],
                        },
                    },
                    "risk": {"status": "review_required", "alerts": []},
                }

            def confirm_order(self, **kwargs):
                return {"success": False, "status": "rejected_by_exchange"}

        service = build_default_service(trade_enabled=True, trade_adapter=RejectingAdapter())
        preview = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "reject_preview",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "reject-order-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )
        result = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "reject_confirm",
                "method": "tools/call",
                "params": {
                    "name": "trade_confirm_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "reject-order-1",
                        "preview_id": preview["result"]["preview_id"],
                        "confirmation": {"accepted": True},
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(result["result"]["ok"])
        self.assertEqual(result["result"]["error"]["code"], "upstream_error")

    def test_audit_sink_records_safe_account_and_trade_events(self):
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.service import build_default_service

        class AccountAdapter:
            def get_balance(self, **kwargs):
                return {"balances": []}

        sink = MemoryAuditSink()
        service = build_default_service(account_adapter=AccountAdapter(), audit_sink=sink)
        service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "audit_account",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "credential_ref",
                            "ref": "TOP_SECRET_REF",
                            "key_id": "key_account",
                            "permissions": ["USER_DATA"],
                        },
                    },
                },
            },
            context=self._context(),
        )

        self.assertEqual(len(sink.events), 1)
        self.assertEqual(sink.events[0]["tool_name"], "account_get_balance")
        self.assertTrue(sink.events[0]["success"])
        self.assertNotIn("TOP_SECRET_REF", json.dumps(sink.events))

        service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "audit_trade",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "audit-preview-1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )
        self.assertEqual(len(sink.events), 2)
        self.assertEqual(sink.events[1]["tool_name"], "trade_preview_order")
        self.assertFalse(sink.events[1]["success"])

    def test_default_service_uses_configured_file_audit_sink(self):
        import json
        import tempfile
        from pathlib import Path

        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings
        from weex_mcp.tenant_context import TenantContext

        class AccountAdapter:
            def get_balance(self, **kwargs):
                return {"balances": []}

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "weex_mcp_audit.log"
            service = build_default_service(
                settings=Settings(audit_log_file=str(audit_path)),
                account_adapter=AccountAdapter(),
            )
            response = service.handle_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": "file-audit-account",
                    "method": "tools/call",
                    "params": {
                        "name": "account_get_balance",
                        "arguments": {
                            "market": "spot",
                            "trading_mode": "live",
                            "credential": {
                                "type": "plaintext",
                                "api_key": "AK_FILE_SERVICE",
                                "api_secret": "SK_FILE_SERVICE",
                                "api_passphrase": "PP_FILE_SERVICE",
                            },
                        },
                    },
                },
                context=TenantContext(tenant_id="tenant", user_id="user", session_id="session"),
            )
            lines = audit_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["tool_name"], "account_get_balance")
        self.assertTrue(event["success"])
        serialized = json.dumps(event, sort_keys=True)
        for forbidden in ("AK_FILE_SERVICE", "SK_FILE_SERVICE", "PP_FILE_SERVICE"):
            self.assertNotIn(forbidden, serialized)

    def test_non_object_params_return_invalid_params(self):
        from weex_mcp.service import build_default_service

        for params in (["unexpected"], "unexpected"):
            response = build_default_service().handle_jsonrpc(
                {"jsonrpc": "2.0", "id": "bad_params", "method": "tools/list", "params": params},
                context=self._context(),
            )
            self.assertEqual(response["error"]["code"], -32602)

    def test_initialized_and_unknown_notifications_return_no_response(self):
        from weex_mcp.service import build_default_service

        service = build_default_service()
        self.assertIsNone(service.handle_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        self.assertIsNone(service.handle_jsonrpc({"jsonrpc": "2.0", "method": "unknown/notification", "params": {}}))

    def test_http_notification_returns_empty_202(self):
        from weex_mcp.http_app import handle_mcp_post

        response = handle_mcp_post(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}).encode(),
            context=self._context(),
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(response.body, "")


if __name__ == "__main__":
    unittest.main()
