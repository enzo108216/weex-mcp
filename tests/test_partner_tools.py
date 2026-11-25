from __future__ import annotations

import json
import unittest
from unittest.mock import patch

PARTNER_TOOL_NAMES = {
    "partner_get_affiliate_uids",
    "partner_get_channel_user_trade_and_asset",
    "partner_get_affiliate_commission",
    "partner_query_sub_channel_transactions",
    "partner_verify_referrals",
    "partner_get_referral_assets",
    "partner_get_referral_deal_data",
}


class RecordingPartnerAdapter:
    def __init__(self):
        self.calls = []

    def _result(self, name, kwargs):
        self.calls.append((name, kwargs))
        return {
            "endpoint": name,
            "method": "GET",
            "path": "/fixed",
            "weight": 10,
            "rate_limit": {"used": {}, "remaining": {}},
            "retry": {"automatic": False},
            "environment": "partner_test",
            "capability_mode": "read_only_query",
            "data": {"records": []},
        }

    def get_affiliate_uids(self, **kwargs):
        return self._result("partner.get-affiliate-uids", kwargs)

    def get_channel_user_trade_and_asset(self, **kwargs):
        return self._result("partner.get-channel-user-trade-and-asset", kwargs)

    def get_affiliate_commission(self, **kwargs):
        return self._result("partner.get-affiliate-commission", kwargs)

    def query_sub_channel_transactions(self, **kwargs):
        return self._result("partner.query-sub-channel-transactions", kwargs)

    def verify_referrals(self, **kwargs):
        return self._result("partner.verify-referrals", kwargs)

    def get_referral_assets(self, **kwargs):
        return self._result("partner.get-referral-assets", kwargs)

    def get_referral_deal_data(self, **kwargs):
        return self._result("partner.get-referral-deal-data", kwargs)


class PartnerToolTests(unittest.TestCase):
    def _credential(self):
        return {"type": "credential_ref", "key_id": "partner-key", "ref": "partner-ref"}

    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant", user_id="user", session_id="session")

    def test_seven_partner_tools_dispatch_with_standard_envelope_and_audit(self):
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        adapter = RecordingPartnerAdapter()
        sink = MemoryAuditSink()
        registry = build_default_registry(
            enabled_modules={"partner"},
            credential_modes={"credential_ref"},
        )
        service = build_default_service(
            registry=registry,
            partner_adapter=adapter,
            audit_sink=sink,
        )
        credential = self._credential()
        calls = {
            "partner_get_affiliate_uids": {"credential": credential},
            "partner_get_channel_user_trade_and_asset": {"credential": credential},
            "partner_get_affiliate_commission": {
                "credential": credential,
                "coin": "USDT",
                "product_type": "SPOT",
            },
            "partner_query_sub_channel_transactions": {
                "credential": credential,
                "product_type": "FUTURES",
                "start_time": "1000",
                "end_time": "2000",
            },
            "partner_verify_referrals": {
                "credential": credential,
                "user_ids": ["1", "2"],
                "expected_environment": "partner_test",
            },
            "partner_get_referral_assets": {
                "credential": credential,
                "user_id": "1",
                "start_time": "2026-01-01",
                "end_time": "2026-01-31",
                "expected_environment": "partner_test",
            },
            "partner_get_referral_deal_data": {
                "credential": credential,
                "start_time": "2026-02-01",
                "end_time": "2026-02-28",
                "user_ids": ["1", "2"],
                "expected_environment": "partner_test",
            },
        }
        for index, (name, arguments) in enumerate(calls.items()):
            with self.subTest(name=name):
                response = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": f"partner-{index}",
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                    context=self._context(),
                )
                payload = response["result"]["structuredContent"]
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["data"]["environment"], "partner_test")
                self.assertEqual(payload["audit"]["tenant_id"], "tenant")
                self.assertFalse(response["result"]["isError"])

        self.assertEqual(len(adapter.calls), 7)
        self.assertTrue(
            all(
                kwargs.get("expected_environment") == "partner_test"
                for name, kwargs in adapter.calls[-3:]
            )
        )
        self.assertEqual(len(sink.events), 7)
        self.assertTrue(all(event["module"] == "partner" for event in sink.events))
        self.assertTrue(all(event["permission"] == "USER_DATA" for event in sink.events))
        self.assertTrue(all(event["credential_type"] == "credential_ref" for event in sink.events))
        serialized = json.dumps(sink.events, sort_keys=True)
        self.assertNotIn("partner-ref", serialized)

    def test_partner_tool_projects_stable_adapter_error_without_secrets(self):
        from weex_mcp.adapters.partner import PartnerAdapterError
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        class ErrorAdapter(RecordingPartnerAdapter):
            def verify_referrals(self, **kwargs):
                raise PartnerAdapterError(
                    "partner_permission_error",
                    category="permission",
                    http_status=403,
                    upstream_code="-1050",
                )

        service = build_default_service(
            registry=build_default_registry(
                enabled_modules={"partner"}, credential_modes={"credential_ref"}
            ),
            partner_adapter=ErrorAdapter(),
        )
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "partner-error",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                },
            },
            context=self._context(),
        )
        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "partner_permission_error")
        self.assertEqual(payload["error"]["details"]["category"], "permission")
        self.assertNotIn("partner-ref", json.dumps(response, sort_keys=True))

    def test_injected_partner_error_is_allowlisted_before_projection(self):
        from weex_mcp.adapters.partner import PartnerAdapterError
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        class UnsafeErrorAdapter(RecordingPartnerAdapter):
            def verify_referrals(self, **kwargs):
                raise PartnerAdapterError(
                    "AK_PARTNER",
                    category="SK_PARTNER",
                    http_status="403",
                    upstream_code="PP_PARTNER",
                    reason="SECRET_REASON",
                    rate_limit={"used": {"SECRET_BUCKET": "SECRET_VALUE"}},
                )

        service = build_default_service(
            registry=build_default_registry(
                enabled_modules={"partner"}, credential_modes={"credential_ref"}
            ),
            partner_adapter=UnsafeErrorAdapter(),
        )
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "unsafe-partner-error",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                },
            },
            context=self._context(),
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["error"]["code"], "partner_adapter_error")
        serialized = json.dumps(payload, sort_keys=True)
        for secret in (
            "AK_PARTNER",
            "SK_PARTNER",
            "PP_PARTNER",
            "SECRET_REASON",
            "SECRET_BUCKET",
            "SECRET_VALUE",
        ):
            self.assertNotIn(secret, serialized)

        class UnhashableErrorAdapter(RecordingPartnerAdapter):
            def verify_referrals(self, **kwargs):
                raise PartnerAdapterError(
                    {"AK_PARTNER": []},
                    category=["SK_PARTNER"],
                    http_status={"status": 403},
                    upstream_code={"PP_PARTNER": []},
                    reason=["SECRET_REASON"],
                    rate_limit={"used": {("SECRET_BUCKET",): ["SECRET_VALUE"]}},
                )

        unhashable_service = build_default_service(
            registry=build_default_registry(
                enabled_modules={"partner"}, credential_modes={"credential_ref"}
            ),
            partner_adapter=UnhashableErrorAdapter(),
        )
        unhashable_response = unhashable_service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "unhashable-partner-error",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                },
            },
            context=self._context(),
        )
        unhashable_payload = unhashable_response["result"]["structuredContent"]
        self.assertEqual(unhashable_payload["error"]["code"], "partner_adapter_error")
        self.assertNotIn("SECRET", json.dumps(unhashable_payload, sort_keys=True))

    def test_partner_value_error_projection_never_returns_raw_provider_or_adapter_text(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        class UnsafeValueErrorAdapter(RecordingPartnerAdapter):
            def verify_referrals(self, **kwargs):
                raise ValueError("AK_INLINE partner-ref https://private.weex.tech")

        class UnsafeProvider:
            def resolve(self, **kwargs):
                raise ValueError("SK_INLINE secret-ref https://provider.weex.tech")

        adapters = (
            UnsafeValueErrorAdapter(),
            WeexPartnerRestAdapter(secret_provider=UnsafeProvider()),
        )
        for index, adapter in enumerate(adapters):
            with self.subTest(index=index):
                service = build_default_service(
                    registry=build_default_registry(
                        enabled_modules={"partner"}, credential_modes={"credential_ref"}
                    ),
                    partner_adapter=adapter,
                )
                response = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": f"unsafe-value-error-{index}",
                        "method": "tools/call",
                        "params": {
                            "name": "partner_verify_referrals",
                            "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                        },
                    },
                    context=self._context(),
                )
                payload = response["result"]["structuredContent"]
                self.assertEqual(payload["error"]["code"], "invalid_arguments")
                self.assertEqual(payload["error"]["message"], "Partner query arguments are invalid.")
                serialized = json.dumps(payload, sort_keys=True)
                for secret in (
                    "AK_INLINE",
                    "partner-ref",
                    "private.weex.tech",
                    "SK_INLINE",
                    "secret-ref",
                    "provider.weex.tech",
                ):
                    self.assertNotIn(secret, serialized)

    def test_unknown_secret_provider_code_uses_stable_generic_projection(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter
        from weex_mcp.secret_provider import SecretProviderError
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        for index, unsafe_code in enumerate(("AK_UNKNOWN_CODE", {"partner-ref": []})):
            with self.subTest(index=index):
                class UnknownCodeProvider:
                    def __init__(self, code):
                        self.code = code

                    def resolve(self, **kwargs):
                        raise SecretProviderError(
                            self.code,
                            "SK_INLINE secret-ref https://provider.weex.tech",
                        )

                service = build_default_service(
                    registry=build_default_registry(
                        enabled_modules={"partner"}, credential_modes={"credential_ref"}
                    ),
                    partner_adapter=WeexPartnerRestAdapter(
                        secret_provider=UnknownCodeProvider(unsafe_code)
                    ),
                )
                response = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": f"unknown-provider-code-{index}",
                        "method": "tools/call",
                        "params": {
                            "name": "partner_verify_referrals",
                            "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                        },
                    },
                    context=self._context(),
                )
                payload = response["result"]["structuredContent"]
                self.assertEqual(payload["error"]["code"], "credential_resolution_failed")
                self.assertEqual(
                    payload["error"]["message"],
                    "Credential provider failed to resolve credentials.",
                )
                serialized = json.dumps(payload, sort_keys=True)
                for secret in (
                    "AK_UNKNOWN_CODE",
                    "partner-ref",
                    "SK_INLINE",
                    "secret-ref",
                    "provider.weex.tech",
                ):
                    self.assertNotIn(secret, serialized)

    def test_generic_adapter_exception_does_not_project_dynamic_class_name(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        dynamic_error = type(
            "AK_INLINE_partner_ref_private_weex_tech",
            (RuntimeError,),
            {},
        )

        class DynamicErrorAdapter(RecordingPartnerAdapter):
            def verify_referrals(self, **kwargs):
                raise dynamic_error("SK_INLINE secret-ref https://private.weex.tech")

        service = build_default_service(
            registry=build_default_registry(
                enabled_modules={"partner"}, credential_modes={"credential_ref"}
            ),
            partner_adapter=DynamicErrorAdapter(),
        )
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "dynamic-adapter-error",
                "method": "tools/call",
                "params": {
                    "name": "partner_verify_referrals",
                    "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                },
            },
            context=self._context(),
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["error"]["code"], "partner_adapter_error")
        self.assertNotIn("details", payload["error"])
        serialized = json.dumps(payload, sort_keys=True)
        for secret in (
            "AK_INLINE",
            "partner_ref",
            "private_weex_tech",
            "SK_INLINE",
            "secret-ref",
            "private.weex.tech",
        ):
            self.assertNotIn(secret, serialized)

    def test_default_and_broker_registries_hide_partner_and_direct_call_never_reaches_adapter(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tool_registry import build_default_registry

        for registry in (
            build_default_registry(),
            build_default_registry(
                enabled_modules={"broker", "market", "account", "trade"},
                credential_modes={"plaintext"},
            ),
        ):
            with self.subTest(tool_count=len(registry.list_tools())):
                adapter = RecordingPartnerAdapter()
                service = build_default_service(registry=registry, partner_adapter=adapter)
                listed = service.handle_jsonrpc(
                    {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}
                )
                names = {tool["name"] for tool in listed["result"]["tools"]}
                self.assertTrue(PARTNER_TOOL_NAMES.isdisjoint(names))
                called = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": "hidden-partner",
                        "method": "tools/call",
                        "params": {
                            "name": "partner_verify_referrals",
                            "arguments": {"credential": self._credential(), "user_ids": ["1"]},
                        },
                    },
                    context=self._context(),
                )
                self.assertEqual(called["error"]["code"], -32602)
                self.assertEqual(adapter.calls, [])

    def test_default_partner_adapter_is_constructed_only_for_a_partner_registry(self):
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings
        from weex_mcp.tool_registry import build_default_registry

        provider = UnconfiguredSecretProvider()
        configured_adapter = RecordingPartnerAdapter()
        with patch(
            "weex_mcp.service.WeexPartnerRestAdapter",
            return_value=configured_adapter,
        ) as constructor:
            default_service = build_default_service(secret_provider=provider)
            constructor.assert_not_called()
            self.assertIsNone(default_service.partner_adapter)

            partner_service = build_default_service(
                settings=Settings(spot_base_url="https://partner-fixture.weex.tech"),
                registry=build_default_registry(
                    enabled_modules={"partner"},
                    credential_modes={"credential_ref"},
                ),
                secret_provider=provider,
            )
        constructor.assert_called_once_with(
            secret_provider=provider,
            base_url="https://partner-fixture.weex.tech",
        )
        self.assertIs(partner_service.partner_adapter, configured_adapter)


if __name__ == "__main__":
    unittest.main()
