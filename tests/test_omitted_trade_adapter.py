import json
import unittest


def credential_argument():
    return {
        "type": "plaintext",
        "api_key": "AK_WRITE",
        "api_secret": "SK_WRITE",
        "api_passphrase": "PP_WRITE",
    }


class RulesAdapter:
    def get_exchange_info(self, *, market, symbol=None):
        return {
            "market": market,
            "symbols": [
                {
                    "symbol": symbol or "BTCUSDT",
                    "status": "TRADING",
                    "enableTrade": True,
                    "minTradeAmount": "0.001",
                    "maxTradeAmount": "100",
                    "stepSize": "0.001",
                    "tickSize": "0.1",
                    "pricePrecision": 1,
                    "quantityPrecision": 3,
                }
            ],
        }


class OmittedTradeAdapterTests(unittest.TestCase):
    def _resolved(self, provider, context):
        return provider.resolve(
            credential_argument=credential_argument(),
            context=context,
            required_permission="TRADE",
        )

    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="anonymous", user_id="anonymous", session_id="anonymous")

    def _adapter(self, transport):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider

        provider = RequestSecretProvider()
        adapter = WeexTradeRestAdapter(
            secret_provider=provider,
            market_adapter=RulesAdapter(),
            transport=transport,
            time_func=lambda: 1,
        )
        return adapter, provider

    def test_preview_mutation_builds_only_static_allowlisted_endpoints(self):
        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            if prepared["url"].endswith("/capi/v3/account/position/allPosition"):
                return json.dumps(
                    [
                        {
                            "positionId": "7",
                            "symbol": "BTCUSDT",
                            "positionSide": "LONG",
                            "marginType": "ISOLATED",
                            "positionAmt": "0.001",
                            "isolatedMargin": "10",
                            "isAutoAppendIsolatedMargin": False,
                        }
                    ]
                ).encode()
            raise AssertionError(f"preview attempted an unexpected private request: {prepared['url']}")

        adapter, provider = self._adapter(transport)
        resolved = self._resolved(provider, self._context())
        common = {
            "credential": resolved,
            "credential_argument": credential_argument(),
            "context": self._context(),
        }
        cases = [
            (
                "batch_order",
                {
                    "market": "spot",
                    "trading_mode": "live",
                    "orders": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "quantity": "0.1",
                            "price": "100",
                            "time_in_force": "GTC",
                            "client_order_id": "batch-1",
                        }
                    ],
                },
                "/api/v3/order/batch",
                "request_body",
            ),
            (
                "batch_cancel",
                {"market": "futures", "trading_mode": "live", "order_ids": ["1", "2"]},
                "/capi/v3/batchOrders",
                "request_body",
            ),
            (
                "cancel_scope",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "order_kind": "conditional",
                    "scope": "all",
                    "acknowledge_all_symbols": True,
                },
                "/capi/v3/algoOpenOrders",
                "request_query",
            ),
            (
                "conditional_order",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "position_side": "LONG",
                    "conditional_type": "STOP_MARKET",
                    "quantity": "0.001",
                    "trigger_price": "100",
                    "client_algo_id": "conditional-1",
                },
                "/capi/v3/algoOrder",
                "request_body",
            ),
            (
                "conditional_cancel",
                {"market": "futures", "trading_mode": "live", "order_id": "1"},
                "/capi/v3/algoOrder",
                "request_query",
            ),
            (
                "tp_sl",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "operation": "place",
                    "symbol": "BTCUSDT",
                    "client_algo_id": "tp-1",
                    "plan_type": "TAKE_PROFIT",
                    "trigger_price": "100",
                    "position_side": "LONG",
                    "quantity": "0.001",
                },
                "/capi/v3/placeTpSlOrder",
                "request_body",
            ),
            (
                "close_positions",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "scope": "position",
                    "position_id": "7",
                    "symbol": "BTCUSDT",
                },
                "/capi/v3/closePositions",
                "request_body",
            ),
            (
                "position_margin",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "isolated_position_id": "7",
                    "amount": "1",
                    "adjustment": "increase",
                },
                "/capi/v3/account/positionMargin",
                "request_body",
            ),
            (
                "auto_append_margin",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "position_id": "7",
                    "enabled": True,
                },
                "/capi/v3/account/modifyAutoAppendMargin",
                "request_body",
            ),
        ]

        for action, arguments, endpoint, binding in cases:
            with self.subTest(action=action):
                preview = adapter.preview_mutation(action=action, arguments=arguments, **common)
                self.assertEqual(preview["summary"]["action"], action)
                self.assertEqual(preview["summary"]["endpoint"], endpoint)
                self.assertIn(binding, preview["summary"])

        self.assertEqual(
            [call["url"].split("?", 1)[0] for call in calls],
            [
                "https://api-contract.weex.com/capi/v3/account/position/allPosition",
                "https://api-contract.weex.com/capi/v3/account/position/allPosition",
                "https://api-contract.weex.com/capi/v3/account/position/allPosition",
            ],
        )

    def test_confirm_mutation_uses_bound_method_path_and_normalizes_success(self):
        from weex_mcp.trade_execution import TradeExecution

        calls = []
        responses = {
            "/api/v3/order/batch": {
                "orderList": [{"orderId": "1", "clientOrderId": "a"}]
            },
            "/capi/v3/batchOrders": [
                {"orderId": "1", "clientOrderId": "a", "success": True}
            ],
            "/capi/v3/algoOrder": {"orderId": "1", "success": True},
            "/capi/v3/placeTpSlOrder": [{"orderId": "1", "success": True}],
            "/capi/v3/modifyTpSlOrder": {"success": True},
            "/capi/v3/closePositions": [
                {"positionId": "7", "success": True, "successOrderId": "2"}
            ],
            "/capi/v3/account/positionMargin": {"code": "0", "msg": "success"},
            "/capi/v3/account/modifyAutoAppendMargin": {"code": "0", "msg": "success"},
        }
        position_reads = iter(
            [
                [{"id": "7", "symbol": "BTCUSDT", "marginType": "ISOLATED", "size": "0.1", "isolatedMargin": "10"}],
                [{"id": "7", "symbol": "BTCUSDT", "marginType": "ISOLATED", "size": "0.1", "isolatedMargin": "11"}],
                [{"id": "7", "symbol": "BTCUSDT", "marginType": "ISOLATED", "size": "0.1", "isAutoAppendIsolatedMargin": False}],
                [{"id": "7", "symbol": "BTCUSDT", "marginType": "ISOLATED", "size": "0.1", "isAutoAppendIsolatedMargin": True}],
            ]
        )

        def transport(prepared, timeout):
            calls.append(prepared)
            path = "/" + prepared["url"].split("/", 3)[3].split("?", 1)[0]
            if path == "/capi/v3/account/position/allPosition":
                return json.dumps(next(position_reads)).encode()
            return json.dumps(responses[path]).encode()

        adapter, provider = self._adapter(transport)
        resolved = self._resolved(provider, self._context())
        common = {
            "credential": resolved,
            "credential_argument": credential_argument(),
            "context": self._context(),
        }
        executions = [
            ("batch_order", "spot", "/api/v3/order/batch", "POST", {"symbol": "BTCUSDT", "orderList": [{"newClientOrderId": "a"}]}, {}),
            ("batch_order", "futures", "/capi/v3/batchOrders", "POST", {"batchOrders": [{"newClientOrderId": "a"}]}, {}),
            ("conditional_cancel", "futures", "/capi/v3/algoOrder", "DELETE", {}, {"orderId": "1"}),
            ("tp_sl", "futures", "/capi/v3/placeTpSlOrder", "POST", {"clientAlgoId": "a"}, {}),
            ("tp_sl", "futures", "/capi/v3/modifyTpSlOrder", "POST", {"orderId": "1"}, {}),
            ("close_positions", "futures", "/capi/v3/closePositions", "POST", {"positionId": "7"}, {}),
            ("position_margin", "futures", "/capi/v3/account/positionMargin", "POST", {"isolatedPositionId": "7", "amount": "1", "type": 1}, {}),
            ("auto_append_margin", "futures", "/capi/v3/account/modifyAutoAppendMargin", "POST", {"positionId": "7", "autoAppendMargin": True}, {}),
        ]

        for action, market, endpoint, _method, body, query in executions:
            summary = {
                "action": action,
                "market": market,
                "trading_mode": "live",
                "endpoint": endpoint,
                "request_body" if body else "request_query": body or query,
            }
            execution = TradeExecution(
                action=action,
                market=market,
                trading_mode="live",
                summary=summary,
                risk={"status": "review_required"},
                risk_signature="risk",
            )
            with self.subTest(action=action, endpoint=endpoint):
                result = adapter.confirm_mutation(execution=execution, **common)
                self.assertTrue(result["success"])

        self.assertEqual(
            [call["method"] for call in calls],
            ["POST", "POST", "DELETE", "POST", "POST", "POST", "GET", "POST", "GET", "GET", "POST", "GET"],
        )
        self.assertEqual(
            ["/" + call["url"].split("/", 3)[3].split("?", 1)[0] for call in calls],
            [
                "/api/v3/order/batch",
                "/capi/v3/batchOrders",
                "/capi/v3/algoOrder",
                "/capi/v3/placeTpSlOrder",
                "/capi/v3/modifyTpSlOrder",
                "/capi/v3/closePositions",
                "/capi/v3/account/position/allPosition",
                "/capi/v3/account/positionMargin",
                "/capi/v3/account/position/allPosition",
                "/capi/v3/account/position/allPosition",
                "/capi/v3/account/modifyAutoAppendMargin",
                "/capi/v3/account/position/allPosition",
            ],
        )

    def test_batch_partial_result_is_explicit_but_malformed_or_empty_create_fails_closed(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        payloads = iter(
            [
                [
                    {"orderId": "1", "clientOrderId": "a", "success": True},
                    {"clientOrderId": "b", "success": False, "errorCode": "E", "errorMessage": "bad"},
                ],
                [],
                [{"success": True}],
            ]
        )
        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(next(payloads)).encode()
        )
        common = {
            "credential": self._resolved(provider, self._context()),
            "credential_argument": credential_argument(),
            "context": self._context(),
        }

        def execution():
            return TradeExecution(
                action="batch_order",
                market="futures",
                trading_mode="live",
                summary={
                    "action": "batch_order",
                    "market": "futures",
                    "trading_mode": "live",
                    "endpoint": "/capi/v3/batchOrders",
                    "request_body": {
                        "batchOrders": [
                            {"newClientOrderId": "a"},
                            {"newClientOrderId": "b"},
                        ]
                    },
                },
                risk={"status": "review_required"},
                risk_signature="risk",
            )

        partial = adapter.confirm_mutation(execution=execution(), **common)
        self.assertTrue(partial["success"])
        self.assertFalse(partial["all_succeeded"])
        self.assertEqual(partial["succeeded_count"], 1)
        self.assertEqual(partial["failed_count"], 1)
        self.assertEqual(partial["items"][0]["client_order_id"], "a")
        self.assertEqual(partial["items"][1]["client_order_id"], "b")

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(execution=execution(), **common)
        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(execution=execution(), **common)

    def test_batch_results_reject_identifiers_unrelated_to_the_request(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        payloads = iter(
            [
                [{"orderId": "1", "clientOrderId": "other", "success": True}],
                [{"orderId": "2", "success": True}],
            ]
        )
        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(next(payloads)).encode()
        )
        common = {
            "credential": self._resolved(provider, self._context()),
            "credential_argument": credential_argument(),
            "context": self._context(),
        }

        executions = [
            TradeExecution(
                action="batch_order",
                market="futures",
                trading_mode="live",
                summary={
                    "action": "batch_order",
                    "market": "futures",
                    "trading_mode": "live",
                    "endpoint": "/capi/v3/batchOrders",
                    "request_body": {"batchOrders": [{"newClientOrderId": "expected"}]},
                },
                risk={"status": "review_required"},
                risk_signature="risk",
            ),
            TradeExecution(
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
                risk_signature="risk",
            ),
        ]

        for execution in executions:
            with self.subTest(action=execution.action):
                with self.assertRaises(WeexRestBusinessError):
                    adapter.confirm_mutation(execution=execution, **common)

    def test_batch_cancel_delete_serializes_the_bound_request_body(self):
        from weex_mcp.trade_execution import TradeExecution

        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return json.dumps(
                [{"orderId": "1", "success": True}]
            ).encode()

        adapter, provider = self._adapter(transport)
        result = adapter.confirm_mutation(
            execution=TradeExecution(
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
                risk_signature="risk",
            ),
            credential=self._resolved(provider, self._context()),
            credential_argument=credential_argument(),
            context=self._context(),
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "DELETE")
        self.assertEqual(json.loads(calls[0]["data"]), {"orderIdList": ["1"]})

    def test_unknown_action_endpoint_and_demo_fail_before_write(self):
        from weex_mcp.trade_execution import TradeExecution

        calls = []
        adapter, provider = self._adapter(
            lambda prepared, timeout: calls.append(prepared) or b"{}"
        )
        common = {
            "credential": self._resolved(provider, self._context()),
            "credential_argument": credential_argument(),
            "context": self._context(),
        }

        with self.assertRaises(ValueError):
            adapter.preview_mutation(
                action="raw_endpoint",
                arguments={"market": "futures", "trading_mode": "live"},
                **common,
            )
        with self.assertRaises(ValueError):
            adapter.preview_mutation(
                action="batch_cancel",
                arguments={"market": "futures", "trading_mode": "demo", "order_ids": ["1"]},
                **common,
            )
        with self.assertRaises(ValueError):
            adapter.confirm_mutation(
                execution=TradeExecution(
                    action="batch_cancel",
                    market="futures",
                    trading_mode="live",
                    summary={
                        "action": "batch_cancel",
                        "market": "futures",
                        "trading_mode": "live",
                        "endpoint": "/arbitrary",
                        "request_body": {"orderIdList": ["1"]},
                    },
                    risk={"status": "review_required"},
                    risk_signature="risk",
                ),
                **common,
            )
        self.assertEqual(calls, [])

    def test_conditional_cancel_rejects_a_mismatched_returned_order_id(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps({"success": True, "orderId": "2"}).encode()
        )
        execution = TradeExecution(
            action="conditional_cancel",
            market="futures",
            trading_mode="live",
            summary={
                "action": "conditional_cancel",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/algoOrder",
                "request_query": {"orderId": "1"},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_close_position_rejects_a_mismatched_returned_position_id(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(
                [{"positionId": "8", "success": True, "successOrderId": "9"}]
            ).encode()
        )
        execution = TradeExecution(
            action="close_positions",
            market="futures",
            trading_mode="live",
            summary={
                "action": "close_positions",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/closePositions",
                "request_body": {"positionId": "7"},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_position_margin_success_requires_position_readback(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        def transport(prepared, timeout):
            if prepared["method"] == "GET":
                return json.dumps(
                    [{
                        "id": "7",
                        "symbol": "BTCUSDT",
                        "marginType": "ISOLATED",
                        "size": "0.1",
                        "isolatedMargin": "10",
                    }]
                ).encode()
            return json.dumps({"code": "0", "msg": "success"}).encode()

        adapter, provider = self._adapter(transport)
        execution = TradeExecution(
            action="position_margin",
            market="futures",
            trading_mode="live",
            summary={
                "action": "position_margin",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/account/positionMargin",
                "request_body": {"isolatedPositionId": "7", "amount": "1", "type": 1},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_auto_append_margin_success_requires_configuration_readback(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        def transport(prepared, timeout):
            if prepared["method"] == "GET":
                return json.dumps(
                    [{
                        "id": "7",
                        "symbol": "BTCUSDT",
                        "marginType": "ISOLATED",
                        "size": "0.1",
                        "isAutoAppendIsolatedMargin": False,
                    }]
                ).encode()
            return json.dumps({"code": "0", "msg": "success"}).encode()

        adapter, provider = self._adapter(transport)
        execution = TradeExecution(
            action="auto_append_margin",
            market="futures",
            trading_mode="live",
            summary={
                "action": "auto_append_margin",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/account/modifyAutoAppendMargin",
                "request_body": {"positionId": "7", "autoAppendMargin": True},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_position_margin_accepts_staging_200_and_confirms_readback(self):
        from weex_mcp.trade_execution import TradeExecution

        get_count = 0

        def transport(prepared, timeout):
            nonlocal get_count
            if prepared["method"] == "GET":
                get_count += 1
                margin = "10" if get_count == 1 else "11"
                return json.dumps(
                    [{
                        "id": "7",
                        "symbol": "BTCUSDT",
                        "marginType": "ISOLATED",
                        "size": "0.1",
                        "isolatedMargin": margin,
                    }]
                ).encode()
            return json.dumps({"code": "200", "msg": "success"}).encode()

        adapter, provider = self._adapter(transport)
        execution = TradeExecution(
            action="position_margin",
            market="futures",
            trading_mode="live",
            summary={
                "action": "position_margin",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/account/positionMargin",
                "request_body": {"isolatedPositionId": "7", "amount": "1", "type": 1},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        result = adapter.confirm_mutation(
            execution=execution,
            credential=self._resolved(provider, self._context()),
            credential_argument=credential_argument(),
            context=self._context(),
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["verification"]["isolated_margin_after"], "11")

    def test_auto_append_margin_accepts_staging_200_and_confirms_readback(self):
        from weex_mcp.trade_execution import TradeExecution

        get_count = 0

        def transport(prepared, timeout):
            nonlocal get_count
            if prepared["method"] == "GET":
                get_count += 1
                return json.dumps(
                    [{
                        "id": "7",
                        "symbol": "BTCUSDT",
                        "marginType": "ISOLATED",
                        "size": "0.1",
                        "isAutoAppendIsolatedMargin": get_count > 1,
                    }]
                ).encode()
            return json.dumps({"code": "200", "msg": "success"}).encode()

        adapter, provider = self._adapter(transport)
        execution = TradeExecution(
            action="auto_append_margin",
            market="futures",
            trading_mode="live",
            summary={
                "action": "auto_append_margin",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/account/modifyAutoAppendMargin",
                "request_body": {"positionId": "7", "autoAppendMargin": True},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        result = adapter.confirm_mutation(
            execution=execution,
            credential=self._resolved(provider, self._context()),
            credential_argument=credential_argument(),
            context=self._context(),
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["verification"]["auto_append_margin_after"])

    def test_closed_position_cannot_be_adjusted(self):
        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(
                [{
                    "id": "7",
                    "symbol": "BTCUSDT",
                    "marginType": "ISOLATED",
                    "size": "0",
                    "isolatedMargin": "10",
                }]
            ).encode()
        )

        with self.assertRaises(ValueError):
            adapter.preview_mutation(
                action="position_margin",
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "isolated_position_id": "7",
                    "amount": "1",
                    "adjustment": "increase",
                },
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_position_without_recognized_size_cannot_be_adjusted(self):
        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(
                [{
                    "id": "7",
                    "symbol": "BTCUSDT",
                    "marginType": "ISOLATED",
                    "isolatedMargin": "10",
                    "isAutoAppendIsolatedMargin": False,
                }]
            ).encode()
        )

        with self.assertRaises(ValueError):
            adapter.preview_mutation(
                action="auto_append_margin",
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "position_id": "7",
                    "enabled": True,
                },
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )

    def test_tp_sl_place_requires_exactly_one_determinate_result(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.trade_execution import TradeExecution

        adapter, provider = self._adapter(
            lambda prepared, timeout: json.dumps(
                [
                    {"success": True, "orderId": "1"},
                    {"success": True, "orderId": "2"},
                ]
            ).encode()
        )
        execution = TradeExecution(
            action="tp_sl",
            market="futures",
            trading_mode="live",
            summary={
                "action": "tp_sl",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/placeTpSlOrder",
                "request_body": {"clientAlgoId": "tp-1"},
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.confirm_mutation(
                execution=execution,
                credential=self._resolved(provider, self._context()),
                credential_argument=credential_argument(),
                context=self._context(),
            )


if __name__ == "__main__":
    unittest.main()
