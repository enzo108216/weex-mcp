import base64
import hashlib
import hmac
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
VALID_STATE = "merchantState_20261124.ABC"


class RedirectingBrokerHandler(BaseHTTPRequestHandler):
    mode = "token"
    captured_requests = []

    def log_message(self, format, *args):
        return

    def _send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self):
        self.send_response(302)
        self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/capture")
        self.end_headers()

    def do_POST(self):
        if self.path == "/v1/oauth/token":
            if self.mode == "token":
                self._redirect()
            else:
                self._send_json({"code": "00000", "data": {"accessToken": "ACCESS_TOKEN"}})
            return
        if self.path == "/v1/oauth/resource/create-api":
            if self.mode == "create":
                self._redirect()
            else:
                self._send_json(
                    {
                        "code": "00000",
                        "data": {"apiKey": "AK", "secret": "SK", "passphrase": "PP"},
                    }
                )
            return
        self.send_error(404)

    def do_GET(self):
        if self.path != "/capture":
            self.send_error(404)
            return
        type(self).captured_requests.append(dict(self.headers.items()))
        if self.mode == "token":
            self._send_json({"code": "00000", "data": {"accessToken": "REDIRECTED_TOKEN"}})
        else:
            self._send_json(
                {
                    "code": "00000",
                    "data": {"apiKey": "REDIRECTED_AK", "secret": "REDIRECTED_SK", "passphrase": "REDIRECTED_PP"},
                }
            )


class FakeBrokerOAuthAdapter:
    def __init__(self):
        self.generate_calls = []
        self.exchange_calls = []

    def generate_authorization_url(self, **kwargs):
        self.generate_calls.append(kwargs)
        return {
            "authorization_url": "https://www.weex.com/oauth?clientId=client",
            "client_id": kwargs["client_id"],
            "redirect_uri": kwargs["redirect_uri"],
            "oauth_security": {
                "state": VALID_STATE,
                "code_verifier": RFC7636_VERIFIER,
                "code_challenge": RFC7636_CHALLENGE,
                "code_challenge_method": "S256",
            },
        }

    def exchange_code_for_api_key(self, **kwargs):
        self.exchange_calls.append(kwargs)
        return {
            "api_key": "AK_RETURNED",
            "api_secret": "SK_RETURNED",
            "api_passphrase": "PP_RETURNED",
        }


class BrokerOAuthRegistryTests(unittest.TestCase):
    def test_registry_exposes_two_broker_oauth_tools(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}

        self.assertEqual(len(tools), 52)
        self.assertIn("broker_oauth_generate_authorization_url", tools)
        self.assertIn("broker_oauth_exchange_code_for_api_key", tools)

    def test_authorization_url_schema_accepts_generated_or_complete_security_group(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        schema = tools["broker_oauth_generate_authorization_url"]["inputSchema"]
        base = {
            "client_id": "weex_merchant_123456",
            "redirect_uri": "https://merchant.example.com/oauth/weex/callback",
        }
        security = {
            "state": VALID_STATE,
            "code_verifier": RFC7636_VERIFIER,
            "code_challenge": RFC7636_CHALLENGE,
        }

        self.assertEqual(schema["required"], ["client_id", "redirect_uri"])
        self.assertEqual(
            schema["properties"]["oauth_security"]["required"],
            ["state", "code_verifier", "code_challenge"],
        )
        validate_tool_arguments(base, schema)
        validate_tool_arguments(base | {"oauth_security": security}, schema)

        invalid_security_groups = (
            {},
            {"state": VALID_STATE, "code_verifier": RFC7636_VERIFIER},
            security | {"unexpected": "value"},
        )
        for oauth_security in invalid_security_groups:
            with self.subTest(oauth_security=oauth_security):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(base | {"oauth_security": oauth_security}, schema)

    def test_exchange_schema_requires_exact_code_exchange_fields(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        schema = tools["broker_oauth_exchange_code_for_api_key"]["inputSchema"]

        self.assertEqual(
            schema["required"],
            ["client_id", "client_secret", "redirect_uri", "code", "code_verifier"],
        )
        self.assertFalse(schema["additionalProperties"])

    def test_broker_tools_publish_explicit_risk_annotations(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        generate = tools["broker_oauth_generate_authorization_url"]["annotations"]
        exchange = tools["broker_oauth_exchange_code_for_api_key"]["annotations"]

        self.assertEqual(
            generate,
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
        self.assertEqual(
            exchange,
            {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )


class BrokerOAuthAdapterTests(unittest.TestCase):
    def _run_redirect_case(self, mode):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        RedirectingBrokerHandler.mode = mode
        RedirectingBrokerHandler.captured_requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectingBrokerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = WeexBrokerOAuthAdapter(gateway_base_url=f"http://127.0.0.1:{server.server_port}")
            with self.assertRaises(BrokerOAuthError) as ctx:
                adapter.exchange_code_for_api_key(
                    client_id="client",
                    client_secret="secret",
                    redirect_uri="https://merchant.example.com/callback",
                    code="code",
                    code_verifier=RFC7636_VERIFIER,
                )
            return ctx.exception
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_token_redirect_is_not_followed(self):
        error = self._run_redirect_case("token")

        self.assertEqual(error.code, "token_exchange_failed")
        self.assertEqual(RedirectingBrokerHandler.captured_requests, [])

    def test_create_api_redirect_is_not_followed(self):
        error = self._run_redirect_case("create")

        self.assertEqual(error.code, "api_key_creation_result_unknown")
        self.assertEqual(RedirectingBrokerHandler.captured_requests, [])

    def test_generate_authorization_url_uses_supplied_rfc7636_values(self):
        from weex_mcp.adapters.broker_oauth import WeexBrokerOAuthAdapter

        adapter = WeexBrokerOAuthAdapter()
        result = adapter.generate_authorization_url(
            client_id="weex_merchant_123456",
            redirect_uri="https://merchant.example.com/oauth/weex/callback",
            oauth_security={
                "state": VALID_STATE,
                "code_verifier": RFC7636_VERIFIER,
                "code_challenge": RFC7636_CHALLENGE,
            },
        )

        parsed = urlsplit(result["authorization_url"])
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "https://www.weex.com/oauth")
        self.assertEqual(
            query,
            {
                "clientId": ["weex_merchant_123456"],
                "responseType": ["code"],
                "scope": ["create:apikey"],
                "redirectUri": ["https://merchant.example.com/oauth/weex/callback"],
                "state": [VALID_STATE],
                "codeChallenge": [RFC7636_CHALLENGE],
                "codeChallengeMethod": ["S256"],
            },
        )
        self.assertEqual(
            result["oauth_security"],
            {
                "state": VALID_STATE,
                "code_verifier": RFC7636_VERIFIER,
                "code_challenge": RFC7636_CHALLENGE,
                "code_challenge_method": "S256",
            },
        )

    def test_generate_authorization_url_creates_complete_security_group_when_omitted(self):
        from weex_mcp.adapters.broker_oauth import WeexBrokerOAuthAdapter

        adapter = WeexBrokerOAuthAdapter(
            state_func=lambda: "s" * 22,
            verifier_func=lambda: RFC7636_VERIFIER,
        )

        result = adapter.generate_authorization_url(
            client_id="client",
            redirect_uri="https://merchant.example.com/callback",
            oauth_security=None,
        )

        self.assertEqual(result["oauth_security"]["state"], "s" * 22)
        self.assertEqual(result["oauth_security"]["code_verifier"], RFC7636_VERIFIER)
        self.assertEqual(result["oauth_security"]["code_challenge"], RFC7636_CHALLENGE)

    def test_generate_authorization_url_rejects_mismatched_pkce(self):
        from weex_mcp.adapters.broker_oauth import WeexBrokerOAuthAdapter

        adapter = WeexBrokerOAuthAdapter()

        with self.assertRaisesRegex(ValueError, "code_challenge"):
            adapter.generate_authorization_url(
                client_id="client",
                redirect_uri="https://merchant.example.com/callback",
                oauth_security={
                    "state": VALID_STATE,
                    "code_verifier": RFC7636_VERIFIER,
                    "code_challenge": "x" * 43,
                },
            )

    def test_exchange_code_signs_exact_token_body_then_creates_api_key(self):
        from weex_mcp.adapters.broker_oauth import WeexBrokerOAuthAdapter

        prepared_requests = []
        responses = [
            {
                "code": "00000",
                "msg": "success",
                "data": {
                    "accessToken": "ACCESS_TOKEN",
                    "refreshToken": "REFRESH_TOKEN",
                    "tokenType": "Bearer",
                    "expiresIn": 3600,
                },
            },
            {
                "code": "00000",
                "msg": "success",
                "data": {"apiKey": "AK_VALUE", "secret": "SK_VALUE", "passphrase": "PP_VALUE"},
            },
        ]

        def transport(prepared, timeout):
            prepared_requests.append(prepared)
            return json.dumps(responses[len(prepared_requests) - 1]).encode()

        adapter = WeexBrokerOAuthAdapter(
            transport=transport,
            time_func=lambda: 1_710_000_000.0,
            nonce_func=lambda: "abc123xyz",
        )

        result = adapter.exchange_code_for_api_key(
            client_id="weex123456",
            client_secret="CLIENT_SECRET",
            redirect_uri="https://client.example.com/callback",
            code="code123",
            code_verifier="verifier1234567890123456789012345678901234abc",
        )

        self.assertEqual(
            result,
            {"api_key": "AK_VALUE", "api_secret": "SK_VALUE", "api_passphrase": "PP_VALUE"},
        )
        self.assertEqual(len(prepared_requests), 2)
        token_request = prepared_requests[0]
        expected_body = (
            '{"grantType":"authorization_code","code":"code123",'
            '"redirectUri":"https://client.example.com/callback",'
            '"codeVerifier":"verifier1234567890123456789012345678901234abc"}'
        )
        expected_message = f"1710000000000abc123xyzPOST/v1/oauth/token{expected_body}"
        expected_sign = base64.b64encode(
            hmac.new(b"CLIENT_SECRET", expected_message.encode(), hashlib.sha256).digest()
        ).decode()
        self.assertEqual(token_request["url"], "https://gateway.weex.com/v1/oauth/token")
        self.assertEqual(token_request["method"], "POST")
        self.assertEqual(token_request["data"], expected_body.encode())
        self.assertEqual(token_request["headers"]["Client-Id"], "weex123456")
        self.assertEqual(token_request["headers"]["Timestamp"], "1710000000000")
        self.assertEqual(token_request["headers"]["Nonce"], "abc123xyz")
        self.assertEqual(token_request["headers"]["Sign"], expected_sign)

        create_request = prepared_requests[1]
        self.assertEqual(create_request["url"], "https://gateway.weex.com/v1/oauth/resource/create-api")
        self.assertEqual(create_request["method"], "POST")
        self.assertIsNone(create_request["data"])
        self.assertEqual(create_request["headers"]["Authorization"], "Bearer ACCESS_TOKEN")

    def test_token_failure_does_not_call_create_api(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return b'{"code":"80003","msg":"client auth failed"}'

        adapter = WeexBrokerOAuthAdapter(transport=transport)
        with self.assertRaises(BrokerOAuthError) as ctx:
            adapter.exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "token_exchange_failed")
        self.assertEqual(ctx.exception.safe_details(), {"stage": "token_exchange", "upstream_code": "80003"})
        self.assertEqual(len(calls), 1)

    def test_already_bound_maps_81001(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        responses = [
            b'{"code":"00000","data":{"accessToken":"ACCESS_TOKEN"}}',
            b'{"code":"81001","msg":"API Key already bound"}',
        ]
        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return responses[len(calls) - 1]

        adapter = WeexBrokerOAuthAdapter(transport=transport)
        with self.assertRaises(BrokerOAuthError) as ctx:
            adapter.exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "api_key_already_bound")
        self.assertEqual(ctx.exception.safe_details()["upstream_code"], "81001")

    def test_create_api_timeout_reports_unknown_result_without_retry(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            if len(calls) == 1:
                return b'{"code":"00000","data":{"accessToken":"ACCESS_TOKEN"}}'
            raise TimeoutError("secret-bearing timeout detail")

        adapter = WeexBrokerOAuthAdapter(transport=transport)
        with self.assertRaises(BrokerOAuthError) as ctx:
            adapter.exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "api_key_creation_result_unknown")
        self.assertEqual(len(calls), 2)

    def test_non_2xx_token_response_never_enters_success_path(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        body = b'{"code":"00000","data":{"accessToken":"MUST_NOT_BE_USED"}}'
        http_error = HTTPError(
            "https://gateway.weex.com/v1/oauth/token",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        adapter = WeexBrokerOAuthAdapter()

        with patch("weex_mcp.adapters.broker_oauth._open_no_redirect", side_effect=http_error) as mocked_urlopen:
            with self.assertRaises(BrokerOAuthError) as ctx:
                adapter.exchange_code_for_api_key(
                    client_id="client",
                    client_secret="secret",
                    redirect_uri="https://merchant.example.com/callback",
                    code="code",
                    code_verifier=RFC7636_VERIFIER,
                )

        self.assertEqual(ctx.exception.code, "token_exchange_failed")
        self.assertEqual(mocked_urlopen.call_count, 1)
        self.assertTrue(http_error.fp.closed)

    def test_missing_access_token_stops_before_create_api(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return b'{"code":"00000","data":{}}'

        with self.assertRaises(BrokerOAuthError) as ctx:
            WeexBrokerOAuthAdapter(transport=transport).exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "token_exchange_failed")
        self.assertEqual(len(calls), 1)

    def test_token_response_requires_explicit_success_code(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return b'{"data":{"accessToken":"MUST_NOT_BE_USED"}}'

        with self.assertRaises(BrokerOAuthError) as ctx:
            WeexBrokerOAuthAdapter(transport=transport).exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "token_exchange_failed")
        self.assertEqual(len(calls), 1)

    def test_incomplete_api_key_success_response_reports_unknown(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        responses = [
            b'{"code":"00000","data":{"accessToken":"ACCESS_TOKEN"}}',
            b'{"code":"00000","data":{"apiKey":"AK_ONLY"}}',
        ]
        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return responses[len(calls) - 1]

        with self.assertRaises(BrokerOAuthError) as ctx:
            WeexBrokerOAuthAdapter(transport=transport).exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "api_key_creation_result_unknown")

    def test_create_api_response_requires_explicit_success_code(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        responses = [
            b'{"code":"00000","data":{"accessToken":"ACCESS_TOKEN"}}',
            b'{"data":{"apiKey":"AK","secret":"SK","passphrase":"PP"}}',
        ]
        calls = []

        def transport(prepared, timeout):
            calls.append(prepared)
            return responses[len(calls) - 1]

        with self.assertRaises(BrokerOAuthError) as ctx:
            WeexBrokerOAuthAdapter(transport=transport).exchange_code_for_api_key(
                client_id="client",
                client_secret="secret",
                redirect_uri="https://merchant.example.com/callback",
                code="code",
                code_verifier=RFC7636_VERIFIER,
            )

        self.assertEqual(ctx.exception.code, "api_key_creation_result_unknown")
        self.assertEqual(len(calls), 2)

    def test_create_api_http_408_reports_unknown_result(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError, WeexBrokerOAuthAdapter

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.body

        timeout_error = HTTPError(
            "https://gateway.weex.com/v1/oauth/resource/create-api",
            408,
            "Request Timeout",
            hdrs=None,
            fp=io.BytesIO(b'{"code":"80006","msg":"expired request"}'),
        )
        adapter = WeexBrokerOAuthAdapter()
        side_effects = [
            FakeResponse(b'{"code":"00000","data":{"accessToken":"ACCESS_TOKEN"}}'),
            timeout_error,
        ]

        with patch("weex_mcp.adapters.broker_oauth._open_no_redirect", side_effect=side_effects) as mocked_urlopen:
            with self.assertRaises(BrokerOAuthError) as ctx:
                adapter.exchange_code_for_api_key(
                    client_id="client",
                    client_secret="secret",
                    redirect_uri="https://merchant.example.com/callback",
                    code="code",
                    code_verifier=RFC7636_VERIFIER,
                )

        self.assertEqual(ctx.exception.code, "api_key_creation_result_unknown")
        self.assertEqual(mocked_urlopen.call_count, 2)


class BrokerOAuthServiceTests(unittest.TestCase):
    @staticmethod
    def _call(service, name, arguments):
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            protocol_version="2025-06-18",
        )
        return response["result"]

    def test_generate_tool_returns_complete_sensitive_security_group(self):
        from weex_mcp.service import build_default_service

        adapter = FakeBrokerOAuthAdapter()
        service = build_default_service(broker_oauth_adapter=adapter)
        result = self._call(
            service,
            "broker_oauth_generate_authorization_url",
            {
                "client_id": "client",
                "redirect_uri": "https://merchant.example.com/callback",
            },
        )

        payload = result["structuredContent"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["oauth_security"]["code_verifier"], RFC7636_VERIFIER)
        self.assertIn(RFC7636_VERIFIER, result["content"][0]["text"])
        self.assertEqual(adapter.generate_calls[0]["oauth_security"], None)

    def test_generate_tool_projects_only_approved_sensitive_fields(self):
        from weex_mcp.service import build_default_service

        class OverReturningAdapter(FakeBrokerOAuthAdapter):
            def generate_authorization_url(self, **kwargs):
                result = super().generate_authorization_url(**kwargs)
                result["accessToken"] = "SHOULD_NOT_ESCAPE"
                result["oauth_security"]["refreshToken"] = "SHOULD_NOT_ESCAPE_EITHER"
                return result

        result = self._call(
            build_default_service(broker_oauth_adapter=OverReturningAdapter()),
            "broker_oauth_generate_authorization_url",
            {"client_id": "client", "redirect_uri": "https://merchant.example.com/callback"},
        )

        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("SHOULD_NOT_ESCAPE", serialized)
        self.assertNotIn("accessToken", serialized)
        self.assertNotIn("refreshToken", serialized)

    def test_exchange_tool_returns_plaintext_credential_without_oauth_tokens(self):
        from weex_mcp.audit import MemoryAuditSink
        from weex_mcp.service import build_default_service

        adapter = FakeBrokerOAuthAdapter()
        audit = MemoryAuditSink()
        service = build_default_service(broker_oauth_adapter=adapter, audit_sink=audit)
        result = self._call(
            service,
            "broker_oauth_exchange_code_for_api_key",
            {
                "client_id": "client",
                "client_secret": "CLIENT_SECRET_INPUT",
                "redirect_uri": "https://merchant.example.com/callback",
                "code": "AUTHORIZATION_CODE_INPUT",
                "code_verifier": RFC7636_VERIFIER,
            },
        )

        payload = result["structuredContent"]
        self.assertEqual(
            payload["data"]["credential"],
            {
                "type": "plaintext",
                "api_key": "AK_RETURNED",
                "api_secret": "SK_RETURNED",
                "api_passphrase": "PP_RETURNED",
            },
        )
        serialized = json.dumps(result, sort_keys=True)
        for expected in ("AK_RETURNED", "SK_RETURNED", "PP_RETURNED"):
            self.assertIn(expected, serialized)
        for forbidden in ("CLIENT_SECRET_INPUT", "AUTHORIZATION_CODE_INPUT", "ACCESS_TOKEN", "REFRESH_TOKEN"):
            self.assertNotIn(forbidden, serialized)

        self.assertEqual(len(audit.events), 1)
        self.assertEqual(audit.events[0]["module"], "broker")
        audit_serialized = json.dumps(audit.events[0], sort_keys=True)
        for forbidden in ("CLIENT_SECRET_INPUT", RFC7636_VERIFIER, "AK_RETURNED", "SK_RETURNED", "PP_RETURNED"):
            self.assertNotIn(forbidden, audit_serialized)

    def test_exchange_tool_failure_remains_redacted(self):
        from weex_mcp.adapters.broker_oauth import BrokerOAuthError
        from weex_mcp.service import build_default_service

        class FailingAdapter(FakeBrokerOAuthAdapter):
            def exchange_code_for_api_key(self, **kwargs):
                raise BrokerOAuthError(
                    "token_exchange_failed",
                    "failure client_secret=LEAK accessToken=TOKEN codeVerifier=VERIFIER",
                    stage="token_exchange",
                    upstream_code="80003",
                )

        service = build_default_service(broker_oauth_adapter=FailingAdapter())
        result = self._call(
            service,
            "broker_oauth_exchange_code_for_api_key",
            {
                "client_id": "client",
                "client_secret": "CLIENT_SECRET_INPUT",
                "redirect_uri": "https://merchant.example.com/callback",
                "code": "code",
                "code_verifier": RFC7636_VERIFIER,
            },
        )

        serialized = json.dumps(result, sort_keys=True)
        self.assertFalse(result["structuredContent"]["ok"])
        self.assertIn("token_exchange_failed", serialized)
        for forbidden in ("LEAK", "TOKEN", "VERIFIER", "CLIENT_SECRET_INPUT", RFC7636_VERIFIER):
            self.assertNotIn(forbidden, serialized)

    def test_exchange_tool_handles_incomplete_injected_adapter_credential(self):
        from weex_mcp.service import build_default_service

        class IncompleteAdapter(FakeBrokerOAuthAdapter):
            def exchange_code_for_api_key(self, **kwargs):
                return {"api_key": "AK_ONLY"}

        service = build_default_service(broker_oauth_adapter=IncompleteAdapter())
        try:
            result = self._call(
                service,
                "broker_oauth_exchange_code_for_api_key",
                {
                    "client_id": "client",
                    "client_secret": "CLIENT_SECRET_INPUT",
                    "redirect_uri": "https://merchant.example.com/callback",
                    "code": "code",
                    "code_verifier": RFC7636_VERIFIER,
                },
            )
        except KeyError as exc:
            self.fail(f"tools/call leaked an adapter KeyError instead of returning a structured error: {exc}")

        payload = result["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "api_key_creation_result_unknown")

    def test_partial_oauth_security_is_rejected_before_adapter(self):
        from weex_mcp.service import build_default_service

        adapter = FakeBrokerOAuthAdapter()
        service = build_default_service(broker_oauth_adapter=adapter)
        result = self._call(
            service,
            "broker_oauth_generate_authorization_url",
            {
                "client_id": "client",
                "redirect_uri": "https://merchant.example.com/callback",
                "oauth_security": {"state": VALID_STATE},
            },
        )

        self.assertFalse(result["structuredContent"]["ok"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.generate_calls, [])


if __name__ == "__main__":
    unittest.main()
