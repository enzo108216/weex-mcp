from __future__ import annotations

import json
import unittest
from io import BytesIO
from urllib.error import HTTPError


class OutcomeAdapter:
    def __init__(self):
        self.confirm_result = {
            "success": True,
            "status": "accepted",
            "client_order_id": "outcome-order-1",
            "exchange_order_id": "exchange-1",
            "raw": {"credential": "MUST_NOT_ENTER_OUTCOME"},
        }
        self.confirm_exception = None
        self.resolve_exception = None

    def resolve_credential(self, *, credential_argument, context):
        from dataclasses import replace

        from weex_mcp.secret_provider import PlaintextSecretProvider

        if self.resolve_exception is not None:
            raise self.resolve_exception
        resolved = PlaintextSecretProvider().resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="TRADE",
        )
        return replace(resolved, permissions=frozenset(["TRADE"]))

    def validate_trade_access(self, *, arguments, credential, context):
        return credential

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
            "risk": {"status": "review_required"},
        }

    def confirm_order(self, *, execution, credential, credential_argument, context):
        if self.confirm_exception is not None:
            raise self.confirm_exception
        return dict(self.confirm_result)

    def preview_mutation(self, *, action, arguments, credential, credential_argument, context):
        assert action == "batch_order"
        return {
            "risk_signature": "batch-risk",
            "summary": {
                "action": action,
                "market": arguments["market"],
                "trading_mode": arguments["trading_mode"],
                "endpoint": "/capi/v3/batchOrders",
                "request_body": {
                    "batchOrders": [
                        {"newClientOrderId": order["client_order_id"]}
                        for order in arguments["orders"]
                    ]
                },
            },
            "risk": {"status": "review_required"},
        }

    def confirm_mutation(self, *, execution, credential, credential_argument, context):
        if self.confirm_exception is not None:
            raise self.confirm_exception
        return dict(self.confirm_result)


class TradeOutcomeWireTests(unittest.TestCase):
    def _credential(self):
        return {
            "type": "plaintext",
            "api_key": "AK",
            "api_secret": "SK",
            "api_passphrase": "PP",
        }

    def _order(self):
        return {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "outcome-order-1",
            "credential": self._credential(),
        }

    def _batch(self):
        return {
            "market": "futures",
            "trading_mode": "live",
            "orders": [
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": "0.001",
                    "client_order_id": "batch-a",
                },
                {
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "position_side": "LONG",
                    "order_type": "MARKET",
                    "quantity": "0.01",
                    "client_order_id": "batch-b",
                },
            ],
            "credential": self._credential(),
        }

    def _call(self, service, name, arguments):
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return response["result"]["structuredContent"]

    def _assert_outcome(self, payload, *, outcome_class, attempted, action):
        self.assertIn("outcome", payload)
        self.assertEqual(payload["outcome"]["class"], outcome_class)
        self.assertIs(payload["outcome"]["write_attempted"], attempted)
        self.assertEqual(payload["outcome"]["action"], action)

    def test_module_schema_and_token_failures_are_not_sent(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        disabled = self._call(
            build_default_service(trade_enabled=False, trade_adapter=adapter),
            "trade_confirm_order",
            {},
        )
        self._assert_outcome(disabled, outcome_class="not_sent", attempted=False, action="order")

        enabled = build_default_service(trade_enabled=True, trade_adapter=adapter)
        invalid_schema = self._call(enabled, "trade_confirm_order", {})
        self._assert_outcome(invalid_schema, outcome_class="not_sent", attempted=False, action="order")

        invalid_token = self._call(
            enabled,
            "trade_confirm_order",
            self._order()
            | {
                "preview_id": "not-a-valid-token",
                "confirmation": {"accepted": True},
            },
        )
        self._assert_outcome(invalid_token, outcome_class="not_sent", attempted=False, action="order")

    def test_accepted_order_and_unexpected_adapter_exception_have_conservative_outcomes(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        accepted = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(accepted, outcome_class="accepted", attempted=True, action="order")
        self.assertEqual(accepted["outcome"]["client_order_id"], "outcome-order-1")
        self.assertEqual(accepted["outcome"]["operation_reference"], "exchange-1")
        outcome_text = json.dumps(accepted["outcome"], sort_keys=True)
        self.assertNotIn("raw", accepted["outcome"])
        self.assertNotIn("MUST_NOT_ENTER_OUTCOME", outcome_text)
        self.assertNotIn("AK", outcome_text)

        adapter.confirm_exception = RuntimeError("unexpected adapter failure")
        preview = self._call(service, "trade_preview_order", arguments)
        unknown = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(unknown, outcome_class="unknown", attempted=True, action="order")

    def test_batch_partial_and_all_rejected_outcomes_are_per_leg(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._batch()

        adapter.confirm_result = {
            "success": True,
            "all_succeeded": False,
            "items": [
                {"ok": True, "order_id": "1", "client_order_id": "batch-a"},
                {"ok": False, "client_order_id": "batch-b", "error": {"code": "E", "message": "bad"}},
            ],
        }
        preview = self._call(service, "trade_preview_batch_order", arguments)
        partial = self._call(
            service,
            "trade_confirm_batch_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(partial, outcome_class="partial", attempted=True, action="batch_order")
        self.assertEqual([item["class"] for item in partial["outcome"]["items"]], ["accepted", "definitive_rejection"])
        self.assertEqual(partial["outcome"]["items"][1]["error_code"], "E")

        adapter.confirm_result = {
            "success": True,
            "all_succeeded": False,
            "items": [
                {"ok": False, "client_order_id": "batch-a", "error": {"code": "E1"}},
                {"ok": False, "client_order_id": "batch-b", "error": {"code": "E2"}},
            ],
        }
        preview = self._call(service, "trade_preview_batch_order", arguments)
        rejected = self._call(
            service,
            "trade_confirm_batch_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(rejected, outcome_class="definitive_rejection", attempted=True, action="batch_order")

    def test_valid_batch_early_and_global_failures_include_per_leg_outcomes(self):
        from weex_mcp.adapters.trade_guard import TradeMutationError
        from weex_mcp.secret_provider import SecretProviderError
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        arguments = self._batch()

        disabled = self._call(
            build_default_service(trade_enabled=False, trade_adapter=adapter),
            "trade_confirm_batch_order",
            arguments
            | {
                "preview_id": "not-checked-while-disabled",
                "confirmation": {"accepted": True},
            },
        )
        self._assert_outcome(disabled, outcome_class="not_sent", attempted=False, action="batch_order")
        self.assertIn("items", disabled["outcome"])
        self.assertEqual(len(disabled["outcome"]["items"]), 2)

        service = build_default_service(trade_enabled=True, trade_adapter=adapter)

        invalid_token = self._call(
            service,
            "trade_confirm_batch_order",
            arguments
            | {
                "preview_id": "invalid-token",
                "confirmation": {"accepted": True},
            },
        )
        self._assert_outcome(invalid_token, outcome_class="not_sent", attempted=False, action="batch_order")
        self.assertIn("items", invalid_token["outcome"])
        self.assertEqual(
            [item["client_order_id"] for item in invalid_token["outcome"]["items"]],
            ["batch-a", "batch-b"],
        )
        self.assertTrue(all(item["class"] == "not_sent" for item in invalid_token["outcome"]["items"]))

        preview = self._call(service, "trade_preview_batch_order", arguments)
        adapter.resolve_exception = SecretProviderError("credential_ref_not_found", "missing")
        provider_failure = self._call(
            service,
            "trade_confirm_batch_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(provider_failure, outcome_class="not_sent", attempted=False, action="batch_order")
        self.assertEqual(len(provider_failure["outcome"]["items"]), 2)
        adapter.resolve_exception = None

        for outcome_class, exception in (
            (
                "definitive_rejection",
                TradeMutationError(
                    code="upstream_error",
                    message="rejected",
                    outcome_class="definitive_rejection",
                    write_attempted=True,
                    details={"upstream_code": "-1001"},
                ),
            ),
            ("unknown", RuntimeError("uncertain")),
        ):
            with self.subTest(outcome_class=outcome_class):
                preview = self._call(service, "trade_preview_batch_order", arguments)
                adapter.confirm_exception = exception
                result = self._call(
                    service,
                    "trade_confirm_batch_order",
                    arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
                )
                self._assert_outcome(result, outcome_class=outcome_class, attempted=True, action="batch_order")
                self.assertIn("items", result["outcome"])
                self.assertEqual(len(result["outcome"]["items"]), 2)
                self.assertTrue(all(item["class"] == outcome_class for item in result["outcome"]["items"]))
                adapter.confirm_exception = None

    def test_service_pre_gate_invalid_batch_identity_stays_not_sent_without_items(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(trade_enabled=False, trade_adapter=OutcomeAdapter())
        for invalid_identity in ("contains space", "x" * 129, "question?mark"):
            with self.subTest(invalid_identity=invalid_identity):
                arguments = self._batch()
                arguments["orders"][0]["client_order_id"] = invalid_identity
                result = self._call(
                    service,
                    "trade_confirm_batch_order",
                    arguments
                    | {
                        "preview_id": "not-checked-while-disabled",
                        "confirmation": {"accepted": True},
                    },
                )

                self._assert_outcome(result, outcome_class="not_sent", attempted=False, action="batch_order")
                self.assertNotIn("items", result["outcome"])

    def test_batch_post_dispatch_requires_complete_items_for_releasable_aggregate(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._batch()
        cases = (
            {"success": True, "_outcome": {"class": "accepted", "write_attempted": True, "action": "batch_order"}},
            {"success": True, "_outcome": {"class": "partial", "write_attempted": True, "action": "batch_order"}},
            {
                "success": True,
                "_outcome": {
                    "class": "definitive_rejection",
                    "write_attempted": True,
                    "action": "batch_order",
                },
            },
            {"success": True},
            {"success": True, "_outcome": {"class": "unknown", "write_attempted": True, "action": "batch_order"}},
        )
        for confirm_result in cases:
            with self.subTest(confirm_result=confirm_result):
                adapter.confirm_result = confirm_result
                preview = self._call(service, "trade_preview_batch_order", arguments)
                result = self._call(
                    service,
                    "trade_confirm_batch_order",
                    arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
                )

                self._assert_outcome(result, outcome_class="unknown", attempted=True, action="batch_order")
                self.assertNotIn("items", result["outcome"])

    def test_post_confirm_value_error_is_unknown_not_service_not_sent(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        adapter.confirm_exception = ValueError("post-confirm validation failed")

        result = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(result, outcome_class="unknown", attempted=True, action="order")

    def test_post_confirm_secret_provider_error_is_unknown(self):
        from weex_mcp.secret_provider import SecretProviderError
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        adapter.confirm_exception = SecretProviderError("credential_ref_not_found", "post-confirm provider failure")

        result = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(result, outcome_class="unknown", attempted=True, action="order")

    def test_malicious_typed_error_details_and_invalid_adapter_outcome_collapse_safely(self):
        from weex_mcp.adapters.trade_guard import TradeMutationError
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        adapter.confirm_exception = TradeMutationError(
            code="upstream_error",
            message="unsafe",
            outcome_class="partial",
            write_attempted="yes",
            details={
                "upstream_code": "E",
                "raw": {"api_secret": "MUST_NOT_ESCAPE"},
                "credential": {"api_key": "MUST_NOT_ESCAPE"},
                "arbitrary": {"nested": "MUST_NOT_ESCAPE"},
                "items": [{"class": "partial", "raw": "MUST_NOT_ESCAPE"}],
            },
        )
        try:
            result = self._call(
                service,
                "trade_confirm_order",
                arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
            )
        except Exception as exc:
            self.fail(f"malicious typed error broke the service: {exc}")
        self._assert_outcome(result, outcome_class="unknown", attempted=True, action="order")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("MUST_NOT_ESCAPE", serialized)
        self.assertEqual(set(result["error"].get("details", {})), {"upstream_code"})
        self.assertNotIn("items", result["outcome"])

        adapter.confirm_exception = None
        adapter.confirm_result = {
            "success": True,
            "_outcome": {
                "class": "accepted",
                "write_attempted": True,
                "action": "cancel_order",
                "client_order_id": "outcome-order-1",
            },
        }
        preview = self._call(service, "trade_preview_order", arguments)
        invalid_adapter_outcome = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self._assert_outcome(invalid_adapter_outcome, outcome_class="unknown", attempted=True, action="order")
        self.assertNotIn("items", invalid_adapter_outcome["outcome"])

    def test_adapter_supplied_outcome_is_projected_without_raw_or_credentials(self):
        from weex_mcp.service import build_default_service

        adapter = OutcomeAdapter()
        adapter.confirm_result = {
            "success": True,
            "_outcome": {
                "class": "accepted",
                "write_attempted": True,
                "action": "order",
                "client_order_id": "outcome-order-1",
                "raw": {"api_secret": "MUST_NOT_ESCAPE"},
                "credential": {"api_key": "MUST_NOT_ESCAPE"},
            },
        }
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )

        self._assert_outcome(confirmed, outcome_class="accepted", attempted=True, action="order")
        self.assertNotIn("raw", confirmed["outcome"])
        self.assertNotIn("credential", confirmed["outcome"])
        self.assertNotIn("MUST_NOT_ESCAPE", json.dumps(confirmed["outcome"], sort_keys=True))

    def test_output_schema_declares_safe_outcome_shape(self):
        from weex_mcp.tool_registry import build_default_registry

        output = build_default_registry().get_tool("trade_confirm_order").to_mcp_tool()["outputSchema"]
        self.assertIn("outcome", output["properties"])
        outcome = output["properties"]["outcome"]
        self.assertEqual(set(outcome["required"]), {"class", "write_attempted", "action"})
        self.assertEqual(
            set(outcome["properties"]["class"]["enum"]),
            {"not_sent", "definitive_rejection", "accepted", "unknown", "partial"},
        )
        self.assertNotIn("raw", outcome["properties"])
        self.assertNotIn("credential", outcome["properties"])
        item_schema = outcome["properties"]["items"]["items"]
        self.assertIn("minItems", outcome["properties"]["items"])
        self.assertEqual(outcome["properties"]["items"]["minItems"], 1)
        self.assertIn("required", item_schema)
        self.assertIn("properties", item_schema)
        self.assertIn("additionalProperties", item_schema)
        self.assertEqual(set(item_schema["required"]), {"class", "write_attempted", "action"})
        self.assertEqual(
            set(item_schema["properties"]["class"]["enum"]),
            {"not_sent", "definitive_rejection", "accepted", "unknown"},
        )
        self.assertFalse(item_schema["additionalProperties"])
        self.assertNotIn("partial", item_schema["properties"]["class"]["enum"])
        self.assertIn("anyOf", item_schema)
        self.assertEqual(
            item_schema["anyOf"],
            [
                {"required": ["client_order_id"]},
                {"required": ["operation_reference"]},
                {"required": ["reconcile_identity"]},
            ],
        )
        self.assertEqual(item_schema["properties"]["error_code"]["pattern"], r"^[A-Za-z0-9_.:-]{1,64}$")

    def test_invalid_outcome_attempt_and_empty_partial_items_collapse_to_unknown(self):
        from weex_mcp.adapters.trade_guard import mutation_outcome

        invalid_attempt = mutation_outcome(
            outcome_class="unknown",
            write_attempted=False,
            action="order",
        )
        self.assertEqual(invalid_attempt, {"class": "unknown", "write_attempted": True, "action": "order"})

        empty_partial = mutation_outcome(
            outcome_class="partial",
            write_attempted=True,
            action="batch_order",
            items=[],
        )
        self.assertEqual(empty_partial, {"class": "unknown", "write_attempted": True, "action": "batch_order"})

    def test_item_identity_uniqueness_aggregate_consistency_and_error_code_safety(self):
        from weex_mcp.adapters.trade_guard import mutation_outcome

        accepted_item = {
            "class": "accepted",
            "write_attempted": True,
            "action": "batch_order",
            "client_order_id": "a",
        }
        rejected_item = {
            "class": "definitive_rejection",
            "write_attempted": True,
            "action": "batch_order",
            "client_order_id": "b",
            "error_code": "contains secret spaces",
        }
        missing_identity = mutation_outcome(
            outcome_class="accepted",
            write_attempted=True,
            action="batch_order",
            items=[{"class": "accepted", "write_attempted": True, "action": "batch_order"}],
        )
        self.assertEqual(missing_identity, {"class": "unknown", "write_attempted": True, "action": "batch_order"})

        duplicate_identity = mutation_outcome(
            outcome_class="accepted",
            write_attempted=True,
            action="batch_order",
            items=[accepted_item, dict(accepted_item)],
        )
        self.assertEqual(duplicate_identity, {"class": "unknown", "write_attempted": True, "action": "batch_order"})

        inconsistent = mutation_outcome(
            outcome_class="accepted",
            write_attempted=True,
            action="batch_order",
            items=[accepted_item, rejected_item],
        )
        self.assertEqual(inconsistent, {"class": "unknown", "write_attempted": True, "action": "batch_order"})

        invalid_top = mutation_outcome(
            outcome_class="not-a-class",
            write_attempted=True,
            action="batch_order",
            items=[accepted_item],
        )
        self.assertEqual(invalid_top, {"class": "unknown", "write_attempted": True, "action": "batch_order"})

        partial = mutation_outcome(
            outcome_class="partial",
            write_attempted=True,
            action="batch_order",
            items=[accepted_item, rejected_item],
        )
        self.assertEqual(partial["class"], "partial")
        self.assertEqual(partial["items"][1]["error_code"], "upstream_error")


class TradeRestOutcomeTests(unittest.TestCase):
    def _capture_mutation_error(self, call):
        from weex_mcp.adapters.trade_guard import TradeMutationError

        try:
            call()
        except Exception as exc:
            self.assertIsInstance(exc, TradeMutationError, f"expected typed mutation error, got {type(exc).__name__}")
            return exc
        self.fail("expected a typed mutation error")

    def _components(self, transport):
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import StaticMappingSecretProvider
        from weex_mcp.tenant_context import TenantContext

        provider = StaticMappingSecretProvider(
            {
                "profile": {
                    "api_key": "AK",
                    "api_secret": "SK",
                    "api_passphrase": "PP",
                    "permissions": ["TRADE"],
                }
            }
        )
        context = TenantContext(tenant_id="tenant", user_id="user", session_id="session")
        credential_argument = {"type": "credential_ref", "key_id": "key", "ref": "profile"}
        resolved = provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="TRADE",
        )
        adapter = WeexTradeRestAdapter(secret_provider=provider, transport=transport)
        return adapter, resolved, credential_argument, context

    def _order_execution(self):
        from weex_mcp.trade_execution import TradeExecution

        return TradeExecution(
            action="order",
            market="futures",
            trading_mode="live",
            summary={
                "action": "order",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/order",
                "request_body": {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.001",
                    "newClientOrderId": "rest-order-1",
                },
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

    def _confirm_order(self, transport):
        adapter, resolved, credential_argument, context = self._components(transport)
        return adapter.confirm_order(
            execution=self._order_execution(),
            credential=resolved,
            credential_argument=credential_argument,
            context=context,
        )

    def test_order_mutation_classifies_rejection_and_uncertain_transport_failures(self):
        def http_error(status, body=b'{}'):
            return HTTPError(
                "https://api-contract.weex.com/capi/v3/order",
                status,
                "error",
                hdrs=None,
                fp=BytesIO(body),
            )

        cases = (
            (lambda _prepared, _timeout: b'{"code":"-1001","msg":"rejected"}', "definitive_rejection"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(TimeoutError("timeout")), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(408)), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(500)), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(400, b'{"code":"-1002"}')), "definitive_rejection"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(409, b'')), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(425, b'not-json')), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(429, b'{}')), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(409, b'{"code":"-1050"}')), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(425, b'{"code":"-1050"}')), "unknown"),
            (lambda _prepared, _timeout: (_ for _ in ()).throw(http_error(429, b'{"code":"-1050"}')), "unknown"),
            (lambda _prepared, _timeout: b'not-json', "unknown"),
            (
                lambda _prepared, _timeout: b'{"success":true,"clientOrderId":"rest-order-1"}',
                "unknown",
            ),
        )
        for transport, expected_class in cases:
            with self.subTest(expected_class=expected_class, transport=transport):
                raised = self._capture_mutation_error(
                    lambda transport=transport: self._confirm_order(transport)
                )
                self.assertEqual(raised.outcome_class, expected_class)
                self.assertIs(raised.write_attempted, True)
                self.assertNotIn("AK", json.dumps(raised.details, sort_keys=True))

    def test_actual_adapter_pre_transport_validation_is_typed_not_sent(self):
        from weex_mcp.trade_execution import TradeExecution

        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: self.fail("pre-transport validation invoked mutation transport")
        )
        invalid_execution = TradeExecution(
            action="order",
            market="futures",
            trading_mode="live",
            summary={
                "action": "order",
                "market": "futures",
                "trading_mode": "live",
                "endpoint": "/capi/v3/not-an-order-endpoint",
                "request_body": {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.001",
                    "newClientOrderId": "rest-order-1",
                },
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )

        error = self._capture_mutation_error(
            lambda: adapter.confirm_order(
                execution=invalid_execution,
                credential=resolved,
                credential_argument=credential_argument,
                context=context,
            )
        )
        self.assertEqual(error.outcome_class, "not_sent")
        self.assertIs(error.write_attempted, False)

    def test_accepted_order_and_non_order_mutation_return_safe_internal_outcomes(self):
        order = self._confirm_order(
            lambda _prepared, _timeout: b'{"success":true,"orderId":"42","clientOrderId":"rest-order-1"}'
        )
        self.assertEqual(order["_outcome"]["class"], "accepted")
        self.assertEqual(order["_outcome"]["client_order_id"], "rest-order-1")
        self.assertEqual(order["_outcome"]["operation_reference"], "42")
        self.assertNotIn("raw", order["_outcome"])

        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "marginType": "CROSSED",
                    "crossLeverage": "5",
                    "success": True,
                }
            ).encode()
        )
        from weex_mcp.trade_execution import TradeExecution

        leverage = adapter.confirm_leverage(
            execution=TradeExecution(
                action="update_leverage",
                market="futures",
                trading_mode="live",
                summary={
                    "action": "update_leverage",
                    "market": "futures",
                    "trading_mode": "live",
                    "endpoint": "/capi/v3/account/leverage",
                    "request_body": {
                        "symbol": "BTCUSDT",
                        "marginType": "CROSSED",
                        "crossLeverage": "5",
                    },
                },
                risk={"status": "review_required"},
                risk_signature="risk",
            ),
            credential=resolved,
            credential_argument=credential_argument,
            context=context,
        )
        self.assertEqual(leverage["_outcome"]["class"], "accepted")
        self.assertNotIn("client_order_id", leverage["_outcome"])
        self.assertNotIn("operation_reference", leverage["_outcome"])

    def test_position_pre_read_is_not_sent_but_post_read_failure_is_unknown(self):
        from weex_mcp.trade_execution import TradeExecution

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

        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: b'{"invalid":"positions"}'
        )
        pre_read = self._capture_mutation_error(
            lambda: adapter.confirm_mutation(
                execution=execution,
                credential=resolved,
                credential_argument=credential_argument,
                context=context,
            )
        )
        self.assertEqual(pre_read.outcome_class, "not_sent")
        self.assertIs(pre_read.write_attempted, False)

        responses = iter(
            (
                b'[{"id":"7","marginType":"ISOLATED","size":"1","isolatedMargin":"10"}]',
                b'{"code":"0","msg":"success"}',
                b'{"invalid":"positions"}',
            )
        )
        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: next(responses)
        )
        post_read = self._capture_mutation_error(
            lambda: adapter.confirm_mutation(
                execution=execution,
                credential=resolved,
                credential_argument=credential_argument,
                context=context,
            )
        )
        self.assertEqual(post_read.outcome_class, "unknown")
        self.assertIs(post_read.write_attempted, True)

        responses = iter(
            (
                b'[{"id":"7","marginType":"ISOLATED","size":"1","isolatedMargin":"10"}]',
                b'{"code":"0","msg":"success"}',
                b'{"code":"-1050","msg":"post-read auth/rate failure"}',
            )
        )
        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: next(responses)
        )
        post_read_business = self._capture_mutation_error(
            lambda: adapter.confirm_mutation(
                execution=execution,
                credential=resolved,
                credential_argument=credential_argument,
                context=context,
            )
        )
        self.assertEqual(post_read_business.outcome_class, "unknown")
        self.assertIs(post_read_business.write_attempted, True)

    def test_post_mutation_normalization_never_infers_rejection_from_untyped_business_error(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.adapters.trade_rest import _post_mutation_error

        error = _post_mutation_error(
            WeexRestBusinessError(code="-1050", message="post-normalization business error"),
            unknown_message="unknown",
            details={"operation_reference": "position-1"},
        )
        self.assertEqual(error.outcome_class, "unknown")
        self.assertIs(error.write_attempted, True)

    def test_batch_adapter_outcomes_are_per_leg_and_unreliable_mapping_is_unknown(self):
        from weex_mcp.trade_execution import TradeExecution

        execution = TradeExecution(
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
                        {"newClientOrderId": "batch-a"},
                        {"newClientOrderId": "batch-b"},
                    ]
                },
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )
        responses = iter(
            (
                json.dumps(
                    [
                        {"orderId": "1", "clientOrderId": "batch-a", "success": True},
                        {"clientOrderId": "batch-b", "success": False, "errorCode": "E"},
                    ]
                ).encode(),
                json.dumps(
                    [
                        {"clientOrderId": "batch-a", "success": False, "errorCode": "E1"},
                        {"clientOrderId": "batch-b", "success": False, "errorCode": "E2"},
                    ]
                ).encode(),
                json.dumps([{"orderId": "1", "clientOrderId": "unrelated", "success": True}]).encode(),
            )
        )
        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: next(responses)
        )
        common = {
            "execution": execution,
            "credential": resolved,
            "credential_argument": credential_argument,
            "context": context,
        }

        partial = adapter.confirm_mutation(**common)
        self.assertEqual(partial["_outcome"]["class"], "partial")
        self.assertEqual(
            [item["class"] for item in partial["_outcome"]["items"]],
            ["accepted", "definitive_rejection"],
        )
        rejected = adapter.confirm_mutation(**common)
        self.assertEqual(rejected["_outcome"]["class"], "definitive_rejection")

        unreliable = self._capture_mutation_error(lambda: adapter.confirm_mutation(**common))
        self.assertEqual(unreliable.outcome_class, "unknown")
        self.assertNotIn("items", unreliable.details)

    def test_batch_identity_mapping_supports_reordering_and_rejects_missing_or_duplicate_identity(self):
        from weex_mcp.trade_execution import TradeExecution

        execution = TradeExecution(
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
                        {"newClientOrderId": "batch-a"},
                        {"newClientOrderId": "batch-b"},
                    ]
                },
            },
            risk={"status": "review_required"},
            risk_signature="risk",
        )
        responses = iter(
            (
                json.dumps(
                    [
                        {"orderId": "2", "clientOrderId": "batch-b", "success": True},
                        {"clientOrderId": "batch-a", "success": False, "errorCode": "E"},
                    ]
                ).encode(),
                json.dumps(
                    [
                        {"orderId": "1", "success": True},
                        {"orderId": "2", "clientOrderId": "batch-b", "success": True},
                    ]
                ).encode(),
                json.dumps(
                    [
                        {"orderId": "1", "clientOrderId": "batch-a", "success": True},
                        {"orderId": "2", "clientOrderId": "batch-a", "success": True},
                    ]
                ).encode(),
            )
        )
        adapter, resolved, credential_argument, context = self._components(
            lambda _prepared, _timeout: next(responses)
        )
        common = {
            "execution": execution,
            "credential": resolved,
            "credential_argument": credential_argument,
            "context": context,
        }

        try:
            reordered = adapter.confirm_mutation(**common)
        except Exception as exc:
            self.fail(f"reordered batch response was not mapped by identity: {exc}")
        self.assertEqual(
            [item["client_order_id"] for item in reordered["_outcome"]["items"]],
            ["batch-a", "batch-b"],
        )
        self.assertEqual(
            [item["class"] for item in reordered["_outcome"]["items"]],
            ["definitive_rejection", "accepted"],
        )
        for _case in ("missing", "duplicate"):
            with self.subTest(case=_case):
                error = self._capture_mutation_error(lambda: adapter.confirm_mutation(**common))
                self.assertEqual(error.outcome_class, "unknown")
                self.assertNotIn("items", error.details)


if __name__ == "__main__":
    unittest.main()
