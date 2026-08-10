import json
import unittest


class McpServiceTests(unittest.TestCase):
    def test_trade_enabled_service_previews_with_plaintext_credential_without_user_context(self):
        from weex_mcp.service import build_default_service

        class MarketRulesAdapter:
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

        response = build_default_service(
            trade_enabled=True,
            market_adapter=MarketRulesAdapter(),
        ).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "plain-trade-preview",
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
                        "client_order_id": "stateless-preview-1",
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK_INLINE",
                            "api_secret": "SK_INLINE",
                            "api_passphrase": "PP_INLINE",
                        },
                    },
                },
            }
        )

        self.assertTrue(response["result"]["ok"])
        self.assertIn("approval_token", response["result"])
        self.assertNotIn("preview_id", response["result"])
        serialized = json.dumps(response, sort_keys=True)
        self.assertNotIn("AK_INLINE", serialized)
        self.assertNotIn("SK_INLINE", serialized)
        self.assertNotIn("PP_INLINE", serialized)

    def test_account_tool_accepts_plaintext_credential_without_user_context(self):
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.service import build_default_service

        class AccountAdapter:
            def get_balance(self, **kwargs):
                self.credential = kwargs["credential_argument"]
                return {"balances": []}

        adapter = AccountAdapter()
        sink = MemoryAuditSink()
        response = build_default_service(account_adapter=adapter, audit_sink=sink).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "plain-account",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK_INLINE",
                            "api_secret": "SK_INLINE",
                            "api_passphrase": "PP_INLINE",
                        },
                    },
                },
            }
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(adapter.credential["api_secret"], "SK_INLINE")
        serialized = json.dumps(response, sort_keys=True)
        self.assertNotIn("AK_INLINE", serialized)
        self.assertNotIn("SK_INLINE", serialized)
        self.assertNotIn("PP_INLINE", serialized)
        self.assertNotIn("SK_INLINE", json.dumps(sink.events, sort_keys=True))

    def test_tools_list_is_stable_without_user_credentials(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(trade_enabled=False)
        response = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("market_get_symbol_price", names)
        self.assertIn("account_get_balance", names)
        self.assertIn("trade_preview_order", names)
        self.assertIn("trade_confirm_order", names)

    def test_market_tool_can_run_without_credential(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        class PublicMarketAdapter:
            def get_symbol_price(self, *, market, symbol, price_type=None):
                return {
                    "market": market,
                    "symbol": symbol,
                    "price": "100.0",
                    "source": "test_public_market",
                }

        service = build_default_service(
            trade_enabled=False,
            market_adapter=PublicMarketAdapter(),
        )
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(response["result"]["data"]["symbol"], "BTCUSDT")

    def test_account_tool_fails_closed_without_credential(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(trade_enabled=False)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {"market": "futures", "trading_mode": "live"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "credential_missing")

    def test_trade_tool_fails_closed_when_module_disabled(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(trade_enabled=False)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "0.001",
                        "credential": {
                            "type": "credential_ref",
                            "ref": "credref_trade",
                            "key_id": "weex_key_trade",
                            "permissions": ["TRADE"],
                        },
                    },
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "module_disabled")

    def test_account_without_user_context_still_validates_arguments(self):
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account-gate-order",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {"unexpected": True},
                },
            }
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "invalid_arguments")

    def test_trade_module_gate_runs_before_argument_schema_validation(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        response = build_default_service(trade_enabled=False).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "trade-gate-order",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {"unexpected": True},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "module_disabled")

    def test_trade_module_gate_precedes_missing_tenant_context(self):
        from weex_mcp.service import build_default_service

        response = build_default_service(trade_enabled=False).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "trade-gate-no-context",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {"unexpected": True},
                },
            }
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "module_disabled")

    def test_initialize_is_rejected_with_injected_protocol_version_setting(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        service = build_default_service(settings=Settings(protocol_version="2099-01-01"))
        response = service.handle_jsonrpc({"jsonrpc": "2.0", "id": "init_1", "method": "initialize", "params": {}})

        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("2099-01-01", response["error"]["message"])

    def test_tools_call_rejects_non_object_arguments_without_coercing_to_empty_object(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        response = build_default_service().handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "bad-arguments",
                "method": "tools/call",
                "params": {"name": "market_get_symbol_price", "arguments": []},
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
