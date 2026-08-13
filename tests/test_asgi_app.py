import asyncio
import base64
import json
import time
import unittest


class FakeMarketAdapter:
    def get_symbol_price(self, *, market, symbol, price_type=None):
        return {"market": market, "symbol": symbol, "price": "1", "source": "fake"}


async def call_asgi(app, *, method="POST", path="/mcp", body=b"", headers=None, query_string=b"", modern=True):
    headers = list(headers or [])
    parsed = None
    if modern and method.upper() == "POST" and path == "/mcp":
        if not any(key.lower() == b"mcp-protocol-version" for key, _value in headers):
            headers.append((b"mcp-protocol-version", b"2026-07-28"))
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("params"), dict) and "_meta" not in parsed["params"]:
            parsed["params"]["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            body = json.dumps(parsed).encode()
        if isinstance(parsed, dict):
            if not any(key.lower() == b"mcp-method" for key, _value in headers):
                headers.append((b"mcp-method", str(parsed.get("method") or "").encode("ascii")))
            params = parsed.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if name is not None and not any(key.lower() == b"mcp-name" for key, _value in headers):
                raw_name = str(name)
                try:
                    encoded_name = raw_name.encode("ascii")
                except UnicodeEncodeError:
                    encoded_name = b"=?base64?" + base64.b64encode(raw_name.encode("utf-8")) + b"?="
                headers.append((b"mcp-name", encoded_name))
    sent = []
    events = [
        {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }
    ]

    async def receive():
        return events.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": headers,
    }
    await app(scope, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, response_body, sent


class AsgiAppTests(unittest.TestCase):
    def _modern_body(self, *, request_id, method, params=None):
        active_params = dict(params or {})
        active_params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": active_params}).encode()

    def _context_headers(self):
        return [
            (b"mcp-protocol-version", b"2026-07-28"),
            (b"x-tenant-id", b"tenant_a"),
            (b"x-user-id", b"user_a"),
            (b"x-session-id", b"session_a"),
        ]

    def test_asgi_requires_protocol_version_header(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "missing-header", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        self.assertEqual(status, 400)
        self.assertIn("header_mismatch", body.decode())
        self.assertEqual(json.loads(body)["error"]["code"], -32020)

    def test_query_credential_rejection_precedes_protocol_and_body_errors(self):
        from weex_mcp.asgi_app import build_asgi_app

        cases = (
            (b'{}', []),
            (b'{"jsonrpc":', []),
            (
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": "unsupported-version",
                    "method": "tools/list",
                    "params": {},
                }).encode(),
                [(b"mcp-protocol-version", b"1900-01-01")],
            ),
        )
        for body_value, headers in cases:
            with self.subTest(body=body_value, headers=headers):
                status, response_body, _sent = asyncio.run(
                    call_asgi(
                        build_asgi_app(),
                        body=body_value,
                        headers=headers,
                        query_string=b"token=must-not-be-in-url",
                        modern=False,
                    )
                )

                payload = json.loads(response_body)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "credential_in_query")

    def test_asgi_preserves_jsonrpc_parse_error_when_malformed_body_has_no_header(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=b'{"jsonrpc":',
                headers=[],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32700)

    def test_asgi_preserves_invalid_request_error_when_non_object_body_has_no_header(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=b"[]",
                headers=[],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32600)

    def test_asgi_requires_protocol_version_in_request_metadata(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "missing-meta", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        self.assertEqual(status, 400)
        self.assertIn("protocol_version_missing", body.decode())
        self.assertEqual(json.loads(body)["error"]["code"], -32602)

    def test_asgi_requires_protocol_metadata_when_params_are_omitted(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=json.dumps({"jsonrpc": "2.0", "id": "missing-params", "method": "tools/list"}).encode(),
                headers=[
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["id"], "missing-params")
        self.assertEqual(payload["error"]["code"], -32602)
        self.assertIn("protocol_version_missing", payload["error"]["message"])

    def test_asgi_requires_client_capabilities_in_request_metadata(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        body_payload = {
            "jsonrpc": "2.0",
            "id": "missing-capabilities",
            "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
        }
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps(body_payload).encode(),
                headers=[
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        self.assertEqual(status, 400)
        self.assertIn("client_capabilities_missing", body.decode())
        self.assertEqual(json.loads(body)["error"]["code"], -32602)

    def test_asgi_accepts_codex_and_claude_legacy_initialize_without_modern_headers(self):
        from weex_mcp.asgi_app import build_asgi_app

        for version in ("2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                status, body, _sent = asyncio.run(
                    call_asgi(
                        build_asgi_app(),
                        body=json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": f"legacy-init-{version}",
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": version,
                                    "capabilities": {},
                                    "clientInfo": {"name": "compatibility-test", "version": "1"},
                                },
                            }
                        ).encode(),
                        modern=False,
                    )
                )

                payload = json.loads(body)
                self.assertEqual(status, 200)
                self.assertEqual(payload["result"]["protocolVersion"], version)
                self.assertNotIn("resultType", payload["result"])

    def test_asgi_dispatches_legacy_tools_list_and_call_with_negotiated_header(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.service import build_default_service

        app = build_asgi_app(service=build_default_service(market_adapter=FakeMarketAdapter()))
        requests = (
            ({"jsonrpc": "2.0", "id": "legacy-list", "method": "tools/list", "params": {}}, "tools"),
            (
                {
                    "jsonrpc": "2.0",
                    "id": "legacy-call",
                    "method": "tools/call",
                    "params": {
                        "name": "market_get_symbol_price",
                        "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                    },
                },
                "ok",
            ),
        )
        for version in ("2025-06-18", "2025-11-25"):
            for request, expected_field in requests:
                with self.subTest(version=version, method=request["method"]):
                    status, body, _sent = asyncio.run(
                        call_asgi(
                            app,
                            body=json.dumps(request).encode(),
                            headers=[(b"mcp-protocol-version", version.encode("ascii"))],
                            modern=False,
                        )
                    )
                    payload = json.loads(body)
                    self.assertEqual(status, 200)
                    self.assertIn(expected_field, payload["result"])
                    self.assertNotIn("resultType", payload["result"])

    def test_slow_tool_call_does_not_block_the_asgi_event_loop(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.service import build_default_service

        class SlowMarketAdapter:
            def get_symbol_price(self, *, market, symbol, price_type=None):
                time.sleep(0.35)
                return {"market": market, "symbol": symbol, "price": "1", "source": "slow-fake"}

        app = build_asgi_app(service=build_default_service(market_adapter=SlowMarketAdapter()))
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "slow-call",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                },
            }
        ).encode()

        async def exercise():
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            request_task = asyncio.create_task(call_asgi(app, body=body))
            await asyncio.sleep(0.05)
            scheduling_delay = loop.time() - started_at
            status, _response_body, _sent = await request_task
            return scheduling_delay, status

        scheduling_delay, status = asyncio.run(exercise())
        self.assertEqual(status, 200)
        self.assertLess(scheduling_delay, 0.20)

    def test_asgi_stops_without_response_when_client_disconnects_during_body_read(self):
        from weex_mcp.asgi_app import build_asgi_app

        sent = []
        receive_count = 0

        async def exercise():
            nonlocal receive_count

            async def receive():
                nonlocal receive_count
                receive_count += 1
                if receive_count > 1:
                    raise AssertionError("ASGI attempted to read again after http.disconnect")
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "query_string": b"",
                "headers": self._context_headers(),
            }
            await build_asgi_app()(scope, receive, send)

        asyncio.run(exercise())
        self.assertEqual(receive_count, 1)
        self.assertEqual(sent, [])

    def test_asgi_rejects_token_and_key_query_parameters(self):
        from weex_mcp.asgi_app import build_asgi_app

        for query in (b"token=secret", b"key=secret", b"token="):
            with self.subTest(query=query):
                status, body, _sent = asyncio.run(
                    call_asgi(
                        build_asgi_app(),
                        body=json.dumps({"jsonrpc": "2.0", "id": "query", "method": "tools/list", "params": {}}).encode(),
                        headers=[
                            (b"mcp-protocol-version", b"2026-07-28"),
                            (b"x-tenant-id", b"tenant_a"),
                            (b"x-user-id", b"user_a"),
                            (b"x-session-id", b"session_a"),
                        ],
                        query_string=query,
                    )
                )
                self.assertEqual(status, 400)
                self.assertIn("credential_in_query", body.decode())

    def test_asgi_rejects_protocol_header_body_metadata_mismatch(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        body = {
            "jsonrpc": "2.0",
            "id": "header-mismatch",
            "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2025-11-25"}},
        }
        status, response_body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps(body).encode(),
                headers=[
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 400)
        self.assertIn("header_mismatch", response_body.decode())
        self.assertEqual(json.loads(response_body)["error"]["code"], -32020)

    def test_asgi_treats_unknown_body_version_as_header_mismatch(self):
        from weex_mcp.asgi_app import build_asgi_app

        body = {
            "jsonrpc": "2.0",
            "id": "unknown-body-version",
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "1900-01-01",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
        status, response_body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=json.dumps(body).encode(),
                headers=[
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(response_body)["error"]["code"], -32020)

    def test_asgi_returns_standard_unsupported_protocol_version_error(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=json.dumps({"jsonrpc": "2.0", "id": "bad-version", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"mcp-protocol-version", b"1900-01-01"),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32022)
        self.assertEqual(payload["error"]["data"]["supported"], ["2026-07-28"])

    def test_asgi_mcp_post_dispatches_tools_list_with_header_context(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.service import build_default_service

        app = build_asgi_app(service=build_default_service())
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "asgi_1", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                    (b"mcp-protocol-version", b"2026-07-28"),
                ],
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "asgi_1")
        self.assertIn("tools", payload["result"])

    def test_asgi_allows_missing_tenant_context_for_request_scoped_credentials(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "asgi_2", "method": "tools/list", "params": {}}).encode(),
            )
        )

        self.assertEqual(status, 200)
        self.assertIn('"tools"', body.decode())

    def test_asgi_rejects_invalid_tenant_context_headers(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=json.dumps({"jsonrpc": "2.0", "id": "invalid-context", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"x-tenant-id", b"tenant_a\n"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 401)
        self.assertIn("tenant_context_missing", body.decode())

    def test_asgi_healthz_does_not_require_context(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app()
        status, body, _sent = asyncio.run(call_asgi(app, method="GET", path="/healthz"))

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_asgi_rejects_oversized_body_before_json_parse(self):
        from weex_mcp.asgi_app import build_asgi_app

        app = build_asgi_app(max_body_bytes=8)
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=b'{"jsonrpc":"2.0","method":"tools/list"}',
                headers=[
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 413)
        self.assertIn("request_too_large", body.decode())

    def test_asgi_rejects_mcp_without_required_bearer_token_when_configured(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.settings import Settings

        app = build_asgi_app(settings=Settings(service_auth_token="t" * 32))
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "asgi_3", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 401)
        self.assertIn("service_auth_failed", body.decode())

    def test_asgi_accepts_mcp_with_required_bearer_token_when_configured(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.settings import Settings

        app = build_asgi_app(settings=Settings(service_auth_token="t" * 32))
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "asgi_4", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"authorization", f"Bearer {'t' * 32}".encode()),
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 200)
        self.assertIn("tools", json.loads(body)["result"])

    def test_asgi_reuses_injected_service_settings_when_settings_argument_is_omitted(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.service import build_default_service
        from weex_mcp.settings import Settings

        service = build_default_service(settings=Settings(service_auth_token="t" * 32))
        app = build_asgi_app(service=service)
        status, body, _sent = asyncio.run(
            call_asgi(
                app,
                body=json.dumps({"jsonrpc": "2.0", "id": "asgi-service-settings", "method": "tools/list", "params": {}}).encode(),
                headers=[
                    (b"x-tenant-id", b"tenant_a"),
                    (b"x-user-id", b"user_a"),
                    (b"x-session-id", b"session_a"),
                ],
            )
        )

        self.assertEqual(status, 401)
        self.assertIn("service_auth_failed", body.decode())

    def test_asgi_rejects_missing_or_mismatched_mcp_method_header(self):
        from weex_mcp.asgi_app import build_asgi_app

        body = self._modern_body(request_id="method-header", method="tools/list")
        for mirrored in (None, b"server/discover"):
            headers = self._context_headers()
            if mirrored is not None:
                headers.append((b"mcp-method", mirrored))
            with self.subTest(mirrored=mirrored):
                status, response_body, _sent = asyncio.run(
                    call_asgi(build_asgi_app(), body=body, headers=headers, modern=False)
                )
                payload = json.loads(response_body)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], -32020)

    def test_asgi_validates_mcp_name_and_base64_sentinel(self):
        from weex_mcp.asgi_app import build_asgi_app
        from weex_mcp.service import build_default_service

        app = build_asgi_app(service=build_default_service(market_adapter=FakeMarketAdapter()))
        body = self._modern_body(
            request_id="name-header",
            method="tools/call",
            params={"name": "market_get_symbol_price", "arguments": {"market": "spot", "symbol": "BTCUSDT"}},
        )
        cases = (None, b"other_tool", b"=?base64?not-valid!?=")
        for mirrored in cases:
            headers = self._context_headers() + [(b"mcp-method", b"tools/call")]
            if mirrored is not None:
                headers.append((b"mcp-name", mirrored))
            with self.subTest(mirrored=mirrored):
                status, response_body, _sent = asyncio.run(
                    call_asgi(app, body=body, headers=headers, modern=False)
                )
                payload = json.loads(response_body)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], -32020)

        encoded = b"=?base64?" + base64.b64encode(b"market_get_symbol_price") + b"?="
        status, response_body, _sent = asyncio.run(
            call_asgi(
                app,
                body=body,
                headers=self._context_headers() + [(b"mcp-method", b"tools/call"), (b"mcp-name", encoded)],
                modern=False,
            )
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response_body)["result"]["ok"])

    def test_asgi_rejects_untrusted_origin(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=self._modern_body(request_id="origin", method="tools/list"),
                headers=self._context_headers() + [(b"mcp-method", b"tools/list"), (b"origin", b"https://evil.example")],
                modern=False,
            )
        )

        self.assertEqual(status, 403)
        self.assertIn("origin_forbidden", body.decode())

    def test_asgi_returns_404_jsonrpc_error_for_unknown_method(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=self._modern_body(request_id="unknown-method", method="unknown/method"),
                headers=self._context_headers() + [(b"mcp-method", b"unknown/method")],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], -32601)

    def test_asgi_unknown_tool_is_protocol_error_not_call_tool_result(self):
        from weex_mcp.asgi_app import build_asgi_app

        status, body, _sent = asyncio.run(
            call_asgi(
                build_asgi_app(),
                body=self._modern_body(request_id="unknown-tool", method="tools/call", params={"name": "does_not_exist", "arguments": {}}),
                headers=self._context_headers() + [(b"mcp-method", b"tools/call"), (b"mcp-name", b"does_not_exist")],
                modern=False,
            )
        )

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertNotIn("result", payload)
        self.assertEqual(payload["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
