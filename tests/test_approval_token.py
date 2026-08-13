import unittest


class ApprovalTokenTests(unittest.TestCase):
    def test_settings_do_not_ship_a_forgeable_shared_secret(self):
        from weex_mcp.settings import Settings

        self.assertEqual(Settings().approval_token_secret, "")

    def test_default_preview_id_ttl_is_five_minutes(self):
        from weex_mcp.approval_token import ApprovalTokenCodec
        from weex_mcp.settings import Settings

        settings = Settings()
        self.assertEqual(settings.approval_token_ttl_seconds, 300)

        clock = [1_700_000_000.0]
        codec = ApprovalTokenCodec(
            secret="s" * 32,
            ttl_seconds=settings.approval_token_ttl_seconds,
            time_func=lambda: clock[0],
        )
        token = codec.issue(
            action="order",
            credential_fingerprint="fp_123",
            summary={"action": "order"},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        claims = codec.verify(token)

        self.assertEqual(claims["expires_at"] - claims["issued_at"], 300)

    def test_issue_and_verify_round_trip_without_server_state(self):
        from weex_mcp.approval_token import ApprovalTokenCodec

        clock = [1_700_000_000.0]
        codec = ApprovalTokenCodec(secret="s" * 32, ttl_seconds=120, time_func=lambda: clock[0])
        token = codec.issue(
            action="order",
            credential_fingerprint="fp_123",
            summary={"action": "order", "request_body": {"newClientOrderId": "client-1"}},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        claims = codec.verify(token)

        self.assertEqual(claims["action"], "order")
        self.assertEqual(claims["credential_fingerprint"], "fp_123")
        self.assertEqual(claims["summary"]["request_body"]["newClientOrderId"], "client-1")
        self.assertNotIn("api_secret", token)

    def test_verify_rejects_tampered_and_expired_tokens(self):
        from weex_mcp.approval_token import ApprovalTokenCodec, ApprovalTokenError

        clock = [1_700_000_000.0]
        codec = ApprovalTokenCodec(secret="s" * 32, ttl_seconds=120, time_func=lambda: clock[0])
        token = codec.issue(
            action="cancel_order",
            credential_fingerprint="fp_123",
            summary={"action": "cancel_order", "request_query": {"orderId": "1"}},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        with self.assertRaises(ApprovalTokenError) as tampered:
            codec.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))
        self.assertEqual(tampered.exception.code, "approval_token_invalid")

        clock[0] += 121
        with self.assertRaises(ApprovalTokenError) as expired:
            codec.verify(token)
        self.assertEqual(expired.exception.code, "approval_token_expired")

    def test_verify_rejects_token_at_exact_expiration_instant(self):
        from weex_mcp.approval_token import ApprovalTokenCodec, ApprovalTokenError

        clock = [1_700_000_000.0]
        codec = ApprovalTokenCodec(secret="s" * 32, ttl_seconds=120, time_func=lambda: clock[0])
        token = codec.issue(
            action="order",
            credential_fingerprint="fp_123",
            summary={"action": "order"},
            risk={"status": "review_required"},
            risk_signature="risk-1",
        )

        clock[0] += 120
        with self.assertRaises(ApprovalTokenError) as expired:
            codec.verify(token)

        self.assertEqual(expired.exception.code, "approval_token_expired")

    def test_issue_rejects_non_json_claims_with_a_stable_error(self):
        from weex_mcp.approval_token import ApprovalTokenCodec, ApprovalTokenError

        codec = ApprovalTokenCodec(secret="s" * 32)

        with self.assertRaises(ApprovalTokenError) as raised:
            codec.issue(
                action="order",
                credential_fingerprint="fp_123",
                summary={"request_body": {"not_json": object()}},
                risk={"status": "review_required"},
                risk_signature="risk-1",
            )

        self.assertEqual(raised.exception.code, "approval_token_issue_failed")


if __name__ == "__main__":
    unittest.main()
