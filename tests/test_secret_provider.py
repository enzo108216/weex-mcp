import unittest


class SecretProviderTests(unittest.TestCase):
    def test_plaintext_fingerprint_changes_when_any_credential_field_changes(self):
        from weex_mcp.secret_provider import PlaintextSecretProvider
        from weex_mcp.tenant_context import TenantContext

        provider = PlaintextSecretProvider()
        context = TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request")

        def resolve(api_key: str, api_secret: str, api_passphrase: str):
            return provider.resolve(
                credential_argument={
                    "type": "plaintext",
                    "api_key": api_key,
                    "api_secret": api_secret,
                    "api_passphrase": api_passphrase,
                },
                context=context,
                required_permission="USER_DATA",
            )

        fingerprints = {
            resolve("AK", "SK", "PP").fingerprint,
            resolve("AK_CHANGED", "SK", "PP").fingerprint,
            resolve("AK", "SK_CHANGED", "PP").fingerprint,
            resolve("AK", "SK", "PP_CHANGED").fingerprint,
        }
        self.assertEqual(len(fingerprints), 4)
        for fingerprint in fingerprints:
            self.assertNotIn("AK", fingerprint)
            self.assertNotIn("SK", fingerprint)
            self.assertNotIn("PP", fingerprint)

    def test_plaintext_provider_resolves_request_credential_without_persisting_secret(self):
        from weex_mcp.secret_provider import PlaintextSecretProvider
        from weex_mcp.tenant_context import TenantContext

        provider = PlaintextSecretProvider()
        resolved = provider.resolve(
            credential_argument={
                "type": "plaintext",
                "api_key": "AK_INLINE",
                "api_secret": "SK_INLINE",
                "api_passphrase": "PP_INLINE",
            },
            context=TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request"),
            required_permission="USER_DATA",
        )

        self.assertEqual(resolved.api_key, "AK_INLINE")
        self.assertEqual(resolved.api_secret, "SK_INLINE")
        self.assertEqual(resolved.api_passphrase, "PP_INLINE")
        self.assertNotIn(
            "USER_DATA",
            resolved.permissions,
            "request-scoped plaintext cannot self-assert verified WEEX permissions",
        )
        self.assertNotIn("SK_INLINE", str(resolved.safe_audit_dict()))
        self.assertNotIn("PP_INLINE", str(resolved.safe_audit_dict()))

    def test_plaintext_provider_does_not_grant_caller_declared_trade_permission(self):
        from weex_mcp.secret_provider import PlaintextSecretProvider
        from weex_mcp.tenant_context import TenantContext

        resolved = PlaintextSecretProvider().resolve(
            credential_argument={
                "type": "plaintext",
                "api_key": "AK_INLINE",
                "api_secret": "SK_INLINE",
                "api_passphrase": "PP_INLINE",
                "permissions": ["TRADE"],
            },
            context=TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request"),
            required_permission="TRADE",
        )

        self.assertNotIn("TRADE", resolved.permissions)

    def test_plaintext_provider_rejects_incomplete_request_credential(self):
        from weex_mcp.secret_provider import PlaintextSecretProvider, SecretProviderError
        from weex_mcp.tenant_context import TenantContext

        with self.assertRaises(SecretProviderError) as ctx:
            PlaintextSecretProvider().resolve(
                credential_argument={
                    "type": "plaintext",
                    "api_key": "AK_INLINE",
                    "api_secret": "SK_INLINE",
                },
                context=TenantContext(tenant_id="mcp", user_id="anonymous", session_id="request"),
                required_permission="TRADE",
            )

        self.assertEqual(ctx.exception.code, "credential_invalid")

    def test_unconfigured_secret_provider_fails_closed_without_leaking_ref(self):
        from weex_mcp.secret_provider import SecretProviderError, UnconfiguredSecretProvider
        from weex_mcp.tenant_context import TenantContext

        provider = UnconfiguredSecretProvider()
        with self.assertRaises(SecretProviderError) as ctx:
            provider.resolve(
                credential_argument={"type": "credential_ref", "ref": "credref_secret", "key_id": "key_a"},
                context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                required_permission="USER_DATA",
            )

        self.assertEqual(ctx.exception.code, "secret_provider_unconfigured")
        self.assertNotIn("credref_secret", str(ctx.exception))

    def test_static_mapping_secret_provider_resolves_credential_ref_for_tests(self):
        from weex_mcp.secret_provider import StaticMappingSecretProvider
        from weex_mcp.tenant_context import TenantContext

        provider = StaticMappingSecretProvider(
            {
                "credref_a": {
                    "api_key": "AK",
                    "api_secret": "SK",
                    "api_passphrase": "PP",
                    "permissions": ["USER_DATA"],
                }
            }
        )
        resolved = provider.resolve(
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
            required_permission="USER_DATA",
        )

        self.assertEqual(resolved.api_key, "AK")
        self.assertEqual(resolved.key_id, "key_a")
        self.assertIn("USER_DATA", resolved.permissions)
        self.assertNotIn("SK", str(resolved.safe_audit_dict()))

    def test_static_mapping_fingerprint_changes_when_ref_is_rebound_to_another_api_key(self):
        from weex_mcp.secret_provider import StaticMappingSecretProvider
        from weex_mcp.tenant_context import TenantContext

        records = {
            "credref_a": {
                "api_key": "AK_BEFORE",
                "api_secret": "SK",
                "api_passphrase": "PP",
                "permissions": ["TRADE"],
            }
        }
        provider = StaticMappingSecretProvider(records)
        arguments = {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"}
        context = TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")

        before = provider.resolve(credential_argument=arguments, context=context, required_permission="TRADE")
        records["credref_a"]["api_key"] = "AK_AFTER"
        after = provider.resolve(credential_argument=arguments, context=context, required_permission="TRADE")

        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertNotIn("AK_BEFORE", before.fingerprint)
        self.assertNotIn("AK_AFTER", after.fingerprint)

    def test_static_mapping_fingerprint_changes_when_secret_or_passphrase_changes(self):
        from weex_mcp.secret_provider import StaticMappingSecretProvider
        from weex_mcp.tenant_context import TenantContext

        record = {
            "api_key": "AK",
            "api_secret": "SK_BEFORE",
            "api_passphrase": "PP_BEFORE",
            "permissions": ["TRADE"],
        }
        provider = StaticMappingSecretProvider({"credref_a": record})
        arguments = {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"}
        context = TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")

        before = provider.resolve(credential_argument=arguments, context=context, required_permission="TRADE")
        record["api_secret"] = "SK_AFTER"
        changed_secret = provider.resolve(credential_argument=arguments, context=context, required_permission="TRADE")
        record["api_passphrase"] = "PP_AFTER"
        changed_passphrase = provider.resolve(credential_argument=arguments, context=context, required_permission="TRADE")

        self.assertEqual(len({before.fingerprint, changed_secret.fingerprint, changed_passphrase.fingerprint}), 3)



if __name__ == "__main__":
    unittest.main()
