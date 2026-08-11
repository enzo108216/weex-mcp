import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError


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
    def _plaintext_order(self):
        return {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "1",
            "client_order_id": "permission-probe-1",
            "credential": {
                "type": "plaintext",
                "api_key": "AK_INLINE",
                "api_secret": "SK_INLINE",
                "api_passphrase": "PP_INLINE",
            },
        }

    def _call_tool(self, service, name, arguments):
        return service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            protocol_version="2025-11-25",
        )["result"]["structuredContent"]

    def test_default_trade_preview_authenticates_plaintext_before_issuing_token(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return json.dumps({"code": "-1002", "msg": "invalid key"}).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        service = build_default_service(
            trade_enabled=True,
            trade_adapter=adapter,
            market_adapter=FakeMarketRulesAdapter(),
        )

        result = self._call_tool(service, "trade_preview_order", self._plaintext_order())

        self.assertFalse(result["ok"])
        self.assertNotIn("approval_token", result)
        self.assertEqual(len(private_calls), 1)
        self.assertEqual(private_calls[0]["method"], "GET")
        self.assertEqual(private_calls[0]["url"], "https://api-spot.weex.com/api/v3/account")

    def test_invalid_spot_demo_order_is_rejected_before_private_access_probe(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return json.dumps({"canTrade": True}).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        result = self._call_tool(
            build_default_service(trade_enabled=True, trade_adapter=adapter),
            "trade_preview_order",
            self._plaintext_order() | {"trading_mode": "demo", "client_order_id": "spot-demo-review-1"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertEqual(private_calls, [])

    def test_default_trade_preview_rejects_authenticated_key_without_trade_access(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        def transport(prepared, timeout):
            return json.dumps({"canTrade": False, "balances": []}).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)

        result = self._call_tool(service, "trade_preview_order", self._plaintext_order())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "permission_denied")
        self.assertNotIn("approval_token", result)

    def test_trade_access_probe_rejects_nested_failure_under_success_code(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return json.dumps(
                {"code": "0", "data": {"success": False, "canTrade": True}}
            ).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)

        result = self._call_tool(service, "trade_preview_order", self._plaintext_order())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertNotIn("approval_token", result)
        self.assertEqual(len(private_calls), 1)
        self.assertEqual(private_calls[0]["method"], "GET")

    def test_futures_live_and_demo_previews_use_read_only_account_config_probe(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return json.dumps({"canTrade": True}).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)

        for trading_mode in ("live", "demo"):
            arguments = self._plaintext_order() | {
                "market": "futures",
                "trading_mode": trading_mode,
                "position_side": "LONG",
                "quantity": "0.001",
                "client_order_id": f"futures-{trading_mode}-permission-probe",
            }
            result = self._call_tool(service, "trade_preview_order", arguments)
            self.assertTrue(result["ok"])

        self.assertEqual(len(private_calls), 2)
        self.assertTrue(all(call["method"] == "GET" for call in private_calls))
        self.assertTrue(
            all(
                call["url"]
                == "https://api-contract.weex.com/capi/v3/account/accountConfig"
                for call in private_calls
            )
        )

    def test_default_trade_confirm_repeats_read_only_trade_access_preflight(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            if prepared["method"] == "GET":
                return json.dumps({"canTrade": True, "permissions": ["SPOT_TRADING"], "balances": []}).encode()
            return json.dumps({"orderId": "1001", "clientOrderId": "permission-probe-1"}).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=RequestSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._plaintext_order()
        preview = self._call_tool(service, "trade_preview_order", arguments)

        confirmed = self._call_tool(
            service,
            "trade_confirm_order",
            arguments
            | {
                "approval_token": preview["approval_token"],
                "confirmation": {"accepted": True, "reply_text": "确认"},
            },
        )

        self.assertTrue(confirmed["ok"])
        access_calls = [call for call in private_calls if call["method"] == "GET"]
        mutation_calls = [call for call in private_calls if call["method"] == "POST"]
        self.assertEqual(len(access_calls), 2)
        self.assertEqual(len(mutation_calls), 1)

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
            if "/capi/v3/account/symbolConfig" in prepared["url"]:
                return json.dumps(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "marginType": "CROSSED",
                            "separatedType": "SEPARATED",
                            "crossLeverage": "5",
                            "isolatedLongLeverage": "1",
                            "isolatedShortLeverage": "1",
                        }
                    ]
                ).encode()
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
            {"success": True, "data": {"orderId": "2002", "clientOrderId": "mcp-client-id"}},
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

    def test_order_confirmation_rejects_a_mismatched_returned_client_id(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        captured = {}
        adapter = self._adapter(
            {"success": True, "data": {"orderId": "2003", "clientOrderId": "another-client-id"}},
            captured,
        )
        preview = adapter.preview_order(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY",
                "position_side": "LONG", "order_type": "MARKET", "quantity": "0.01", "client_order_id": "mcp-client-id",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_order(
                execution=self._preview_record(preview, market="futures", trading_mode="live"),
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )

    def test_futures_cancel_confirmation_rejects_a_mismatched_returned_identifier(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        captured = {}
        adapter = self._adapter({"success": True, "orderId": "2"}, captured)
        execution = TradeExecution(
            action="cancel_order",
            market="futures",
            trading_mode="live",
            summary={
                "action": "cancel_order",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/order",
                "request_query": {"orderId": "1"},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_cancel_order(
                execution=execution,
                credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
            )

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

    def test_spot_limit_preview_rejects_live_usdt_order_below_five_usdt(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        class SpotUsdtCatalog:
            def get_exchange_info(self, *, market, symbol=None):
                return {
                    "market": market,
                    "symbols": [
                        {
                            "symbol": symbol or "BTCUSDT",
                            "status": "TRADING",
                            "enableTrade": True,
                            "quoteAsset": "USDT",
                            "stepSize": "0.000001",
                            "tickSize": "0.01",
                            "minTradeAmount": "0.000002",
                        }
                    ],
                }

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=SpotUsdtCatalog(),
        )

        with self.assertRaises(ValueError) as ctx:
            adapter.preview_order(
                arguments={
                    "market": "spot",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": "0.000002",
                    "price": "58362.79",
                    "time_in_force": "GTC",
                    "client_order_id": "spot-min-value",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        self.assertIn("5 USDT", str(ctx.exception))

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
            (
                "futures",
                "https://api-contract.weex.com/capi/v3/order?origClientOrderId=client-1",
                {"orderId": "1001", "origClientOrderId": "client-1", "success": True},
            ),
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

    def test_futures_cancel_by_client_id_accepts_staging_identity_only_response(self):
        captured = {}
        adapter = self._adapter(
            {
                "orderId": None,
                "origClientOrderId": "client-1",
                "success": True,
                "errorCode": None,
                "errorMessage": None,
            },
            captured,
        )
        preview = adapter.preview_cancel_order(
            arguments={
                "market": "futures",
                "trading_mode": "live",
                "client_order_id": "client-1",
            },
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        result = adapter.confirm_cancel_order(
            execution=self._preview_record(preview, market="futures", trading_mode="live"),
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["client_order_id"], "client-1")
        self.assertIsNone(result["exchange_order_id"])

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
        adapter = self._adapter(
            {
                "symbol": "BTCUSDT",
                "marginType": "CROSSED",
                "crossLeverage": "5",
                "isolatedLongLeverage": "1",
                "isolatedShortLeverage": "1",
            },
            captured,
        )
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
        self.assertEqual(
            captured["body"],
            {
                "symbol": "BTCUSDT",
                "marginType": "CROSSED",
                "crossLeverage": "5",
                "isolatedLongLeverage": "5",
                "isolatedShortLeverage": "5",
            },
        )
        self.assertTrue(result["success"])

    def test_leverage_preview_rejects_nested_symbol_config_failure_under_success_code(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        def transport(prepared, timeout):
            return json.dumps(
                {
                    "code": "0",
                    "data": {
                        "success": False,
                        "symbol": "BTCUSDT",
                        "marginType": "CROSSED",
                        "crossLeverage": "5",
                        "isolatedLongLeverage": "1",
                        "isolatedShortLeverage": "1",
                    },
                }
            ).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
        )

        with self.assertRaises(WeexRestBusinessError) as context:
            adapter.preview_leverage(
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "leverage": "5",
                    "margin_mode": "cross",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        self.assertEqual(context.exception.code, "upstream_rejected")

    def test_leverage_preview_rejects_non_positive_current_symbol_leverages(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        for invalid_field in ("crossLeverage", "isolatedLongLeverage", "isolatedShortLeverage"):
            with self.subTest(invalid_field=invalid_field):
                configuration = {
                    "symbol": "BTCUSDT",
                    "marginType": "CROSSED",
                    "crossLeverage": "1",
                    "isolatedLongLeverage": "1",
                    "isolatedShortLeverage": "1",
                }
                configuration[invalid_field] = "0"

                def transport(prepared, timeout, *, response=configuration):
                    return json.dumps({"code": "0", "data": response}).encode()

                adapter = WeexTradeRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    market_adapter=FakeMarketRulesAdapter(),
                    transport=transport,
                )

                with self.assertRaises(WeexRestBusinessError) as context:
                    adapter.preview_leverage(
                        arguments={
                            "market": "futures",
                            "trading_mode": "live",
                            "symbol": "BTCUSDT",
                            "leverage": "5",
                            "margin_mode": "cross",
                        },
                        credential=self._credential(),
                        credential_argument=self._credential_argument(),
                        context=self._context(),
                    )

                self.assertEqual(context.exception.code, "invalid_response")

    def test_futures_isolated_leverage_preserves_other_symbol_leverages(self):
        captured = {}
        adapter = self._adapter(
            {
                "symbol": "BTCUSDT",
                "marginType": "ISOLATED",
                "crossLeverage": "5",
                "isolatedLongLeverage": "3",
                "isolatedShortLeverage": "1",
            },
            captured,
        )
        preview = adapter.preview_leverage(
            arguments={
                "market": "futures",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "margin_mode": "isolated",
                "position_side": "LONG",
                "leverage": "3",
            },
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )
        result = adapter.confirm_leverage(
            execution=self._preview_record(preview, market="futures", trading_mode="live"),
            credential=self._credential(),
            credential_argument=self._credential_argument(),
            context=self._context(),
        )

        self.assertEqual(
            captured["body"],
            {
                "symbol": "BTCUSDT",
                "marginType": "ISOLATED",
                "crossLeverage": "5",
                "isolatedLongLeverage": "3",
                "isolatedShortLeverage": "1",
            },
        )
        self.assertTrue(result["success"])

    def test_futures_leverage_fails_closed_when_symbol_config_is_incomplete(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        def transport(prepared, timeout):
            return json.dumps([{"symbol": "BTCUSDT", "marginType": "CROSSED"}]).encode()

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            market_adapter=FakeMarketRulesAdapter(),
            transport=transport,
            time_func=lambda: 1234.567,
        )

        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.preview_leverage(
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "margin_mode": "cross",
                    "leverage": "2",
                },
                credential=self._credential(),
                credential_argument=self._credential_argument(),
                context=self._context(),
            )

        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_trade_default_transport_never_converts_http_error_body_into_success(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import _default_transport

        error_body = b'{"code":"0","data":{"success":true}}'
        http_error = HTTPError(
            url="https://api-contract.weex.com/capi/v3/account/leverage",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(error_body),
        )
        prepared = {
            "url": "https://api-contract.weex.com/capi/v3/account/leverage",
            "method": "POST",
            "data": b"{}",
            "headers": {},
        }

        with patch("weex_mcp.adapters.trade_rest.urlopen", side_effect=http_error):
            with self.assertRaises(WeexRestBusinessError) as context:
                _default_transport(prepared, 5.0)

        self.assertEqual(context.exception.code, "http_400")

    def test_trade_default_transport_preserves_business_code_on_http_error(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import _default_transport

        http_error = HTTPError(
            url="https://api-contract.weex.com/capi/v3/account/leverage",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"code":"-1141","msg":"Parameter cannot be empty."}'),
        )
        prepared = {
            "url": "https://api-contract.weex.com/capi/v3/account/leverage",
            "method": "POST",
            "data": b"{}",
            "headers": {},
        }

        with patch("weex_mcp.adapters.trade_rest.urlopen", side_effect=http_error):
            with self.assertRaises(WeexRestBusinessError) as context:
                _default_transport(prepared, 5.0)

        self.assertEqual(context.exception.code, "-1141")

    def test_direct_staging_trade_adapter_uses_staging_market_origins(self):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter

        adapter = WeexTradeRestAdapter(
            secret_provider=FakeSecretProvider(),
            spot_base_url="https://stg-spotpro-openapi.weex.tech",
            futures_base_url="https://stg-api-contract.weex.tech",
        )

        self.assertEqual(
            adapter.market_adapter.spot_base_url,
            "https://stg-spotpro-openapi.weex.tech",
        )
        self.assertEqual(
            adapter.market_adapter.futures_base_url,
            "https://stg-api-contract.weex.tech",
        )

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

    def test_futures_live_margin_mode_accepts_documented_http_style_success_code(self):
        captured = {}
        adapter = self._adapter({"code": "200", "msg": "success", "requestTime": 1234567}, captured)
        preview = adapter.preview_margin_mode(
            arguments={
                "market": "futures", "trading_mode": "live", "symbol": "BTCUSDT",
                "margin_mode": "cross",
            },
            credential=self._credential(), credential_argument=self._credential_argument(), context=self._context(),
        )

        result = adapter.confirm_margin_mode(
            execution=self._preview_record(preview, market="futures", trading_mode="live"), credential=self._credential(),
            credential_argument=self._credential_argument(), context=self._context(),
        )

        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/account/marginType")
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
