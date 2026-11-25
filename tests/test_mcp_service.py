import json
import unittest


class McpServiceTests(unittest.TestCase):
    def test_service_uses_injected_registry_for_listing_and_direct_tool_calls(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        registry = build_default_registry(
            enabled_modules={"market", "account", "trade"},
            credential_modes={"credential_ref"},
        )
        try:
            service = build_default_service(trade_enabled=False, registry=registry)
        except TypeError as exc:
            self.fail(f"service registry injection is missing: {exc}")

        listed = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "local-list", "method": "tools/list", "params": {}}
        )
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(len(names), 24)
        self.assertNotIn("broker_oauth_generate_authorization_url", names)
        self.assertFalse(any(name.startswith("trade_") for name in names))

        called = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "local-direct-oauth",
                "method": "tools/call",
                "params": {
                    "name": "broker_oauth_generate_authorization_url",
                    "arguments": {"client_id": "client", "redirect_uri": "https://merchant.example/callback"},
                },
            }
        )
        self.assertNotIn("result", called)
        self.assertEqual(called["error"]["code"], -32602)

    def test_trade_enabled_service_previews_with_plaintext_credential_without_user_context(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
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

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return json.dumps({"canTrade": True, "balances": []}).encode()

        market_adapter = MarketRulesAdapter()
        trade_adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=market_adapter,
            transport=transport,
        )
        response = build_default_service(
            trade_enabled=True,
            market_adapter=market_adapter,
            trade_adapter=trade_adapter,
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
        self.assertIn("preview_id", response["result"])
        self.assertNotIn("approval_token", response["result"])
        self.assertEqual(len(private_calls), 1)
        self.assertEqual(private_calls[0]["method"], "GET")
        self.assertEqual(private_calls[0]["url"], "https://api-spot.weex.com/api/v3/account")
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

    def test_tools_list_hides_trade_tools_when_module_is_disabled(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(trade_enabled=False)
        response = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(len(names), 26)
        self.assertIn("broker_oauth_generate_authorization_url", names)
        self.assertIn("broker_oauth_exchange_code_for_api_key", names)
        self.assertIn("market_get_symbol_price", names)
        self.assertIn("account_get_balance", names)
        self.assertFalse(any(name.startswith("trade_") for name in names))

    def test_tools_list_includes_trade_tools_when_module_is_enabled(self):
        from weex_mcp.service import build_default_service

        response = build_default_service(trade_enabled=True).handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(len(names), 52)
        self.assertEqual(sum(name.startswith("trade_") for name in names), 26)

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

    def test_t3_account_and_market_tools_dispatch_through_standard_envelopes(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        class PublicMarketAdapter:
            def __init__(self):
                self.calls = []

            def get_server_time(self, *, market):
                self.calls.append(("time", market))
                return {"market": market, "server_time": 1, "raw": {"serverTime": 1}}

            def ping(self, *, market):
                self.calls.append(("ping", market))
                return {"market": market, "reachable": True, "raw": {}}

            def get_symbol_price(self, *, market, symbol=None, symbols=None, price_type=None):
                self.calls.append(("price", market, symbol, symbols, price_type))
                prices = (
                    [{"symbol": item, "price": "1"} for item in symbols]
                    if symbols is not None
                    else [{"symbol": "BTCUSDT", "price": "1"}]
                )
                return {
                    "market": market,
                    "prices": prices,
                    "raw": [],
                }

            def get_exchange_info(self, *, market, symbol=None, symbols=None, symbol_status=None):
                self.calls.append(("exchange", market, symbol, symbols, symbol_status))
                return {
                    "market": market,
                    "symbols": [{"symbol": item, "status": symbol_status} for item in symbols],
                    "raw": {},
                }

        class AccountAdapter:
            def __init__(self):
                self.calls = []

            def get_position(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "market": kwargs["market"],
                    "trading_mode": kwargs["trading_mode"],
                    "symbol": kwargs["symbol"],
                    "positions": [],
                    "raw": [],
                }

        market_adapter = PublicMarketAdapter()
        account_adapter = AccountAdapter()
        service = build_default_service(
            trade_enabled=False,
            market_adapter=market_adapter,
            account_adapter=account_adapter,
        )
        context = TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")
        calls = (
            ("market_get_server_time", {"market": "futures"}),
            ("market_ping", {"market": "spot"}),
            ("market_get_symbol_price", {"market": "spot"}),
            ("market_get_symbol_price", {"market": "spot", "symbols": ["btc/usdt", "ETHUSDT"]}),
            (
                "market_get_exchange_info",
                {"market": "spot", "symbols": ["btc/usdt", "ETHUSDT"], "symbol_status": "TRADING"},
            ),
            (
                "account_get_position",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "credential": {"type": "credential_ref", "key_id": "key", "ref": "ref"},
                },
            ),
        )
        for index, (name, arguments) in enumerate(calls):
            with self.subTest(name=name):
                response = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": f"t3-{index}",
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                    context=context,
                )
                self.assertTrue(response["result"]["structuredContent"]["ok"])
                self.assertFalse(response["result"]["isError"])

        self.assertEqual(
            market_adapter.calls,
            [
                ("time", "futures"),
                ("ping", "spot"),
                ("price", "spot", None, None, None),
                ("price", "spot", None, ["BTCUSDT", "ETHUSDT"], None),
                ("exchange", "spot", None, ["BTCUSDT", "ETHUSDT"], "TRADING"),
            ],
        )
        self.assertEqual(account_adapter.calls[0]["symbol"], "BTCUSDT")
        self.assertEqual(account_adapter.calls[0]["credential_argument"]["ref"], "ref")

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
