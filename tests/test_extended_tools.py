import unittest


class FakeMarketAdapter:
    def get_symbol_price(self, *, market, symbol, price_type=None):
        return {"market": market, "symbol": symbol, "price": "1"}

    def get_depth(self, *, market, symbol, limit=None):
        return {"market": market, "symbol": symbol, "bids": [], "asks": [], "limit": limit}

    def get_klines(self, *, market, symbol, interval, start_time=None, end_time=None, limit=None):
        return {"market": market, "symbol": symbol, "interval": interval, "klines": []}

    def get_exchange_info(self, *, market, symbol=None):
        return {"market": market, "symbol": symbol, "symbols": []}


class FakeAccountAdapter:
    def get_balance(self, *, market, trading_mode, credential_argument, context):
        return {"market": market, "trading_mode": trading_mode, "balances": []}

    def get_positions(self, *, market, trading_mode, credential_argument, context, symbol=None):
        return {"market": market, "trading_mode": trading_mode, "positions": [], "symbol": symbol}

    def get_orders(self, *, market, trading_mode, credential_argument, context, status, **filters):
        return {"market": market, "trading_mode": trading_mode, "orders": [], "status": status, "filters": filters}

    def get_trades(self, *, market, trading_mode, credential_argument, context, **filters):
        return {"market": market, "trading_mode": trading_mode, "trades": [], "filters": filters}

    def get_bills(self, *, market, trading_mode, credential_argument, context, **filters):
        return {"market": market, "trading_mode": trading_mode, "bills": [], "filters": filters}


class ExtendedToolTests(unittest.TestCase):
    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")

    def _user_data_credential(self):
        return {
            "type": "credential_ref",
            "ref": "credref_user_data",
            "key_id": "key_user_data",
            "permissions": ["USER_DATA"],
        }

    def _trade_credential(self):
        return {
            "type": "credential_ref",
            "ref": "credref_trade",
            "key_id": "key_trade",
            "permissions": ["TRADE"],
        }

    def test_tools_list_contains_local_market_account_trade_surface(self):
        from weex_mcp.tool_registry import build_default_registry

        names = {tool["name"] for tool in build_default_registry().list_tools()}

        expected = {
            "market_get_symbol_price",
            "market_get_depth",
            "market_get_klines",
            "market_get_exchange_info",
            "account_get_balance",
            "account_get_positions",
            "account_get_orders",
            "account_get_trades",
            "account_get_bills",
            "trade_preview_order",
            "trade_confirm_order",
            "trade_preview_cancel_order",
            "trade_confirm_cancel_order",
            "trade_preview_leverage",
            "trade_confirm_leverage",
            "trade_preview_margin_mode",
            "trade_confirm_margin_mode",
        }
        self.assertTrue(expected.issubset(names))

    def test_market_depth_tool_uses_public_adapter_without_credential(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(market_adapter=FakeMarketAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_market_1",
                "method": "tools/call",
                "params": {
                    "name": "market_get_depth",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT", "limit": "15"},
                },
            },
            context=self._context(),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(response["result"]["data"]["limit"], "15")

    def test_market_depth_missing_required_fields_returns_missing_fields(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(market_adapter=FakeMarketAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_market_missing",
                "method": "tools/call",
                "params": {
                    "name": "market_get_depth",
                    "arguments": {"market": "spot"},
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "missing_fields")
        self.assertEqual(response["result"]["error"]["details"]["fields"], ["symbol"])

    def test_spot_kline_tool_rejects_futures_only_limit_before_adapter(self):
        from weex_mcp.service import build_default_service

        calls = []

        class RecordingAdapter(FakeMarketAdapter):
            def get_klines(self, **kwargs):
                calls.append(kwargs)
                return {"klines": []}

        response = build_default_service(market_adapter=RecordingAdapter()).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "spot-kline-limit",
                "method": "tools/call",
                "params": {
                    "name": "market_get_klines",
                    "arguments": {
                        "market": "spot",
                        "symbol": "BTCUSDT",
                        "interval": "1m",
                        "limit": "10",
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "invalid_arguments")
        self.assertEqual(calls, [])

    def test_account_positions_tool_uses_user_data_adapter(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(account_adapter=FakeAccountAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_account_1",
                "method": "tools/call",
                "params": {
                    "name": "account_get_positions",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "demo",
                        "symbol": "BTCUSDT",
                        "credential": self._user_data_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(response["result"]["data"]["symbol"], "BTCUSDT")

    def test_account_balance_missing_required_fields_returns_missing_fields(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(account_adapter=FakeAccountAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_account_missing",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "credential": self._user_data_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "missing_fields")
        self.assertEqual(response["result"]["error"]["details"]["fields"], ["market", "trading_mode"])

    def test_account_tools_forward_endpoint_specific_pagination_and_bill_filters(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(account_adapter=FakeAccountAdapter())
        orders = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account-orders-page",
                "method": "tools/call",
                "params": {
                    "name": "account_get_orders",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "status": "history",
                        "page": "2",
                        "credential": self._user_data_credential(),
                    },
                },
            },
            context=self._context(),
        )
        bills = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account-bill-filters",
                "method": "tools/call",
                "params": {
                    "name": "account_get_bills",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "bill_type": "income",
                        "asset": "USDT",
                        "income_type": "position_funding",
                        "next_key_id": "99",
                        "next_key_time": "88",
                        "credential": self._user_data_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertEqual(orders["result"]["data"]["filters"]["page"], "2")
        for field, value in {
            "asset": "USDT",
            "income_type": "position_funding",
            "next_key_id": "99",
            "next_key_time": "88",
        }.items():
            self.assertEqual(bills["result"]["data"]["filters"][field], value)

    def test_trade_cancel_preview_with_credential_ref_requires_provider(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(trade_enabled=True)
        preview = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_trade_1",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_cancel_order",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "order_id": "1",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(preview["result"]["ok"])
        self.assertEqual(preview["result"]["error"]["code"], "secret_provider_unconfigured")
        self.assertNotIn("preview_id", preview["result"])

    def test_trade_confirm_order_rejects_non_order_preview(self):
        from weex_mcp.service import build_default_service

        class LeveragePreviewAdapter:
            def resolve_credential(self, *, credential_argument, context):
                from weex_mcp.secret_provider import ResolvedWeexCredential

                return ResolvedWeexCredential(
                    key_id="leverage-key",
                    api_key="AK_FAKE",
                    api_secret="SK_FAKE",
                    api_passphrase="PP_FAKE",
                    permissions=frozenset(["TRADE"]),
                    fingerprint="leverage-fp",
                )

            def preview_leverage(self, *, arguments, credential, credential_argument, context):
                return {
                    "risk_signature": "leverage-risk",
                    "summary": {
                        "action": "update_leverage",
                        "market": "futures",
                        "trading_mode": "live",
                        "endpoint": "/capi/v3/account/leverage",
                        "request_body": {"symbol": "BTCUSDT", "marginType": "CROSSED", "crossLeverage": "5"},
                    },
                    "risk": {"status": "review_required", "alerts": []},
                }

            def preview_order(self, *, arguments, credential, credential_argument, context):
                return {
                    "risk_signature": "order-risk",
                    "summary": {
                        "action": "order",
                        "market": arguments["market"],
                        "trading_mode": arguments["trading_mode"],
                        "endpoint": "/api/v3/order",
                        "request_body": {
                            "symbol": arguments["symbol"],
                            "side": arguments["side"],
                            "type": arguments["order_type"],
                            "quantity": arguments["quantity"],
                            "newClientOrderId": arguments["client_order_id"],
                        },
                    },
                    "risk": {"status": "review_required", "alerts": []},
                }

            def confirm_order(self, **kwargs):
                raise AssertionError("wrong-action token must be rejected before execution")

        service = build_default_service(trade_enabled=True, trade_adapter=LeveragePreviewAdapter())
        preview = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_trade_action_preview",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_leverage",
                    "arguments": {
                        "market": "futures",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "margin_mode": "cross",
                        "leverage": "5",
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        confirm = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "extended_trade_action_confirm",
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
                        "client_order_id": "wrong-action-order-1",
                        "preview_id": preview["result"]["preview_id"],
                        "confirmation": {"accepted": True},
                        "credential": self._trade_credential(),
                    },
                },
            },
            context=self._context(),
        )

        self.assertFalse(confirm["result"]["ok"])
        self.assertEqual(confirm["result"]["error"]["code"], "approval_mismatch")


if __name__ == "__main__":
    unittest.main()
