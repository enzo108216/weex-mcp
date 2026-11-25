from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


class FakeSecretProvider:
    def __init__(self):
        self.calls = []

    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        self.calls.append((credential_argument, context, required_permission))
        return ResolvedWeexCredential(
            key_id=credential_argument["key_id"],
            api_key="AK_PARTNER",
            api_secret="SK_PARTNER",
            api_passphrase="PP_PARTNER",
            permissions=frozenset({"USER_DATA"}),
            fingerprint="fp_partner",
        )


class PartnerAdapterTests(unittest.TestCase):
    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="tenant", user_id="user", session_id="session")

    def _credential(self):
        return {"type": "credential_ref", "key_id": "partner-key", "ref": "partner-ref"}

    def test_seven_partner_methods_use_fixed_wire_contracts_and_user_data_credential(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        prepared_requests = []

        def transport(prepared, timeout_seconds):
            prepared_requests.append((prepared, timeout_seconds))
            return (
                200,
                {"X-USED-WEIGHT-1M": "20", "x-remaining-weight-1m": "80"},
                b'{"code":"00000","data":{"records":[]}}',
            )

        adapter = WeexPartnerRestAdapter(
            secret_provider=provider,
            transport=transport,
            time_func=lambda: 1234.567,
        )
        common = {"credential_argument": self._credential(), "context": self._context()}
        results = [
            adapter.get_affiliate_uids(
                uid="1", start_time="1000", end_time="2000", page="1", page_size="100", **common
            ),
            adapter.get_channel_user_trade_and_asset(
                uid="2", start_time="1000", end_time="2000", page="2", page_size="50", **common
            ),
            adapter.get_affiliate_commission(
                uid="3",
                start_time="1000",
                end_time="2000",
                coin="USDT",
                product_type="SPOT",
                page="3",
                page_size="25",
                **common,
            ),
            adapter.query_sub_channel_transactions(
                sub_uid="4",
                start_time="1000",
                end_time="2000",
                product_type="FUTURES",
                page_num="4",
                page_size="10",
                **common,
            ),
            adapter.verify_referrals(
                user_ids=["5", "6"], expected_environment="partner_production", **common
            ),
            adapter.get_referral_assets(
                user_id="7",
                start_time="2026-01-01",
                end_time="2026-01-31",
                expected_environment="partner_production",
                **common,
            ),
            adapter.get_referral_deal_data(
                user_ids=["8", "9"],
                start_time="2026-02-01",
                end_time="2026-02-28",
                expected_environment="partner_production",
                **common,
            ),
        ]

        expected = (
            (
                "GET",
                "/api/v3/rebate/affiliate/getAffiliateUIDs",
                "uid=1&startTime=1000&endTime=2000&page=1&pageSize=100",
                {},
                20,
            ),
            (
                "GET",
                "/api/v3/rebate/affiliate/getChannelUserTradeAndAsset",
                "uid=2&startTime=1000&endTime=2000&page=2&pageSize=50",
                {},
                20,
            ),
            (
                "GET",
                "/api/v3/rebate/affiliate/getAffiliateCommission",
                "uid=3&startTime=1000&endTime=2000&coin=USDT&productType=SPOT&page=3&pageSize=25",
                {},
                20,
            ),
            (
                "POST",
                "/api/v3/rebate/affiliate/querySubChannelTransactions",
                "",
                {
                    "subUid": 4,
                    "startTime": 1000,
                    "endTime": 2000,
                    "productType": "FUTURES",
                    "pageNum": 4,
                    "pageSize": 10,
                },
                10,
            ),
            ("GET", "/api/v3/agency/verifyReferrals", "userIds=5%2C6", {}, 10),
            (
                "GET",
                "/api/v3/agency/getAssert",
                "userId=7&startTime=2026-01-01&endTime=2026-01-31",
                {},
                10,
            ),
            (
                "GET",
                "/api/v3/agency/getDealData",
                "userIds=8&userIds=9&startTime=2026-02-01&endTime=2026-02-28",
                {},
                10,
            ),
        )
        self.assertEqual(len(prepared_requests), 7)
        self.assertEqual(len(provider.calls), 7)
        self.assertTrue(all(call[2] == "USER_DATA" for call in provider.calls))
        for index, (method, path, query_string, body, weight) in enumerate(expected):
            with self.subTest(index=index):
                prepared, timeout_seconds = prepared_requests[index]
                self.assertEqual(timeout_seconds, 30.0)
                self.assertEqual(prepared["method"], method)
                self.assertEqual(prepared["path"], path)
                self.assertEqual(prepared["query_string"], query_string)
                self.assertEqual(prepared["body"], body)
                self.assertEqual(prepared["data"], None if method == "GET" else json.dumps(body, separators=(",", ":")).encode())
                self.assertEqual(prepared["headers"]["locale"], "en-US")
                self.assertNotIn("SK_PARTNER", prepared["url"])
                result = results[index]
                self.assertEqual(result["method"], method)
                self.assertEqual(result["path"], path)
                self.assertEqual(result["weight"], weight)
                self.assertEqual(result["rate_limit"], {"used": {"1M": 20}, "remaining": {"1M": 80}})
                self.assertEqual(result["retry"], {"automatic": False})
                self.assertEqual(result["environment"], "partner_production")
                self.assertEqual(result["capability_mode"], "read_only_query")
                self.assertEqual(result["data"], {"code": "00000", "data": {"records": []}})

        first = prepared_requests[0][0]
        message = f"1234567GET{first['path']}?{first['query_string']}"
        expected_signature = base64.b64encode(
            hmac.new(b"SK_PARTNER", message.encode(), hashlib.sha256).digest()
        ).decode()
        self.assertEqual(first["headers"]["ACCESS-SIGN"], expected_signature)
        post = prepared_requests[3][0]
        post_body = json.dumps(post["body"], separators=(",", ":"))
        post_message = f"1234567POST{post['path']}{post_body}"
        expected_post_signature = base64.b64encode(
            hmac.new(b"SK_PARTNER", post_message.encode(), hashlib.sha256).digest()
        ).decode()
        self.assertEqual(post["headers"]["ACCESS-SIGN"], expected_post_signature)

    def test_partner_test_null_time_requires_expected_and_actual_test_environment(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        requests = []
        test_adapter = WeexPartnerRestAdapter(
            secret_provider=provider,
            base_url="https://partner-fixture.weex.tech",
            transport=lambda prepared, timeout: requests.append(prepared) or (200, {}, b'{"data":[]}'),
        )
        result = test_adapter.query_sub_channel_transactions(
            product_type="SPOT",
            start_time=None,
            end_time=None,
            expected_environment="partner_test",
            credential_argument=self._credential(),
            context=self._context(),
        )
        self.assertEqual(requests[0]["body"], {"startTime": None, "endTime": None, "productType": "SPOT"})
        self.assertEqual(result["environment"], "partner_test")
        self.assertNotIn("partner-fixture.weex.tech", json.dumps(result, sort_keys=True))

        invalid_calls = (
            lambda: test_adapter.query_sub_channel_transactions(
                product_type="SPOT",
                start_time=None,
                end_time=None,
                credential_argument=self._credential(),
                context=self._context(),
            ),
            lambda: WeexPartnerRestAdapter(
                secret_provider=provider,
                transport=lambda prepared, timeout: (200, {}, b"{}"),
            ).query_sub_channel_transactions(
                product_type="SPOT",
                start_time=None,
                end_time=None,
                expected_environment="partner_test",
                credential_argument=self._credential(),
                context=self._context(),
            ),
            lambda: test_adapter.get_affiliate_uids(
                expected_environment="partner_production",
                credential_argument=self._credential(),
                context=self._context(),
            ),
        )
        calls_before = len(provider.calls)
        requests_before = len(requests)
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(PartnerAdapterError):
                    call()
        self.assertEqual(len(provider.calls), calls_before)
        self.assertEqual(len(requests), requests_before)

    def test_partner_origin_policy_allows_only_production_or_nonbare_test_origin(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        production = WeexPartnerRestAdapter(secret_provider=provider)
        testing = WeexPartnerRestAdapter(
            secret_provider=provider,
            base_url="https://a-b.weex.tech",
        )
        self.assertEqual(production.environment, "partner_production")
        self.assertEqual(testing.environment, "partner_test")

        invalid = (
            "http://api-spot.weex.com",
            "https://weex.tech",
            "https://api-spot.weex.com/",
            "https://api-spot.weex.com/path",
            "https://api-spot.weex.com:443",
            "https://api-spot.weex.com?x=1",
            "https://api-spot.weex.com#fragment",
            "https://user@api-spot.weex.com",
        )
        for base_url in invalid:
            with self.subTest(base_url=base_url):
                with self.assertRaises(PartnerAdapterError) as ctx:
                    WeexPartnerRestAdapter(secret_provider=provider, base_url=base_url)
                self.assertEqual(ctx.exception.code, "partner_origin_invalid")
                self.assertNotIn(base_url, json.dumps(ctx.exception.safe_details(), sort_keys=True))

        for timeout_seconds in (True, 0, -1, math.nan, math.inf):
            with self.subTest(timeout_seconds=timeout_seconds):
                with self.assertRaises(ValueError):
                    WeexPartnerRestAdapter(
                        secret_provider=provider,
                        timeout_seconds=timeout_seconds,
                    )

    def test_expected_environment_binds_all_partner_methods_before_credentials(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        adapter = WeexPartnerRestAdapter(secret_provider=provider)
        invalid_calls = (
            lambda: adapter.verify_referrals(
                user_ids=["1"],
                expected_environment="partner_test",
                credential_argument=self._credential(),
                context=self._context(),
            ),
            lambda: adapter.get_referral_assets(
                user_id="1",
                start_time="2026-01-01",
                end_time="2026-01-31",
                expected_environment="partner_test",
                credential_argument=self._credential(),
                context=self._context(),
            ),
            lambda: adapter.get_referral_deal_data(
                start_time="2026-01-01",
                end_time="2026-01-31",
                expected_environment="partner_test",
                credential_argument=self._credential(),
                context=self._context(),
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(PartnerAdapterError) as ctx:
                    call()
                self.assertEqual(ctx.exception.code, "expected_partner_environment_mismatch")
        self.assertEqual(provider.calls, [])

    def test_partner_timeout_is_single_attempt_and_errors_are_stable_and_redacted(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        attempts = []

        def timeout_transport(prepared, timeout_seconds):
            attempts.append(timeout_seconds)
            raise TimeoutError("AK_PARTNER https://private.weex.tech SECRET_BODY")

        adapter = WeexPartnerRestAdapter(secret_provider=provider, transport=timeout_transport)
        with self.assertRaises(PartnerAdapterError) as ctx:
            adapter.verify_referrals(
                user_ids=["1"],
                credential_argument=self._credential(),
                context=self._context(),
            )
        self.assertEqual(attempts, [30.0])
        self.assertEqual(ctx.exception.code, "partner_transport_error")
        serialized = json.dumps(ctx.exception.safe_details(), sort_keys=True)
        self.assertNotIn("AK_PARTNER", serialized)
        self.assertNotIn("private.weex.tech", serialized)
        self.assertNotIn("SECRET_BODY", serialized)

        cases = (
            (401, b'{"code":"-1040","msg":"SECRET"}', "partner_authentication_error"),
            (403, b'{"code":"-1050","msg":"SECRET"}', "partner_permission_error"),
            (429, b'{"code":"429","msg":"SECRET"}', "partner_rate_limit"),
            (429, b'{"code":"-1050","msg":"SECRET"}', "partner_rate_limit"),
            (429, b'{"code":{"nested":"SECRET"}}', "partner_rate_limit"),
            (400, b'{"code":"-1128","msg":"SECRET"}', "partner_validation_error"),
            (503, b'{"code":"-1000","msg":"SECRET"}', "partner_upstream_error"),
            (503, b"gateway down SECRET", "partner_upstream_error"),
            (302, b'<a href="https://evil.example">redirect</a>', "partner_redirect_forbidden"),
            (200, b'{"code":"-1050","msg":"SECRET"}', "partner_permission_error"),
            (200, b'{"code":"AK_PARTNER","msg":"SECRET"}', "partner_upstream_error"),
            (200, b'{"code":{"nested":"SECRET"}}', "partner_response_schema_error"),
            (200, b'{"code":"00000","success":false,"msg":"SECRET"}', "partner_upstream_error"),
            (200, b"not-json SECRET", "partner_response_schema_error"),
            (200, b'"scalar"', "partner_response_schema_error"),
        )
        for status, body, expected_code in cases:
            with self.subTest(status=status, expected_code=expected_code):
                error_adapter = WeexPartnerRestAdapter(
                    secret_provider=provider,
                    transport=lambda prepared, timeout, status=status, body=body: (
                        status,
                        (
                            {"X-USED-WEIGHT-1M": "99"}
                            if status == 429
                            else {
                                "X-USED-WEIGHT-1M": "7",
                                "X-REMAINING-WEIGHT-1M": "93",
                            }
                        ),
                        body,
                    ),
                )
                with self.assertRaises(PartnerAdapterError) as error_ctx:
                    error_adapter.verify_referrals(
                        user_ids=["1"],
                        credential_argument=self._credential(),
                        context=self._context(),
                    )
                self.assertEqual(error_ctx.exception.code, expected_code)
                safe = json.dumps(error_ctx.exception.safe_details(), sort_keys=True)
                self.assertNotIn("SECRET", safe)
                self.assertNotIn("evil.example", safe)
                self.assertNotIn("AK_PARTNER", safe)
                if status == 429:
                    self.assertEqual(
                        error_ctx.exception.safe_details()["rate_limit"]["used"],
                        {"1M": 99},
                    )
                    self.assertFalse(error_ctx.exception.safe_details()["retry_automatic"])
                if expected_code == "partner_response_schema_error":
                    self.assertEqual(error_ctx.exception.safe_details()["http_status"], 200)
                    self.assertEqual(
                        error_ctx.exception.safe_details()["rate_limit"],
                        {"used": {"1M": 7}, "remaining": {"1M": 93}},
                    )

    def test_default_transport_uses_no_redirect_opener_and_returns_redirect_for_classification(self):
        from weex_mcp.adapters.partner import _default_transport

        prepared = {
            "method": "GET",
            "url": "https://api-spot.weex.com/api/v3/agency/verifyReferrals?userIds=1",
            "headers": {},
            "data": None,
        }
        http_error = HTTPError(
            prepared["url"],
            302,
            "Found",
            {"Location": "https://evil.example"},
            io.BytesIO(b"redirect"),
        )
        with patch("weex_mcp.adapters.partner._open_no_redirect", side_effect=http_error) as opener:
            status, headers, body = _default_transport(prepared, 30.0)
        opener.assert_called_once()
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "https://evil.example")
        self.assertEqual(body, b"redirect")

    def test_partner_argument_validation_fails_before_credential_or_transport(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter

        provider = FakeSecretProvider()
        requests = []
        adapter = WeexPartnerRestAdapter(
            secret_provider=provider,
            transport=lambda prepared, timeout: requests.append(prepared) or (200, {}, b"{}"),
        )
        invalid_calls = (
            lambda: adapter.get_affiliate_uids(uid="0", credential_argument=self._credential(), context=self._context()),
            lambda: adapter.get_affiliate_uids(
                start_time="1", credential_argument=self._credential(), context=self._context()
            ),
            lambda: adapter.get_affiliate_uids(
                page="0", credential_argument=self._credential(), context=self._context()
            ),
            lambda: adapter.get_affiliate_commission(
                coin="ETH", credential_argument=self._credential(), context=self._context()
            ),
            lambda: adapter.verify_referrals(
                user_ids=["1", "1"], credential_argument=self._credential(), context=self._context()
            ),
            lambda: adapter.get_referral_assets(
                user_id="1",
                start_time="2026-02-30",
                end_time="2026-03-01",
                credential_argument=self._credential(),
                context=self._context(),
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(provider.calls, [])
        self.assertEqual(requests, [])

    def test_test_origin_success_payload_is_redacted_and_production_payload_is_preserved(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter

        origin = "https://private-partner.weex.tech"
        for leaked_value in (f"{origin}/secret", "private-partner.weex.tech/secret"):
            with self.subTest(leaked_value=leaked_value):
                adapter = WeexPartnerRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    base_url=origin,
                    transport=lambda prepared, timeout, leaked_value=leaked_value: (
                        200,
                        {},
                        json.dumps({"data": {"url": leaked_value}}).encode(),
                    ),
                )
                result = adapter.verify_referrals(
                    user_ids=["1"],
                    credential_argument=self._credential(),
                    context=self._context(),
                )
                serialized = json.dumps(result, sort_keys=True)
                self.assertNotIn("private-partner.weex.tech", serialized)
                self.assertIn("***", serialized)

        production_origin = "https://api-spot.weex.com"
        production = WeexPartnerRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: (
                200,
                {},
                json.dumps({"data": {"url": f"{production_origin}/public"}}).encode(),
            ),
        ).verify_referrals(
            user_ids=["1"],
            credential_argument=self._credential(),
            context=self._context(),
        )
        self.assertEqual(production["data"]["data"]["url"], f"{production_origin}/public")

    def test_test_origin_redaction_fails_closed_only_when_rescan_finds_a_residual(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        origin = "https://private-partner.weex.tech"
        adapter = WeexPartnerRestAdapter(
            secret_provider=FakeSecretProvider(),
            base_url=origin,
            transport=lambda prepared, timeout: (
                200,
                {},
                json.dumps({"data": {"url": origin}}).encode(),
            ),
        )
        with patch(
            "weex_mcp.adapters.partner._redact_origin_content",
            side_effect=lambda value, selected_origin: value,
        ):
            with self.assertRaises(PartnerAdapterError) as ctx:
                adapter.verify_referrals(
                    user_ids=["1"],
                    credential_argument=self._credential(),
                    context=self._context(),
                )
        self.assertEqual(ctx.exception.code, "partner_output_redaction_failed")

    def test_success_payload_redacts_resolved_credential_literals_in_keys_and_values(self):
        from weex_mcp.adapters.partner import WeexPartnerRestAdapter

        adapter = WeexPartnerRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: (
                200,
                {},
                json.dumps(
                    {
                        "data": {
                            "note": "AK_PARTNER / SK_PARTNER / PP_PARTNER",
                            "AK_PARTNER": "PP_PARTNER",
                        }
                    }
                ).encode(),
            ),
        )
        result = adapter.verify_referrals(
            user_ids=["1"],
            credential_argument=self._credential(),
            context=self._context(),
        )

        serialized = json.dumps(result, sort_keys=True)
        for secret in ("AK_PARTNER", "SK_PARTNER", "PP_PARTNER"):
            self.assertNotIn(secret, serialized)
        self.assertIn("***", serialized)

    def test_selected_origin_is_not_projected_from_error_business_code(self):
        from weex_mcp.adapters.partner import PartnerAdapterError, WeexPartnerRestAdapter

        for origin, hostname in (
            ("https://api-spot.weex.com", "api-spot.weex.com"),
            ("https://private-partner.weex.tech", "private-partner.weex.tech"),
        ):
            with self.subTest(origin=origin):
                adapter = WeexPartnerRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    base_url=origin,
                    transport=lambda prepared, timeout, hostname=hostname: (
                        400,
                        {},
                        json.dumps({"code": hostname, "msg": "hidden"}).encode(),
                    ),
                )
                with self.assertRaises(PartnerAdapterError) as ctx:
                    adapter.verify_referrals(
                        user_ids=["1"],
                        credential_argument=self._credential(),
                        context=self._context(),
                    )
                self.assertEqual(ctx.exception.code, "partner_validation_error")
                self.assertNotIn(hostname, json.dumps(ctx.exception.safe_details(), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
