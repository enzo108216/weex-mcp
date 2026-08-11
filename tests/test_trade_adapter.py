import json
import unittest


class FakeSecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id=credential_argument["key_id"],
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset([required_permission]),
            fingerprint="fp_trade_test",
        )


class FakeMarketRulesAdapter:
    def get_exchange_info(self, *, market, symbol=None):
        rule = {"symbol": symbol or "BTCUSDT"}
        if market == "spot":
            rule.update({"status": "TRADING", "enableTrade": True, "stepSize": "0.001", "tickSize": "0.1", "minTradeAmount": "0.001"})
        else:
            rule.update({"status": "TRADING", "enableTrade": True, "quantityPrecision": 3, "pricePrecision": 1, "minOrderSize": "0.001"})
        return {"market": market, "symbols": [rule]}


class TradeAdapterTests(unittest.TestCase):
    def test_confirm_adapter_contract_has_no_stateful_compatibility_parameters(self):
        import inspect

        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter, WeexTradeRestAdapter

        for adapter_type in (WeexTradeRestAdapter, WeexFuturesTradeRestAdapter):
            for method_name in ("confirm_order", "confirm_cancel_order", "confirm_leverage", "confirm_margin_mode"):
                with self.subTest(adapter=adapter_type.__name__, method=method_name):
                    parameters = inspect.signature(getattr(adapter_type, method_name)).parameters
                    self.assertIn("execution", parameters)
                    self.assertNotIn("preview", parameters)
                    self.assertNotIn("idempotency_key", parameters)

        constructor_parameters = inspect.signature(WeexTradeRestAdapter).parameters
        self.assertNotIn("client_order_id_factory", constructor_parameters)

    def test_order_preview_requires_explicit_client_order_id(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
        )

        with self.assertRaisesRegex(ValueError, "client_order_id"):
            adapter.preview_order(
                arguments={
                    "market": "futures",
                    "trading_mode": "demo",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": "0.001",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")

    def _credential(self):
        from weex_mcp.credential_context import CredentialContext

        return CredentialContext.from_argument(
            {"type": "credential_ref", "ref": "credref_trade", "key_id": "key_trade", "permissions": ["TRADE"]}
        )

    def _credential_argument(self):
        return {"type": "credential_ref", "ref": "credref_trade", "key_id": "key_trade", "permissions": ["TRADE"]}

    def test_futures_limit_preview_rejects_price_beyond_exchange_precision(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        class RulesAdapter:
            def get_exchange_info(self, *, market, symbol=None):
                return {
                    "market": market,
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "enableTrade": True,
                            "quantityPrecision": 3,
                            "pricePrecision": 1,
                            "minOrderSize": "0.001",
                        }
                    ],
                }

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=RulesAdapter(),
        )

        with self.assertRaises(ValueError) as ctx:
            adapter.preview_order(
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "LIMIT",
                    "quantity": "0.001",
                    "price": "100.05",
                    "time_in_force": "GTC",
                    "client_order_id": "mcp-price-precision",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        self.assertIn("price", str(ctx.exception))

    def test_demo_preview_builds_susdt_market_order_summary(self):
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter

        adapter = WeexFuturesTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
        )

        preview = adapter.preview_order(
            arguments={
                "market": "futures",
                "trading_mode": "demo",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "MARKET",
                "quantity": "0.001",
                "client_order_id": "mcp-test-1",
            },
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertEqual(preview["summary"]["endpoint"], "/capi/v3/sim/order")
        self.assertEqual(preview["summary"]["request_body"]["symbol"], "BTCSUSDT")
        self.assertEqual(preview["summary"]["request_body"]["side"], "BUY")
        self.assertEqual(preview["summary"]["request_body"]["positionSide"], "LONG")
        self.assertEqual(preview["summary"]["request_body"]["type"], "MARKET")
        self.assertEqual(preview["summary"]["request_body"]["newClientOrderId"], "mcp-test-1")
        self.assertEqual(preview["risk"]["status"], "review_required")
        self.assertTrue(preview["risk_signature"].startswith("demo_order_"))

    def test_demo_preview_uses_regular_catalog_symbol_for_existing_susdt_input(self):
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter

        captured = {}

        class RulesAdapter:
            def get_exchange_info(self, *, market, symbol=None):
                captured["market"] = market
                captured["symbol"] = symbol
                return {
                    "market": market,
                    "symbols": [{
                        "symbol": "BTCUSDT",
                        "quantityPrecision": 3,
                        "pricePrecision": 1,
                        "minOrderSize": "0.001",
                    }],
                }

        adapter = WeexFuturesTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=RulesAdapter(),
        )

        preview = adapter.preview_order(
            arguments={
                "market": "futures",
                "trading_mode": "demo",
                "symbol": "BTCSUSDT",
                "side": "BUY",
                "position_side": "LONG",
                "order_type": "MARKET",
                "quantity": "0.001",
                "client_order_id": "mcp-test-susdt",
            },
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertEqual(captured, {"market": "futures", "symbol": "BTCUSDT"})
        self.assertEqual(preview["summary"]["request_body"]["symbol"], "BTCSUSDT")

    def test_demo_confirm_submits_signed_request_with_raw_credential_argument(self):
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter
        from weex_mcp.trade_execution import TradeExecution

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            captured["timeout_seconds"] = timeout_seconds
            body = json.loads(prepared["data"].decode("utf-8"))
            return json.dumps(
                {
                    "orderId": "demo_order_1",
                    "clientOrderId": body["newClientOrderId"],
                    "success": True,
                    "errorCode": "",
                    "errorMessage": "",
                }
            ).encode("utf-8")

        adapter = WeexFuturesTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=fake_transport,
            time_func=lambda: 1234.567,
            timeout_seconds=3.0,
        )
        preview = TradeExecution(
            action="order",
            market="futures",
            trading_mode="demo",
            risk_signature="sig",
            summary={
                "endpoint": "/capi/v3/sim/order",
                "request_body": {
                    "symbol": "BTCSUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.001",
                    "newClientOrderId": "mcp-test-2",
                },
            },
            risk={},
        )

        result = adapter.confirm_order(
            execution=preview,
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertEqual(result["exchange_order_id"], "demo_order_1")
        self.assertEqual(result["client_order_id"], "mcp-test-2")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/sim/order")
        self.assertEqual(captured["headers"]["ACCESS-KEY"], "AK_TEST")
        self.assertEqual(captured["headers"]["ACCESS-PASSPHRASE"], "PP_TEST")
        self.assertEqual(captured["headers"]["ACCESS-TIMESTAMP"], "1234567")
        self.assertIn("ACCESS-SIGN", captured["headers"])
        self.assertEqual(captured["timeout_seconds"], 3.0)

    def test_demo_confirm_rejects_exchange_business_error(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter
        from weex_mcp.trade_execution import TradeExecution

        adapter = WeexFuturesTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: b'{"code":"-1001","msg":"exchange rejected"}',
        )
        preview = TradeExecution(
            action="order",
            market="futures",
            trading_mode="demo",
            risk_signature="sig",
            summary={
                "endpoint": "/capi/v3/sim/order",
                "request_body": {
                    "symbol": "BTCSUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.001",
                    "newClientOrderId": "mcp-error-1",
                },
            },
            risk={},
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_order(
                execution=preview,
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

    def test_live_trading_preview_fails_closed(self):
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter

        adapter = WeexFuturesTradeRestAdapter(secret_provider=FakeSecretProvider())

        with self.assertRaises(ValueError) as ctx:
            adapter.preview_order(
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": "0.001",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        self.assertIn("demo futures", str(ctx.exception))

    def test_demo_adapter_rejects_all_non_order_mutations(self):
        from weex_mcp.adapters.trade_rest import WeexFuturesTradeRestAdapter
        from weex_mcp.trade_execution import TradeExecution

        adapter = WeexFuturesTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: self.fail("demo-only adapter attempted a live mutation"),
        )
        mutation_arguments = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "order_id": "1",
            "margin_mode": "cross",
            "leverage": "5",
        }
        for method_name in ("preview_cancel_order", "preview_leverage", "preview_margin_mode"):
            with self.subTest(method=method_name):
                with self.assertRaises(ValueError):
                    getattr(adapter, method_name)(
                        arguments=mutation_arguments,
                        credential=self._credential(),
                        credential_argument=self._credential_argument(),
                        context=self._context(),
                    )

        with self.assertRaises(ValueError):
            adapter.preview_mutation(
                action="batch_cancel",
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "order_ids": ["1"],
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        execution = TradeExecution(
            action="batch_cancel",
            market="futures",
            trading_mode="live",
            summary={
                "action": "batch_cancel",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/batchOrders",
                "request_body": {"orderIdList": ["1"]},
            },
            risk={"status": "review_required"},
            risk_signature="demo-only-mutation",
        )
        with self.assertRaises(ValueError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

    def test_trigger_fields_are_futures_only_and_validated(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
        )
        common = {
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "position_side": "LONG",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "trigger-order-1",
            "tp_trigger_price": "100",
            "tp_working_type": "MARK_PRICE",
        }
        with self.assertRaises(ValueError):
            adapter.preview_order(
                arguments={"market": "spot", **common},
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        with self.assertRaises(ValueError):
            adapter.preview_order(
                arguments={"market": "futures", **common, "tp_trigger_price": "NaN"},
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )
        with self.assertRaises(ValueError):
            adapter.preview_order(
                arguments={"market": "futures", **common, "tp_working_type": "anything"},
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )
        with self.assertRaises(ValueError):
            adapter.preview_order(
                arguments={"market": "futures", **{key: value for key, value in common.items() if key != "tp_trigger_price"}},
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        preview = adapter.preview_order(
            arguments={"market": "futures", **common},
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )
        self.assertEqual(preview["summary"]["request_body"]["tpTriggerPrice"], "100")
        self.assertEqual(preview["summary"]["request_body"]["TpWorkingType"], "MARK_PRICE")

    def test_trade_preview_rejects_nested_exchange_info_failure(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=WeexMarketRestAdapter(
                transport=lambda url, timeout: b'{"code":"0","data":{"success":false,"symbols":[]}}'
            ),
        )
        with self.assertRaises(WeexRestBusinessError):
            adapter.preview_order(
                arguments={
                    "market": "spot",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "1",
                    "client_order_id": "nested-error-1",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )


if __name__ == "__main__":
    unittest.main()
