from __future__ import annotations

import importlib
import importlib.util
import json
import unittest


class RecordingTradeAdapter:
    def __init__(self, provider, events):
        self.provider = provider
        self.events = events
        self.execute_calls = 0
        self.resolve_calls = 0

    def resolve_credential(self, *, credential_argument, context):
        self.resolve_calls += 1
        return self.provider.resolve(
            credential_argument=credential_argument,
            context=context,
            required_permission="TRADE",
        )

    def validate_trade_access(self, *, arguments, credential, context):
        self.events.append("access")
        return credential

    def preview_order(self, *, arguments, credential, credential_argument, context):
        self.events.append("preview")
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
            "risk": {"status": "review_required", "alerts": ["order"]},
        }

    def confirm_order(self, *, execution, credential, credential_argument, context):
        self.events.append("confirm")
        self.execute_calls += 1
        return {"success": True, "status": "accepted"}


class RecordingAuthorizer:
    def __init__(self, events, *, result="verified"):
        self.events = events
        self.result = result
        self.calls = []

    def authorize(self, **kwargs):
        self.events.append("authorize")
        self.calls.append(kwargs)
        if self.result != "verified":
            return self.result
        from weex_mcp.execution_authorization import VerifiedExecutionAuthorization

        return VerifiedExecutionAuthorization(
            authorization_id="authorization-1",
            scope_fingerprint="fp_scope",
            expires_at=1_900_000_000,
            audit_metadata={"strategy": "grid"},
        )


class DenyingAuthorizer:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    def authorize(self, **_kwargs):
        from weex_mcp.execution_authorization import ExecutionAuthorizationError

        self.events.append("authorize")
        self.calls += 1
        raise ExecutionAuthorizationError(
            "execution_authorization_denied",
            "opaque-ref-must-not-escape",
        )


class ExecutionAuthorizationContractTests(unittest.TestCase):
    def _module(self):
        spec = importlib.util.find_spec("weex_mcp.execution_authorization")
        self.assertIsNotNone(spec, "weex_mcp.execution_authorization module is missing")
        return importlib.import_module("weex_mcp.execution_authorization")

    def test_default_authorizer_fails_closed_without_leaking_ref(self):
        from weex_mcp.tenant_context import TenantContext
        from weex_mcp.trade_execution import TradeExecution

        module = self._module()
        authorizer = module.FailClosedExecutionAuthorizer()
        with self.assertRaises(module.ExecutionAuthorizationError) as raised:
            authorizer.authorize(
                context=TenantContext(tenant_id="local", user_id="user", session_id="session"),
                credential_fingerprint="fp_credential",
                execution=TradeExecution(
                    action="order",
                    market="spot",
                    trading_mode="live",
                    summary={"action": "order"},
                    risk={"status": "review_required"},
                    risk_signature="risk-1",
                ),
                delegated_ref={"ref": "SECRET_AUTHORIZATION_REF"},
            )

        self.assertEqual(raised.exception.code, "execution_authorizer_unconfigured")
        self.assertNotIn("SECRET_AUTHORIZATION_REF", raised.exception.safe_message())

    def test_verified_authorization_exposes_only_redacted_audit_facts(self):
        module = self._module()
        verified = module.VerifiedExecutionAuthorization(
            authorization_id="authorization-1",
            scope_fingerprint="fp_scope",
            expires_at=1_900_000_000,
            audit_metadata={"strategy": "grid", "api_secret": "MUST_NOT_ESCAPE"},
        )

        audit = verified.safe_audit_dict()
        self.assertEqual(audit["authorization_id"], "authorization-1")
        self.assertEqual(audit["scope_fingerprint"], "fp_scope")
        self.assertEqual(audit["authorization_expires_at"], 1_900_000_000)
        serialized = json.dumps(audit, sort_keys=True)
        self.assertIn("grid", serialized)
        self.assertNotIn("MUST_NOT_ESCAPE", serialized)


class DelegatedExecutionTests(unittest.TestCase):
    def _arguments(self):
        return {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "delegated-order-1",
            "credential": {"type": "credential_ref", "key_id": "key", "ref": "profile"},
        }

    def _components(self):
        from weex_mcp.secret_provider import StaticMappingSecretProvider

        provider = StaticMappingSecretProvider(
            {
                "profile": {
                    "api_key": "AK",
                    "api_secret": "SK",
                    "api_passphrase": "PP",
                    "permissions": ["TRADE"],
                },
                "other-profile": {
                    "api_key": "AK_OTHER",
                    "api_secret": "SK_OTHER",
                    "api_passphrase": "PP_OTHER",
                    "permissions": ["TRADE"],
                }
            }
        )
        events = []
        return provider, events, RecordingTradeAdapter(provider, events)

    def _service(self, *, provider, adapter, authorizer=None):
        from weex_mcp.local import build_local_user_service
        from weex_mcp.settings import Settings

        kwargs = {
            "settings": Settings(trade_enabled=True, approval_token_secret="s" * 32),
            "secret_provider": provider,
            "trade_adapter": adapter,
        }
        if authorizer is not None:
            kwargs["execution_authorizer"] = authorizer
        try:
            return build_local_user_service(**kwargs)
        except TypeError as exc:
            self.fail(f"local execution authorizer injection is missing: {exc}")

    def _call(self, service, name, arguments):
        return service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )["result"]["structuredContent"]

    def test_delegated_authorizer_runs_after_recheck_and_before_confirm(self):
        provider, events, adapter = self._components()
        authorizer = RecordingAuthorizer(events)
        service = self._service(provider=provider, adapter=adapter, authorizer=authorizer)
        arguments = self._arguments()
        preview = self._call(service, "trade_preview_order", arguments)
        events.clear()

        confirmed = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": preview["preview_id"],
                "delegated_authorization": {"ref": "opaque-authorization-ref"},
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(events, ["authorize", "preview", "access", "confirm"])
        self.assertEqual(len(authorizer.calls), 1)
        call = authorizer.calls[0]
        self.assertEqual(call["delegated_ref"], {"ref": "opaque-authorization-ref"})
        self.assertEqual(call["execution"].summary["request_body"]["newClientOrderId"], "delegated-order-1")
        self.assertTrue(call["credential_fingerprint"].startswith("fp_"))
        self.assertEqual(confirmed["audit"]["authorization_id"], "authorization-1")

    def test_missing_or_invalid_authorizer_fails_before_confirm_transport(self):
        arguments = self._arguments()
        for authorizer, expected_code in (
            (None, "execution_authorizer_unconfigured"),
            (DenyingAuthorizer([]), "execution_authorization_denied"),
            (RecordingAuthorizer([], result=object()), "execution_authorization_invalid"),
        ):
            with self.subTest(expected_code=expected_code):
                provider, events, adapter = self._components()
                if isinstance(authorizer, RecordingAuthorizer):
                    authorizer.events = events
                if isinstance(authorizer, DenyingAuthorizer):
                    authorizer.events = events
                service = self._service(provider=provider, adapter=adapter, authorizer=authorizer)
                preview = self._call(service, "trade_preview_order", arguments)
                events.clear()
                result = self._call(
                    service,
                    "trade_confirm_order",
                    arguments
                    | {
                        "preview_id": preview["preview_id"],
                        "delegated_authorization": {"ref": "opaque-authorization-ref"},
                    },
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], expected_code)
                self.assertEqual(adapter.execute_calls, 0)
                self.assertFalse(any(event in {"preview", "access", "confirm"} for event in events))
                self.assertEqual(adapter.resolve_calls, 2)

    def test_binding_mismatch_does_not_call_authorizer_or_confirm_transport(self):
        provider, events, adapter = self._components()
        authorizer = RecordingAuthorizer(events)
        service = self._service(provider=provider, adapter=adapter, authorizer=authorizer)
        arguments = self._arguments()
        preview = self._call(service, "trade_preview_order", arguments)
        events.clear()

        result = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "quantity": "0.002",
                "preview_id": preview["preview_id"],
                "delegated_authorization": {"ref": "opaque-authorization-ref"},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "approval_mismatch")
        self.assertEqual(len(authorizer.calls), 1)
        self.assertEqual(events, ["authorize", "preview", "access"])
        self.assertEqual(adapter.execute_calls, 0)

    def test_action_or_credential_mismatch_fails_before_authorizer_and_network_recheck(self):
        provider, events, adapter = self._components()
        authorizer = RecordingAuthorizer(events)
        service = self._service(provider=provider, adapter=adapter, authorizer=authorizer)
        arguments = self._arguments()
        preview = self._call(service, "trade_preview_order", arguments)

        cases = (
            (
                "action",
                "trade_confirm_cancel_order",
                {
                    "market": "spot",
                    "trading_mode": "live",
                    "order_id": "123",
                    "credential": arguments["credential"],
                },
            ),
            (
                "credential",
                "trade_confirm_order",
                arguments
                | {
                    "credential": {
                        "type": "credential_ref",
                        "key_id": "other-key",
                        "ref": "other-profile",
                    }
                },
            ),
        )
        for label, tool_name, current_arguments in cases:
            with self.subTest(label=label):
                events.clear()
                authorizer.calls.clear()
                before_resolve = adapter.resolve_calls
                result = self._call(
                    service,
                    tool_name,
                    current_arguments
                    | {
                        "preview_id": preview["preview_id"],
                        "delegated_authorization": {"ref": "opaque-authorization-ref"},
                    },
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "approval_mismatch")
                self.assertEqual(authorizer.calls, [])
                self.assertEqual(events, [])
                self.assertEqual(adapter.resolve_calls, before_resolve + 1)
                self.assertEqual(adapter.execute_calls, 0)

    def test_malformed_signed_execution_claims_fail_before_authorizer_and_network_recheck(self):
        from weex_mcp.tenant_context import TenantContext

        provider, events, adapter = self._components()
        authorizer = RecordingAuthorizer(events)
        service = self._service(provider=provider, adapter=adapter, authorizer=authorizer)
        arguments = self._arguments()
        resolved = provider.resolve(
            credential_argument=arguments["credential"],
            context=TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request"),
            required_permission="TRADE",
        )
        malformed_token = service.approval_codec.issue(
            action="order",
            credential_fingerprint=resolved.fingerprint,
            summary={"action": "order", "market": ["spot"], "trading_mode": "live"},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        result = self._call(
            service,
            "trade_confirm_order",
            arguments
            | {
                "preview_id": malformed_token,
                "delegated_authorization": {"ref": "opaque-authorization-ref"},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "approval_token_invalid")
        self.assertEqual(authorizer.calls, [])
        self.assertEqual(events, [])
        self.assertEqual(adapter.execute_calls, 0)

    def test_runtime_rejects_interactive_and_delegated_authorization_together(self):
        from weex_mcp.approval_token import ApprovalTokenCodec
        from weex_mcp.execution_authorization import FailClosedExecutionAuthorizer
        from weex_mcp.tenant_context import TenantContext
        from weex_mcp.tools.trade import confirm_order

        provider, events, adapter = self._components()
        arguments = self._arguments() | {
            "preview_id": "not-used",
            "confirmation": {"accepted": True},
            "delegated_authorization": {"ref": "opaque-authorization-ref"},
        }
        try:
            result = confirm_order(
                arguments,
                context=TenantContext(tenant_id="local", user_id="user", session_id="session"),
                trade_enabled=True,
                approval_codec=ApprovalTokenCodec(secret="s" * 32),
                adapter=adapter,
                execution_authorizer=FailClosedExecutionAuthorizer(),
            )
        except TypeError as exc:
            self.fail(f"trade runtime authorizer defense is missing: {exc}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertEqual(events, [])
        self.assertEqual(adapter.execute_calls, 0)


if __name__ == "__main__":
    unittest.main()
