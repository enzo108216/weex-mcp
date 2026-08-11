import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


class CredentialContextTests(unittest.TestCase):
    def test_plaintext_credential_has_fingerprint_without_exposing_secret(self):
        from weex_mcp.credential_context import CredentialContext

        credential = CredentialContext.from_argument(
            {
                "type": "plaintext",
                "api_key": "AK_INLINE",
                "api_secret": "SK_INLINE",
                "api_passphrase": "PP_INLINE",
            }
        )

        self.assertEqual(credential.kind, "plaintext")
        self.assertRegex(credential.fingerprint, r"^fp_[0-9a-f]{16}$")
        serialized = json.dumps(credential.safe_audit_dict(), sort_keys=True)
        self.assertNotIn("AK_INLINE", serialized)
        self.assertNotIn("SK_INLINE", serialized)
        self.assertNotIn("PP_INLINE", serialized)

    def test_tenant_context_rejects_empty_or_control_character_identity(self):
        from weex_mcp.tenant_context import TenantContext

        for values in (("", "user", "session"), ("tenant", "", "session"), ("tenant", "user", ""), ("tenant\n", "user", "session")):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    TenantContext(*values)

    def test_credential_ref_has_stable_fingerprint_without_exposing_ref(self):
        from weex_mcp.credential_context import CredentialContext

        credential = CredentialContext.from_argument(
            {
                "type": "credential_ref",
                "ref": "credref_super_secret_reference",
                "key_id": "weex_key_123",
                "permissions": ["USER_DATA"],
            }
        )

        self.assertEqual(credential.kind, "credential_ref")
        self.assertEqual(credential.key_id, "weex_key_123")
        self.assertIn("USER_DATA", credential.permissions)
        self.assertRegex(credential.fingerprint, r"^fp_[0-9a-f]{16}$")
        self.assertNotIn("credref_super_secret_reference", credential.safe_audit_dict().values())

    def test_caller_key_id_is_absent_from_tool_response_and_memory_audit(self):
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        class AccountAdapter:
            def get_balance(self, **kwargs):
                return {"balances": []}

        caller_key_id = "weex_0123456789abcdef0123456789abcdef"
        sink = MemoryAuditSink()
        response = build_default_service(account_adapter=AccountAdapter(), audit_sink=sink).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "key-id-redaction",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "credential_ref",
                            "ref": "credref_key_id_redaction",
                            "key_id": caller_key_id,
                            "permissions": ["USER_DATA"],
                        },
                    },
                },
            },
            context=TenantContext(tenant_id="tenant", user_id="user", session_id="session"),
        )

        result = response["result"]
        content_payload = json.loads(result["content"][0]["text"])
        self.assertNotIn(caller_key_id, json.dumps(result, sort_keys=True))
        self.assertNotIn(caller_key_id, json.dumps(result["structuredContent"], sort_keys=True))
        self.assertNotIn(caller_key_id, json.dumps(content_payload, sort_keys=True))
        self.assertNotIn(caller_key_id, json.dumps(sink.events, sort_keys=True))

        audits = [result["audit"], result["structuredContent"]["audit"], content_payload["audit"], sink.events[0]]
        fingerprints = {audit["credential_fingerprint"] for audit in audits}
        self.assertEqual(len(fingerprints), 1)
        self.assertRegex(fingerprints.pop(), r"^fp_[0-9a-f]{16}$")
        for audit in audits:
            self.assertEqual(audit["permissions"], ["USER_DATA"])

    def test_resolved_credential_safe_audit_dict_excludes_raw_key_id(self):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        caller_key_id = "weex_fedcba9876543210fedcba9876543210"
        credential = ResolvedWeexCredential(
            key_id=caller_key_id,
            api_key="api-key",
            api_secret="api-secret",
            api_passphrase="api-passphrase",
            permissions=frozenset({"TRADE"}),
            fingerprint="fp_0123456789abcdef",
        )

        safe_metadata = credential.safe_audit_dict()

        self.assertNotIn(caller_key_id, json.dumps(safe_metadata, sort_keys=True))
        self.assertEqual(safe_metadata["credential_fingerprint"], "fp_0123456789abcdef")
        self.assertEqual(safe_metadata["permissions"], ["TRADE"])

    def test_missing_credential_raises_structured_error(self):
        from weex_mcp.credential_context import CredentialContext, CredentialError

        with self.assertRaises(CredentialError) as ctx:
            CredentialContext.from_argument(None)

        self.assertEqual(ctx.exception.code, "credential_missing")

    def test_rejects_secret_material_in_query_string(self):
        from weex_mcp.credential_context import CredentialError, reject_secret_in_query

        with self.assertRaises(CredentialError) as ctx:
            reject_secret_in_query({"api_key": "AK", "symbol": "BTCUSDT"})

        self.assertEqual(ctx.exception.code, "credential_in_query")

    def test_rejects_token_and_key_query_names(self):
        from weex_mcp.credential_context import CredentialError, reject_secret_in_query

        for key in ("token", "key"):
            with self.subTest(key=key):
                with self.assertRaises(CredentialError) as ctx:
                    reject_secret_in_query({key: "secret"})
                self.assertEqual(ctx.exception.code, "credential_in_query")

    def test_redaction_removes_nested_secret_values(self):
        from weex_mcp.audit import redact_secrets

        payload = {
            "api_key": "AK",
            "nested": {
                "secret": "SK",
                "passphrase": "PP",
                "signature": "SIG",
                "ciphertext": "CIPHERTEXT",
                "safe": "visible",
            },
        }

        redacted = redact_secrets(payload)
        serialized = json.dumps(redacted, sort_keys=True)

        self.assertIn("visible", serialized)
        for forbidden in ["AK", "SK", "PP", "SIG", "CIPHERTEXT"]:
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(redacted["api_key"], "[REDACTED]")

    def test_redaction_removes_secret_assignments_embedded_in_strings(self):
        from weex_mcp.audit import redact_secrets

        payload = redact_secrets(
            {
                "message": "provider failed for api_secret=TOPSECRET and api-key: AKVALUE",
                "url": "https://example.invalid/path?signature=SIGVALUE&symbol=BTCUSDT",
            }
        )
        serialized = json.dumps(payload, sort_keys=True)

        for forbidden in ("TOPSECRET", "AKVALUE", "SIGVALUE"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("BTCUSDT", serialized)

    def test_account_and_trade_provider_errors_never_echo_provider_message(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.trade_rest import WeexTradeRestAdapter
        from weex_mcp.secret_provider import SecretProviderError
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        class LeakingProvider:
            def resolve(self, **kwargs):
                raise SecretProviderError("provider_failed", "provider failed for api_secret=TOPSECRET")

        context = TenantContext(tenant_id="tenant", user_id="user", session_id="session")
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        account_response = build_default_service(
            account_adapter=WeexAccountRestAdapter(secret_provider=LeakingProvider())
        ).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account-secret",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {"market": "spot", "trading_mode": "live", "credential": credential},
                },
            },
            context=context,
        )
        trade_response = build_default_service(
            trade_enabled=True,
            trade_adapter=WeexTradeRestAdapter(secret_provider=LeakingProvider()),
        ).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "trade-secret",
                "method": "tools/call",
                "params": {
                    "name": "trade_preview_order",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "order_type": "MARKET",
                        "quantity": "1",
                        "client_order_id": "security-preview-1",
                        "credential": credential,
                    },
                },
            },
            context=context,
        )

        for response in (account_response, trade_response):
            serialized = json.dumps(response, sort_keys=True)
            self.assertNotIn("TOPSECRET", serialized)
            self.assertNotIn("api_secret", serialized)
            self.assertEqual(response["result"]["error"]["code"], "provider_failed")

    def test_concurrent_plaintext_account_requests_do_not_cross_credentials(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        barrier = threading.Barrier(2)
        captured_keys = []
        labels = {"AK_CONCURRENT_ONE": "ACCOUNT_ONE", "AK_CONCURRENT_TWO": "ACCOUNT_TWO"}

        def transport(prepared, timeout):
            api_key = prepared["headers"]["ACCESS-KEY"]
            captured_keys.append(api_key)
            barrier.wait(timeout=2)
            return json.dumps({"balances": [{"asset": labels[api_key], "free": "1", "locked": "0"}]}).encode()

        audit = MemoryAuditSink()
        service = build_default_service(
            account_adapter=WeexAccountRestAdapter(
                secret_provider=RequestSecretProvider(),
                transport=transport,
                time_func=lambda: 1234.567,
            ),
            audit_sink=audit,
        )

        def call(api_key, api_secret, api_passphrase):
            response = service.handle_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": api_key[-3:],
                    "method": "tools/call",
                    "params": {
                        "name": "account_get_balance",
                        "arguments": {
                            "market": "spot",
                            "trading_mode": "live",
                            "credential": {
                                "type": "plaintext",
                                "api_key": api_key,
                                "api_secret": api_secret,
                                "api_passphrase": api_passphrase,
                            },
                        },
                    },
                }
            )["result"]
            return response["data"]["balances"][0]["asset"], response

        credentials = (
            ("AK_CONCURRENT_ONE", "SK_CONCURRENT_ONE", "PP_CONCURRENT_ONE"),
            ("AK_CONCURRENT_TWO", "SK_CONCURRENT_TWO", "PP_CONCURRENT_TWO"),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call, *credential) for credential in credentials]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual([label for label, _response in results], ["ACCOUNT_ONE", "ACCOUNT_TWO"])
        self.assertCountEqual(captured_keys, [credential[0] for credential in credentials])
        serialized = json.dumps([response for _label, response in results] + audit.events, sort_keys=True)
        for credential in credentials:
            for secret in credential:
                self.assertNotIn(secret, serialized)
        self.assertEqual(len({event["credential_fingerprint"] for event in audit.events}), 2)

    def test_rest_business_error_safe_details_redacts_upstream_message(self):
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        details = WeexRestBusinessError(
            code="bad_key",
            message="rejected api_secret=TOPSECRET",
        ).safe_details()

        self.assertNotIn("TOPSECRET", json.dumps(details, sort_keys=True))
if __name__ == "__main__":
    unittest.main()
