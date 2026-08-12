import copy
import unittest


def credential(api_key="AK_TRADE"):
    return {
        "type": "plaintext",
        "api_key": api_key,
        "api_secret": "SK_TRADE",
        "api_passphrase": "PP_TRADE",
    }


def confirmation():
    return {"accepted": True, "reply_text": "确认"}


def cases():
    return {
        "batch_order": {
            "market": "spot",
            "trading_mode": "live",
            "orders": [
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "0.001",
                    "client_order_id": "batch-1",
                }
            ],
            "credential": credential(),
        },
        "batch_cancel": {
            "market": "futures",
            "trading_mode": "live",
            "client_order_ids": ["batch-1"],
            "credential": credential(),
        },
        "cancel_scope": {
            "market": "spot",
            "trading_mode": "live",
            "order_kind": "normal",
            "scope": "symbol",
            "symbol": "BTCUSDT",
            "credential": credential(),
        },
        "conditional_order": {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "position_side": "LONG",
            "conditional_type": "TAKE_PROFIT_MARKET",
            "quantity": "0.001",
            "trigger_price": "100",
            "client_algo_id": "conditional-1",
            "credential": credential(),
        },
        "conditional_cancel": {
            "market": "futures",
            "trading_mode": "live",
            "order_id": "1",
            "credential": credential(),
        },
        "tp_sl": {
            "market": "futures",
            "trading_mode": "live",
            "operation": "modify",
            "order_id": "1",
            "trigger_price": "100",
            "credential": credential(),
        },
        "close_positions": {
            "market": "futures",
            "trading_mode": "live",
            "scope": "position",
            "position_id": "1",
            "symbol": "BTCUSDT",
            "credential": credential(),
        },
        "position_margin": {
            "market": "futures",
            "trading_mode": "live",
            "isolated_position_id": "1",
            "amount": "1",
            "adjustment": "decrease",
            "credential": credential(),
        },
        "auto_append_margin": {
            "market": "futures",
            "trading_mode": "live",
            "position_id": "1",
            "enabled": False,
            "credential": credential(),
        },
    }


class GenericMutationAdapter:
    def __init__(self):
        self.preview_actions = []
        self.confirm_actions = []

    def resolve_credential(self, *, credential_argument, context):
        from weex_mcp.audit import make_fingerprint
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id="request",
            api_key=credential_argument["api_key"],
            api_secret=credential_argument["api_secret"],
            api_passphrase=credential_argument["api_passphrase"],
            permissions=frozenset({"TRADE"}),
            fingerprint=make_fingerprint("resolved_weex_api_key", credential_argument["api_key"]),
        )

    def preview_mutation(self, *, action, arguments, credential, credential_argument, context):
        self.preview_actions.append(action)
        request = {
            key: value
            for key, value in arguments.items()
            if key not in {"credential", "preview_id", "approval_token", "confirmation"}
        }
        binding_name = "request_query" if action in {"cancel_scope", "conditional_cancel"} else "request_body"
        return {
            "risk_signature": f"risk-{action}",
            "summary": {
                "action": action,
                "market": arguments["market"],
                "trading_mode": arguments["trading_mode"],
                "endpoint": f"/test/{action}",
                binding_name: request,
            },
            "risk": {"status": "review_required", "alerts": [action]},
        }

    def confirm_mutation(self, *, execution, credential, credential_argument, context):
        self.confirm_actions.append(execution.action)
        return {"success": True, "status": "accepted", "action": execution.action}


class OmittedTradeCapabilityTests(unittest.TestCase):
    def _call(self, service, name, arguments):
        return service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )["result"]

    def test_each_new_write_action_uses_stateless_preview_and_full_confirm_recheck(self):
        from weex_mcp.service import build_default_service

        adapter = GenericMutationAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)

        for action, arguments in cases().items():
            with self.subTest(action=action):
                preview = self._call(service, f"trade_preview_{action}", arguments)
                self.assertTrue(preview["ok"], preview)
                self.assertIn("preview_id", preview)
                self.assertNotIn("approval_token", preview)

                confirmed = self._call(
                    service,
                    f"trade_confirm_{action}",
                    arguments
                    | {
                        "preview_id": preview["preview_id"],
                        "confirmation": confirmation(),
                    },
                )
                self.assertTrue(confirmed["ok"], confirmed)

        self.assertEqual(adapter.preview_actions, [item for action in cases() for item in (action, action)])
        self.assertEqual(adapter.confirm_actions, list(cases()))

    def test_new_confirm_rejects_parameter_and_credential_tampering_before_write(self):
        from weex_mcp.service import build_default_service

        adapter = GenericMutationAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        arguments = cases()["position_margin"]
        preview = self._call(service, "trade_preview_position_margin", arguments)

        tampered = self._call(
            service,
            "trade_confirm_position_margin",
            arguments
            | {
                "amount": "2",
                "preview_id": preview["preview_id"],
                "confirmation": confirmation(),
            },
        )
        self.assertFalse(tampered["ok"])
        self.assertEqual(tampered["error"]["code"], "approval_mismatch")

        changed_credential = copy.deepcopy(arguments)
        changed_credential["credential"] = credential("AK_OTHER")
        changed_credential.update({"preview_id": preview["preview_id"], "confirmation": confirmation()})
        changed = self._call(service, "trade_confirm_position_margin", changed_credential)
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.confirm_actions, [])

    def test_preview_id_from_one_new_action_cannot_confirm_another(self):
        from weex_mcp.service import build_default_service

        adapter = GenericMutationAdapter()
        service = build_default_service(trade_enabled=True, trade_adapter=adapter)
        preview = self._call(service, "trade_preview_conditional_cancel", cases()["conditional_cancel"])
        target = cases()["auto_append_margin"] | {
            "preview_id": preview["preview_id"],
            "confirmation": confirmation(),
        }

        result = self._call(service, "trade_confirm_auto_append_margin", target)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "approval_mismatch")
        self.assertEqual(adapter.confirm_actions, [])

    def test_trade_disabled_precedes_new_action_schema_and_adapter(self):
        from weex_mcp.service import build_default_service

        adapter = GenericMutationAdapter()
        result = self._call(
            build_default_service(trade_enabled=False, trade_adapter=adapter),
            "trade_preview_close_positions",
            {"unexpected": True},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "module_disabled")
        self.assertEqual(adapter.preview_actions, [])
        self.assertEqual(adapter.confirm_actions, [])

    def test_new_preview_missing_fields_stays_structured_and_skips_adapter(self):
        from weex_mcp.service import build_default_service

        adapter = GenericMutationAdapter()
        result = self._call(
            build_default_service(trade_enabled=True, trade_adapter=adapter),
            "trade_preview_batch_order",
            {
                "market": "futures",
                "trading_mode": "live",
                "credential": credential(),
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_fields")
        self.assertEqual(result["error"]["details"]["fields"], ["orders"])
        self.assertEqual(adapter.preview_actions, [])


if __name__ == "__main__":
    unittest.main()
