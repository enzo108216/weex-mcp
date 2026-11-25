from __future__ import annotations

import unittest


class _SecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id=str(credential_argument["key_id"]),
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset({required_permission}),
            fingerprint="fp_test",
        )


class T7PartnerCompatibilityTests(unittest.TestCase):
    def test_settings_parse_partner_timeout_with_thirty_second_default(self) -> None:
        from weex_mcp.settings import Settings

        defaults = Settings.from_environment({})
        configured = Settings.from_environment(
            {"WEEX_PARTNER_TIMEOUT_SECONDS": "12.5"}
        )

        self.assertEqual(defaults.partner_timeout_seconds, 30.0)
        self.assertEqual(configured.partner_timeout_seconds, 12.5)

    def test_local_service_wires_partner_timeout_and_locale(self) -> None:
        from weex_mcp.local import build_local_user_service
        from weex_mcp.secret_provider import UnconfiguredSecretProvider
        from weex_mcp.settings import Settings

        service = build_local_user_service(
            settings=Settings(
                partner_timeout_seconds=12.5,
                request_locale="zh-CN",
            ),
            secret_provider=UnconfiguredSecretProvider(),
        )

        self.assertEqual(service.partner_adapter.timeout_seconds, 12.5)
        self.assertEqual(service.partner_adapter.locale, "zh-CN")

    def test_partner_success_preserves_locale_and_http_status(self) -> None:
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter
        from weex_mcp.tenant_context import TenantContext

        prepared_requests: list[dict[str, object]] = []

        def transport(prepared, _timeout):
            prepared_requests.append(prepared)
            return 206, {"X-USED-WEIGHT-1M": "7"}, b'{"data":[]}'

        adapter = WeexPartnerRestAdapter(
            secret_provider=_SecretProvider(),
            locale="zh-CN",
            transport=transport,
            time_func=lambda: 1.0,
        )
        result = adapter.get_affiliate_uids(
            credential_argument={
                "type": "credential_ref",
                "key_id": "key",
                "ref": "ref",
            },
            context=TenantContext(
                tenant_id="tenant",
                user_id="user",
                session_id="session",
            ),
        )

        self.assertEqual(prepared_requests[0]["headers"]["locale"], "zh-CN")
        self.assertEqual(result["http_status"], 206)
        self.assertEqual(result["rate_limit"]["used"]["1M"], 7)


if __name__ == "__main__":
    unittest.main()
