import json
import unittest


class FakeSecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id="key_trade",
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset(["TRADE"]),
            fingerprint="fp_trade",
        )


class FakeMarketRulesAdapter:
    def get_exchange_info(self, *, market, symbol=None):
        rule = {"symbol": symbol or "BTCUSDT"}
        if market == "spot":
            rule.update({"status": "TRADING", "enableTrade": True, "stepSize": "0.001", "tickSize": "0.1", "minTradeAmount": "0.001"})
        else:
            rule.update({"quantityPrecision": 3, "pricePrecision": 1, "minOrderSize": "0.001", "minLeverage": 1, "maxLeverage": 100})
        return {"market": market, "symbols": [rule]}


class FixedFuturesCatalogAdapter:
    def get_exchange_info(self, *, market, symbol=None):
        return {
            "market": market,
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "quantityPrecision": 3,
                    "pricePrecision": 1,
                    "minOrderSize": "0.001",
                    "minLeverage": 1,
                    "maxLeverage": 100,
                }
            ],
        }


class TradeRestCompleteTests(unittest.TestCase):
    def test_preview_rejects_exchange_info_without_required_trading_rules(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import ResolvedWeexCredential
        from weex_mcp.tenant_context import TenantContext

        class Provider:
            def resolve(self, *, credential_argument, context, required_permission):
                return ResolvedWeexCredential(
                    key_id="resolved-key", api_key="AK_TEST", api_secret="SK_TEST", api_passphrase="PP_TEST",
                    permissions=frozenset([required_permission]), fingerprint="resolved-fp",
                )

        class IncompleteExchangeInfo:
            def get_exchange_info(self, *, market, symbol=None):
                return {"market": market, "symbols": [{"symbol": symbol or "BTCUSDT"}]}

        adapter = WeexTradeRestAdapter(secret_provider=Provider(), market_adapter=IncompleteExchangeInfo())
        context = TenantContext(tenant_id="t", user_id="u", session_id="s")
        credential = Provider().resolve(
            credential_argument={"type": "credential_ref", "ref": "r", "key_id": "k"},
            context=context,
            required_permission="TRADE",
        )
        with self.assertRaises(Exception) as ctx:
            adapter.preview_order(
                arguments={"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY", "order_type": "MARKET", "quantity": "1", "client_order_id": "incomplete-rules-1"},
                credential=credential,
                credential_argument={"type": "credential_ref", "ref": "r", "key_id": "k"},
                context=context,
            )
        self.assertEqual(getattr(ctx.exception, "code", None), "invalid_response")

    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")

    def _credential_argument(self):
        return {"type": "credential_ref", "ref": "ref_trade", "key_id": "key_trade", "permissions": ["TRADE"]}

    def _credential(self):
        from weex_mcp.credential_context import CredentialContext

        return CredentialContext.from_argument(self._credential_argument())

    def _preview_record(self, preview_result, *, market, trading_mode):
        from weex_mcp.trade_execution import TradeExecution

        return TradeExecution(
            action=str(preview_result["summary"].get("action") or "order"),
            market=market,
            trading_mode=trading_mode,
            risk_signature=preview_result["risk_signature"],
            summary=preview_result["summary"],
            risk=preview_result["risk"],
        )

    def _adapter(self, response, captured):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        def transport(prepared, timeout):
            captured.update(prepared)
            return json.dumps(response).encode()

        return WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
            time_func=lambda: 1234.567,
        )

    def test_spot_live_order_request(self):
        captured = {}
        adapter = self._adapter({"orderId": 1001, "clientOrderId": "mcp-client-id"}, captured)
        preview = adapter.preview_order(
            arguments={
                "market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
                "order_type": "LIMIT", "quantity": "1", "price": "100", "time_in_force": "GTC", "client_order_id": "mcp-client-id",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        result = adapter.confirm_order(
            execution=self._preview_record(preview, market="spot", trading_mode="live"),
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api-spot.weex.com/api/v3/order")
        self.assertNotIn("positionSide", captured["body"])
        self.assertEqual(captured["body"]["timeInForce"], "GTC")
        self.assertTrue(result["success"])

    def test_futures_live_order_request(self):
        captured = {}
        adapter = self._adapter({"orderId": "2001", "clientOrderId": "mcp-client-id", "success": True}, captured)
        preview = adapter.preview_order(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "SELL",
                "position_side": "SHORT", "order_type": "MARKET", "quantity": "0.01", "client_order_id": "mcp-client-id",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        adapter.confirm_order(
            execution=self._preview_record(preview, market="futures", trading_mode="live"),
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/order")
        self.assertEqual(captured["body"]["positionSide"], "SHORT")
        self.assertEqual(captured["body"]["symbol"], "BTCUSDT")

    def test_futures_order_confirm_requires_explicit_success_true(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        captured = {}
        adapter = self._adapter({"orderId": "2001", "clientOrderId": "mcp-client-id"}, captured)
        preview = adapter.preview_order(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
                "position_side": "LONG", "order_type": "MARKET", "quantity": "0.01", "client_order_id": "mcp-client-id",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.confirm_order(
                execution=self._preview_record(preview, market="futures", trading_mode="live"),
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )

        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_futures_order_confirm_accepts_outer_success_with_nested_order(self):
        captured = {}
        adapter = self._adapter(
            {"success": True, "data": {"orderId": "2002", "clientOrderId": "nested-client-id"}},
            captured,
        )
        preview = adapter.preview_order(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
                "position_side": "LONG", "order_type": "MARKET", "quantity": "0.01", "client_order_id": "mcp-client-id",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        result = adapter.confirm_order(
            execution=self._preview_record(preview, market="futures", trading_mode="live"),
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(result["exchange_order_id"], "2002")
        self.assertTrue(result["success"])

    def test_spot_preview_enforces_max_trade_amount_boundary(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        class SpotCatalog:
            def get_exchange_info(self, *, market, symbol=None):
                return {
                    "market": market,
                    "symbols": [{
                        "symbol": symbol or "BTCUSDT",
                        "status": "TRADING",
                        "enableTrade": True,
                        "stepSize": "0.001",
                        "tickSize": "0.1",
                        "minTradeAmount": "0.001",
                        "maxTradeAmount": "2",
                    }],
                }

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=SpotCatalog(),
        )
        base_arguments = {
            "market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
            "order_type": "MARKET", "client_order_id": "mcp-client-id",
        }

        preview = adapter.preview_order(
            arguments=base_arguments | {"quantity": "2"},
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        self.assertEqual(preview["summary"]["quantity"], "2")

        with self.assertRaises(ValueError) as ctx:
            adapter.preview_order(
                arguments=base_arguments | {"quantity": "2.001"},
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertIn("maximum", str(ctx.exception))

    def test_futures_preview_enforces_max_order_size_boundary(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        class FuturesCatalog:
            def get_exchange_info(self, *, market, symbol=None):
                return {
                    "market": market,
                    "symbols": [{
                        "symbol": symbol or "BTCUSDT",
                        "quantityPrecision": 3,
                        "pricePrecision": 1,
                        "minOrderSize": "0.001",
                        "maxOrderSize": "1",
                    }],
                }

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FuturesCatalog(),
        )
        base_arguments = {
            "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
            "position_side": "LONG", "order_type": "MARKET", "client_order_id": "mcp-client-id",
        }

        preview = adapter.preview_order(
            arguments=base_arguments | {"quantity": "1"},
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        self.assertEqual(preview["summary"]["quantity"], "1")

        with self.assertRaises(ValueError) as ctx:
            adapter.preview_order(
                arguments=base_arguments | {"quantity": "1.001"},
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertIn("maximum", str(ctx.exception))

    def test_futures_order_preview_accepts_real_catalog_shape_without_spot_status_fields(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FixedFuturesCatalogAdapter(),
        )
        preview = adapter.preview_order(
            arguments={
                "market": "futures",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "MARKET",
                "quantity": "0.01",
                "client_order_id": "mcp-client-id",
            },
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertEqual(preview["summary"]["request_body"]["symbol"], "BTCUSDT")

    def test_leverage_preview_rejects_unknown_symbol_and_catalog_range_violations(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FixedFuturesCatalogAdapter(),
        )
        base = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "margin_mode": "cross",
            "leverage": "5",
        }
        for arguments in (
            base | {"symbol": "NOTAREALSYMBOL"},
            base | {"leverage": "0.5"},
            base | {"leverage": "101"},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    adapter.preview_leverage(
                        arguments=arguments,
                        credential=self._credential(),
                        credential_argument=self._credential_argument(),
                        context=self._context(),
                    )

    def test_margin_mode_preview_rejects_unknown_symbol(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FixedFuturesCatalogAdapter(),
        )
        with self.assertRaises(ValueError):
            adapter.preview_margin_mode(
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "NOTAREALSYMBOL",
                    "margin_mode": "cross",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

    def test_spot_and_futures_live_cancel_requests(self):
        for market, expected_url, response in (
            ("spot", "https://api-spot.weex.com/api/v3/order?orderId=1001", {"orderId": 1001, "status": "CANCELED"}),
            ("futures", "https://api-contract.weex.com/capi/v3/order?origClientOrderId=client-1", {"success": True}),
        ):
            with self.subTest(market=market):
                captured = {}
                adapter = self._adapter(response, captured)
                arguments = {"market": market, "trading_mode": "live", "symbol": "BTCUSDT"}
                arguments["order_id" if market == "spot" else "client_order_id"] = "1001" if market == "spot" else "client-1"
                preview = adapter.preview_cancel_order(
                    arguments=arguments, credential=self._credential(), credential_argument=self._credential_argument(), context=self._context()
                )
                result = adapter.confirm_cancel_order(
                    execution=self._preview_record(preview, market=market, trading_mode="live"), credential=self._credential(),
                    credential_argument=self._credential_argument(), context=self._context(),
                )
                self.assertEqual(captured["method"], "DELETE")
                self.assertEqual(captured["url"], expected_url)
                self.assertTrue(result["success"])

    def test_cancel_request_does_not_require_or_drop_symbol_and_second_identity(self):
        captured = {}
        adapter = self._adapter({"orderId": "1001", "origClientOrderId": "client-1", "success": True}, captured)
        preview = adapter.preview_cancel_order(
            arguments={
                "market": "futures",
                "trading_mode": "live",
                "order_id": "1001",
                "client_order_id": "client-1",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        self.assertEqual(preview["summary"]["request_query"], {"orderId": "1001", "origClientOrderId": "client-1"})
        adapter.confirm_cancel_order(
            execution=self._preview_record(preview, market="futures", trading_mode="live"),
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        self.assertIn("?orderId=1001&origClientOrderId=client-1", captured["url"])

    def test_cancel_rejects_non_numeric_or_out_of_range_order_id_before_transport(self):
        for order_id in ("order-1", "9223372036854775808"):
            with self.subTest(order_id=order_id):
                captured = {}
                adapter = self._adapter({"success": True}, captured)
                with self.assertRaises(ValueError):
                    adapter.preview_cancel_order(
                        arguments={"market": "spot", "trading_mode": "live", "order_id": order_id},
                        credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
                    )
                self.assertEqual(captured, {})

    def test_futures_cancel_requires_explicit_success_boolean(self):
        captured = {}
        adapter = self._adapter({"data": {"status": "CANCELED"}}, captured)
        preview = adapter.preview_cancel_order(
            arguments={"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "order_id": "1001"},
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.confirm_cancel_order(
                execution=self._preview_record(preview, market="futures", trading_mode="live"),
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_futures_leverage_requires_requested_leverage_evidence(self):
        captured = {}
        adapter = self._adapter({"data": {"symbol": "BTCUSDT"}}, captured)
        preview = adapter.preview_leverage(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT",
                "margin_mode": "cross", "leverage": "5",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.confirm_leverage(
                execution=self._preview_record(preview, market="futures", trading_mode="live"),
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_futures_live_leverage_request(self):
        captured = {}
        adapter = self._adapter({"symbol": "BTCUSDT", "marginType": "CROSSED", "crossLeverage": "5"}, captured)
        preview = adapter.preview_leverage(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT",
                "margin_mode": "cross", "leverage": "5",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        result = adapter.confirm_leverage(
            execution=self._preview_record(preview, market="futures", trading_mode="live"), credential=self._credential(),
            credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/account/leverage")
        self.assertEqual(captured["body"], {"symbol": "BTCUSDT", "marginType": "CROSSED", "crossLeverage": "5"})
        self.assertTrue(result["success"])

    def test_futures_live_margin_mode_request(self):
        captured = {}
        adapter = self._adapter({"code": "0", "msg": "success", "requestTime": 1234567}, captured)
        preview = adapter.preview_margin_mode(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT",
                "margin_mode": "isolated", "separated_type": "SEPARATED",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )
        result = adapter.confirm_margin_mode(
            execution=self._preview_record(preview, market="futures", trading_mode="live"), credential=self._credential(),
            credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/account/marginType")
        self.assertEqual(captured["body"], {"symbol": "BTCUSDT", "marginType": "ISOLATED", "separatedType": "SEPARATED"})
        self.assertTrue(result["success"])

    def test_margin_mode_rejects_nonempty_garbage_response_without_success_evidence(self):
        captured = {}
        adapter = self._adapter({"data": {"garbage": "x"}}, captured)
        preview = adapter.preview_margin_mode(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT",
                "margin_mode": "isolated",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.confirm_margin_mode(
                execution=self._preview_record(preview, market="futures", trading_mode="live"),
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_spot_demo_and_demo_mutations_are_rejected(self):
        captured = {}
        adapter = self._adapter({}, captured)
        with self.assertRaises(ValueError):
            adapter.preview_order(
                arguments={
                    "market": "spot", "trading_mode": "demo", "symbol": "BTCUSDT", "side": "BUY",
                    "order_type": "MARKET", "quantity": "1",
                },
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        with self.assertRaises(ValueError):
            adapter.preview_cancel_order(
                arguments={"market": "futures", "trading_mode": "demo", "symbol": "BTCUSDT", "order_id": "1"},
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()
