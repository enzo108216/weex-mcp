import unittest


class StatelessTradeAdapter:
    def __init__(self):
        self.preview_calls = 0
        self.execute_calls = 0
        self.preview_actions = []
        self.execute_actions = []

    def resolve_credential(self, *, credential_argument, context):
        from weex_mcp.audit import make_fingerprint
        from weex_mcp.secret_provider import ResolvedWeexCredential

        api_key = credential_argument["api_key"]
        return ResolvedWeexCredential(
            key_id="request",
            api_key=api_key,
            api_secret=credential_argument["api_secret"],
            api_passphrase=credential_argument["api_passphrase"],
            permissions=frozenset(["TRADE"]),
            fingerprint=make_fingerprint("resolved_weex_api_key", api_key),
        )

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
    def _credential(self, api_key="AK_ONE"):
        return {
            "type": "plaintext",
            "api_key": api_key,
            "api_secret": "SK_INLINE",
            "api_passphrase": "PP_INLINE",
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

    def test_confirm_rechecks_full_order_before_execution(self):
        from weex_mcp.service import build_default_service

        adapter = StatelessTradeAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = self._order()
        preview = self._call(service, "trade_preview_order", arguments)
        self.assertTrue(preview["ok"])
        self.assertIn("approval_token", preview)
        self.assertNotIn("preview_id", preview)

        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "approval_token": preview["approval_token"],
                "confirmation": {"accepted": True, "reply_text": "确认"},
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(adapter.preview_calls, 2)
        self.assertEqual(adapter.execute_calls, 1)

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
                "approval_token": preview["approval_token"],
                "confirmation": {"accepted": True, "reply_text": "确认"},
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
                "approval_token": preview["approval_token"],
                "confirmation": {"accepted": True, "reply_text": "确认"},
            },
        )
        self.assertFalse(changed_credential["ok"])
        self.assertEqual(changed_credential["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.execute_calls, 0)

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
                        "approval_token": preview["approval_token"],
                        "confirmation": {"accepted": True, "reply_text": "确认"},
                    },
                )
                self.assertTrue(confirmed["ok"])
                self.assertEqual(adapter.preview_actions, [action, action])
                self.assertEqual(adapter.execute_actions, [action])

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
                "approval_token": cancel_preview["approval_token"],
                "confirmation": {"accepted": True, "reply_text": "确认"},
            },
        )
        self.assertEqual(wrong_action["error"]["code"], "approval_mismatch")

        token = order_preview["approval_token"]
        tampered = self._call(
            service,
            "trade_confirm_order",
            order
            | {
                "approval_token": token[:-1] + ("A" if token[-1] != "A" else "B"),
                "confirmation": {"accepted": True, "reply_text": "确认"},
            },
        )
        self.assertEqual(tampered["error"]["code"], "approval_token_invalid")

        rejected = self._call(
            service,
            "trade_confirm_order",
            order
            | {
                "approval_token": token,
                "confirmation": {"accepted": False, "reply_text": "确认"},
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
            "approval_token": preview["approval_token"],
            "confirmation": {"accepted": True, "reply_text": "确认"},
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
        claims = service.approval_codec.verify(preview["approval_token"])
        encoded_claims = json.dumps(claims, ensure_ascii=False)
        for secret in ("AK_ONE", "SK_INLINE", "PP_INLINE"):
            self.assertNotIn(secret, encoded_claims)

        failure = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "approval_token": "not-a-token",
                "confirmation": {"accepted": True, "reply_text": "确认"},
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


if __name__ == "__main__":
    unittest.main()
