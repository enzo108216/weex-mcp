import unittest


class StatelessTradeAdapter:
    def __init__(self):
        self.preview_calls = 0
        self.execute_calls = 0
        self.preview_actions = []
        self.execute_actions = []

    def resolve_credential(self, *, credential_argument, context):
        from dataclasses import replace

        from weex_mcp.secret_provider import PlaintextSecretProvider

        resolved = PlaintextSecretProvider().resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="TRADE",
        )
        return replace(resolved, permissions=frozenset(["TRADE"]))

    def _preview(self, action, arguments, *, binding_name, binding):
        self.preview_calls += 1
        self.preview_actions.append(action)
        return {
            "risk_signature": f"{action}-risk",
            "summary": {
                "action": action,
                "market": arguments["market"],
                "trading_mode": arguments["trading_mode"],
                "endpoint": f"/test/{action}",
                binding_name: binding,
            },
            "risk": {"status": "review_required", "alerts": [action]},
        }

    def _confirm(self, action):
        self.execute_calls += 1
        self.execute_actions.append(action)
        return {"success": True, "status": "accepted", "action": action}

    def preview_order(self, *, arguments, credential, credential_argument, context):
        body = {
            "symbol": arguments["symbol"],
            "side": arguments["side"],
            "type": arguments["order_type"],
            "quantity": arguments["quantity"],
            "newClientOrderId": arguments["client_order_id"],
        }
        return self._preview("order", arguments, binding_name="request_body", binding=body)

    def confirm_order(self, *, execution, credential, credential_argument, context):
        result = self._confirm("order")
        result["client_order_id"] = execution.summary["request_body"]["newClientOrderId"]
        return result

    def preview_cancel_order(self, *, arguments, credential, credential_argument, context):
        query = {}
        if arguments.get("order_id"):
            query["orderId"] = arguments["order_id"]
        if arguments.get("client_order_id"):
            query["origClientOrderId"] = arguments["client_order_id"]
        return self._preview("cancel_order", arguments, binding_name="request_query", binding=query)

    def confirm_cancel_order(self, *, execution, credential, credential_argument, context):
        return self._confirm("cancel_order")

    def preview_leverage(self, *, arguments, credential, credential_argument, context):
        explicit_fields = {
            "cross_leverage": "crossLeverage",
            "isolated_long_leverage": "isolatedLongLeverage",
            "isolated_short_leverage": "isolatedShortLeverage",
        }
        if any(field in arguments for field in explicit_fields):
            if "leverage" in arguments or "position_side" in arguments:
                raise ValueError(
                    "Explicit leverage fields cannot be combined with leverage or position_side."
                )
            body = {"symbol": arguments["symbol"]}
            if "margin_mode" in arguments:
                body["marginType"] = arguments["margin_mode"]
            for source, target in explicit_fields.items():
                if source in arguments:
                    body[target] = arguments[source]
            result = self._preview(
                "update_leverage", arguments, binding_name="request_body", binding=body
            )
            result["summary"]["leverage_mode"] = "explicit_atomic"
            return result
        body = {
            "symbol": arguments["symbol"],
            "marginType": arguments["margin_mode"],
            "leverage": arguments["leverage"],
        }
        return self._preview("update_leverage", arguments, binding_name="request_body", binding=body)

    def confirm_leverage(self, *, execution, credential, credential_argument, context):
        return self._confirm("update_leverage")

    def preview_margin_mode(self, *, arguments, credential, credential_argument, context):
        body = {"symbol": arguments["symbol"], "marginType": arguments["margin_mode"]}
        return self._preview("change_margin_mode", arguments, binding_name="request_body", binding=body)

    def confirm_margin_mode(self, *, execution, credential, credential_argument, context):
        return self._confirm("change_margin_mode")


class StatelessTradeTests(unittest.TestCase):
    def _credential(self, api_key="AK_ONE", api_secret="SK_INLINE", api_passphrase="PP_INLINE"):
        return {
            "type": "plaintext",
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
        }

    def _order(self):
        return {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "1",
            "client_order_id": "stateless-order-1",
            "credential": self._credential(),
        }

    def _call(self, service, name, arguments):
        return service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )["result"]

    def test_service_has_no_preview_or_idempotency_store(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(trade_enabled=True, trade_adapter=StatelessTradeAdapter())

        self.assertFalse(hasattr(service, "preview_store"))
        self.assertFalse(hasattr(service, "idempotency_store"))

    def test_preview_missing_fields_returns_concatenated_clarification_prompt(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        arguments = self._order()
        del arguments["symbol"]
        del arguments["quantity"]

        result = self._call(
            build_default_service(trade_enabled=True, trade_adapter=adapter),
            "trade_preview_order",
            arguments,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_fields")
        self.assertEqual(result["error"]["details"]["fields"], ["symbol", "quantity"])
        self.assertIs(result["clarification_required"], True)
        self.assertEqual(result["clarification_prompt"], "Please provide the following missing fields: symbol, quantity.")
        self.assertEqual(adapter.preview_calls, 0)

    def test_confirm_missing_fields_does_not_return_preview_clarification(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        del arguments["quantity"]

        result = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_fields")
        self.assertNotIn("clarification_required", result)
        self.assertNotIn("clarification_prompt", result)
        self.assertEqual(adapter.execute_calls, 0)

    def test_cancel_preview_missing_identifier_returns_clarification_prompt(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        result = self._call(
            build_default_service(trade_enabled=True, trade_adapter=adapter),
            "trade_preview_cancel_order",
            {
                "market": "spot",
                "trading_mode": "live",
                "credential": self._credential(),
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["details"]["fields"], ["order_id", "client_order_id"])
        self.assertIs(result["clarification_required"], True)
        self.assertEqual(
            result["clarification_prompt"],
            "Please provide the following missing fields: order_id, client_order_id.",
        )
        self.assertEqual(adapter.preview_calls, 0)

    def test_confirm_rechecks_full_order_before_execution(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        self.assertTrue(preview["ok"])
        self.assertIn("preview_id", preview)
        self.assertNotIn("approval_token", preview)
        self.assertIs(preview["user_confirmation"]["accepted"], True)
        self.assertNotIn("reply_text", preview["user_confirmation"])

        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(adapter.preview_calls, 2)
        self.assertEqual(adapter.execute_calls, 1)

    def test_confirm_temporarily_accepts_legacy_approval_token_alias(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)

        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "approval_token": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(adapter.execute_calls, 1)

    def test_confirm_rejects_both_confirmation_credential_aliases(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)

        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "approval_token": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )

        self.assertFalse(confirmed["ok"])
        self.assertEqual(confirmed["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.preview_calls, 1)
        self.assertEqual(adapter.execute_calls, 0)

    def test_parameter_or_credential_change_rejects_before_execution(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)

        changed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "quantity": "2",
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.execute_calls, 0)

        changed_credential = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "credential": self._credential("AK_TWO"),
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(changed_credential["ok"])
        self.assertEqual(changed_credential["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.execute_calls, 0)

        for credential in (
            self._credential(api_secret="SK_CHANGED"),
            self._credential(api_passphrase="PP_CHANGED"),
        ):
            with self.subTest(credential=credential):
                changed_credential_version = self._call(
                    service,
                    "trade_confirm_order",
                    arguments
                    | {
                        "credential": credential,
                        "preview_id": preview["preview_id"],
                        "confirmation": {"accepted": True},
                    },
                )
                self.assertFalse(changed_credential_version["ok"])
                self.assertEqual(changed_credential_version["error"]["code"], "approval_mismatch")
                self.assertEqual(adapter.execute_calls, 0)

    def test_approval_binding_allows_same_route_and_rejects_host_or_route_change(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        settings = Settings(trade_enabled=True, approval_token_secret="s" * 32)
        local_binding = {"host_kind": "local_user", "route_fingerprint": "fp_route_a"}
        try:
            preview_service = build_default_service(
                settings=settings,
                approval_binding=local_binding,
                trade_adapter=StatelessTradeAdapter(),
            )
        except TypeError as exc:
            self.fail(f"service approval binding support is missing: {exc}")
        matching_adapter = StatelessTradeAdapter()
        matching_service = build_default_service(
            settings=settings,
            approval_binding={"route_fingerprint": "fp_route_a", "host_kind": "local_user"},
            trade_adapter=matching_adapter,
        )
        arguments = self._order()
        preview = self._call(preview_service, "trade_preview_order", arguments)

        confirmed = self._call(
            matching_service,
            "trade_confirm_order",
            arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
        )
        self.assertTrue(confirmed["ok"])
        self.assertEqual(matching_adapter.execute_calls, 1)

        for binding in (
            {"host_kind": "broker_server", "route_fingerprint": "fp_route_a"},
            {"host_kind": "local_user", "route_fingerprint": "fp_route_b"},
        ):
            with self.subTest(binding=binding):
                rejected_adapter = StatelessTradeAdapter()
                rejected_service = build_default_service(
                    settings=settings,
                    approval_binding=binding,
                    trade_adapter=rejected_adapter,
                )
                rejected = self._call(
                    rejected_service,
                    "trade_confirm_order",
                    arguments | {"preview_id": preview["preview_id"], "confirmation": {"accepted": True}},
                )
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["error"]["code"], "approval_token_invalid")
                self.assertEqual(rejected_adapter.preview_calls, 0)
                self.assertEqual(rejected_adapter.execute_calls, 0)

    def test_shared_secret_allows_cross_instance_confirm_and_rotation_fails_closed(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        shared_settings = Settings(trade_enabled=True, approval_token_secret="s" * 32)
        preview_adapter = StatelessTradeAdapter()
        confirm_adapter = StatelessTradeAdapter()
        preview_service = build_default_service(settings=shared_settings, trade_adapter=preview_adapter)
        confirm_service = build_default_service(settings=shared_settings, trade_adapter=confirm_adapter)
        arguments = self._order()

        preview = self._call(preview_service, "trade_preview_order", arguments)
        confirmed = self._call(
            confirm_service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(preview_adapter.execute_calls, 0)
        self.assertEqual(confirm_adapter.execute_calls, 1)

        rotated_adapter = StatelessTradeAdapter()
        rotated_service = build_default_service(
            settings=Settings(trade_enabled=True, approval_token_secret="r" * 32),
            trade_adapter=rotated_adapter,
        )
        rejected = self._call(
            rotated_service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"]["code"], "approval_token_invalid")
        self.assertEqual(rotated_adapter.preview_calls, 0)
        self.assertEqual(rotated_adapter.execute_calls, 0)

    def test_all_four_actions_are_fully_rechecked_before_execution(self):
        from weex_mcp.service import build_default_service

        cases = (
            ("order", "trade_preview_order", "trade_confirm_order", self._order()),
            (
                "cancel_order",
                "trade_preview_cancel_order",
                "trade_confirm_cancel_order",
                {"market": "spot", "trading_mode": "live", "order_id": "123", "credential": self._credential()},
            ),
            (
                "update_leverage",
                "trade_preview_leverage",
                "trade_confirm_leverage",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "margin_mode": "cross",
                    "leverage": "5",
                    "credential": self._credential(),
                },
            ),
            (
                "change_margin_mode",
                "trade_preview_margin_mode",
                "trade_confirm_margin_mode",
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "margin_mode": "isolated",
                    "credential": self._credential(),
                },
            ),
        )
        for action, preview_name, confirm_name, arguments in cases:
            with self.subTest(action=action):
                adapter = StatelessTradeAdapter()
                service = build_default_service(trade_enabled=True, trade_adapter=adapter)
                preview = self._call(service, preview_name, arguments)
                confirmed = self._call(
                    service,
                    confirm_name,
                    arguments
                    | {
                        "preview_id": preview["preview_id"],
                        "confirmation": {"accepted": True},
                    },
                )
                self.assertTrue(confirmed["ok"])
                self.assertEqual(adapter.preview_actions, [action, action])
                self.assertEqual(adapter.execute_actions, [action])

    def test_explicit_atomic_leverage_preview_token_binds_every_field(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "cross_leverage": "2",
            "isolated_long_leverage": "3",
            "credential": self._credential(),
        }
        preview = self._call(service, "trade_preview_leverage", arguments)
        self.assertTrue(preview["ok"])

        tampered = self._call(
            service,
            "trade_confirm_leverage",
            arguments
            | {
                "isolated_long_leverage": "4",
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(tampered["ok"])
        self.assertEqual(tampered["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.execute_calls, 0)

    def test_leverage_missing_fields_are_deferred_to_preview_and_confirm_tools(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        base = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "credential": self._credential(),
        }
        missing_preview = self._call(service, "trade_preview_leverage", base)
        self.assertFalse(missing_preview["ok"])
        self.assertEqual(missing_preview["error"]["code"], "missing_fields")
        self.assertEqual(
            missing_preview["error"]["details"]["fields"],
            ["margin_mode", "leverage"],
        )
        self.assertTrue(missing_preview["clarification_required"])
        self.assertEqual(adapter.preview_calls, 0)

        valid = base | {"margin_mode": "cross", "leverage": "5"}
        preview = self._call(service, "trade_preview_leverage", valid)
        self.assertTrue(preview["ok"])
        missing_confirm = self._call(
            service,
            "trade_confirm_leverage",
            base
            | {
                "margin_mode": "cross",
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(missing_confirm["ok"])
        self.assertEqual(missing_confirm["error"]["code"], "missing_fields")
        self.assertEqual(missing_confirm["error"]["details"]["fields"], ["leverage"])
        self.assertEqual(missing_confirm["outcome"]["class"], "not_sent")
        self.assertFalse(missing_confirm["outcome"]["write_attempted"])
        self.assertEqual(adapter.execute_calls, 0)

    def test_leverage_mixed_modes_reach_adapter_validation_without_mutation(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        base = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "cross_leverage": "2",
            "credential": self._credential(),
        }
        mixed_preview = self._call(
            service,
            "trade_preview_leverage",
            base | {"leverage": "5"},
        )
        self.assertFalse(mixed_preview["ok"])
        self.assertEqual(mixed_preview["error"]["code"], "invalid_arguments")
        self.assertEqual(
            mixed_preview["error"]["message"],
            "Explicit leverage fields cannot be combined with leverage or position_side.",
        )

        preview = self._call(service, "trade_preview_leverage", base)
        mixed_confirm = self._call(
            service,
            "trade_confirm_leverage",
            base
            | {
                "leverage": "5",
                "preview_id": preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertFalse(mixed_confirm["ok"])
        self.assertEqual(mixed_confirm["error"]["code"], "invalid_arguments")
        self.assertEqual(
            mixed_confirm["error"]["message"],
            "Explicit leverage fields cannot be combined with leverage or position_side.",
        )
        self.assertEqual(mixed_confirm["outcome"]["class"], "not_sent")
        self.assertEqual(adapter.execute_calls, 0)

    def test_wrong_action_tampering_and_invalid_confirmation_arguments_fail_before_execution(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        order = self._order()
        order_preview = self._call(service, "trade_preview_order", order)
        cancel = {"market": "spot", "trading_mode": "live", "order_id": "123", "credential": self._credential()}
        cancel_preview = self._call(service, "trade_preview_cancel_order", cancel)

        wrong_action = self._call(
            service,
            "trade_confirm_order",
            order
            | {
                "preview_id": cancel_preview["preview_id"],
                "confirmation": {"accepted": True},
            },
        )
        self.assertEqual(wrong_action["error"]["code"], "approval_mismatch")

        token = order_preview["preview_id"]
        tampered = self._call(
            service,
            "trade_confirm_order",
            order
            | {
                "preview_id": token[:-1] + ("A" if token[-1] != "A" else "B"),
                "confirmation": {"accepted": True},
            },
        )
        self.assertEqual(tampered["error"]["code"], "approval_token_invalid")

        rejected = self._call(
            service,
            "trade_confirm_order",
            order
            | {
                "preview_id": token,
                "confirmation": {"accepted": False},
            },
        )
        self.assertEqual(rejected["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.execute_calls, 0)

    def test_same_valid_token_can_be_replayed_and_is_not_exactly_once(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        confirmation = arguments | {
            "preview_id": preview["preview_id"],
            "confirmation": {"accepted": True},
        }

        first = self._call(service, "trade_confirm_order", confirmation)
        second = self._call(service, "trade_confirm_order", confirmation)

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(adapter.execute_calls, 2)

    def test_token_claims_and_errors_do_not_expose_request_credentials(self):
        import json

        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        claims = service.approval_codec.verify(preview["preview_id"])
        encoded_claims = json.dumps(claims, ensure_ascii=False)
        for secret in ("AK_ONE", "SK_INLINE", "PP_INLINE"):
            self.assertNotIn(secret, encoded_claims)

        failure = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": "not-a-token",
                "confirmation": {"accepted": True},
            },
        )
        encoded_failure = json.dumps(failure, ensure_ascii=False)
        for secret in ("AK_ONE", "SK_INLINE", "PP_INLINE", "not-a-token"):
            self.assertNotIn(secret, encoded_failure)

    def test_non_json_preview_data_fails_closed_without_issuing_a_token(self):
        from weex_mcp.service import build_default_service

        class NonJsonAdapter(StatelessTradeAdapter):
            def preview_order(self, *, arguments, credential, credential_argument, context):
                result = super().preview_order(
                    arguments=arguments,
                    credential=credential,
                    credential_argument=credential_argument,
                    context=context,
                )
                result["risk"]["not_json"] = object()
                return result

        service = build_default_service(trade_enabled=True, trade_adapter=NonJsonAdapter())

        result = self._call(service, "trade_preview_order", self._order())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "approval_token_issue_failed")
        self.assertNotIn("approval_token", result)
        self.assertNotIn("preview_id", result)


if __name__ == "__main__":
    unittest.main()
