import json
import unittest


class HttpAppTests(unittest.TestCase):
    def test_http_post_dispatches_jsonrpc_tools_list(self):
        from weex_mcp.http_app import handle_mcp_post
        from weex_mcp.tenant_context import TenantContext

        response = handle_mcp_post(
            json.dumps({"jsonrpc": "2.0", "id": "http_1", "method": "tools/list", "params": {}}).encode(),
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        body = json.loads(response.body)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["id"], "http_1")
        self.assertIn("tools", body["result"])

    def test_http_post_rejects_secret_material_in_query(self):
        from weex_mcp.http_app import handle_mcp_post
        from weex_mcp.tenant_context import TenantContext

        response = handle_mcp_post(
            json.dumps({"jsonrpc": "2.0", "id": "http_2", "method": "tools/list", "params": {}}).encode(),
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
            query_params={"api_key": "AK_SHOULD_NOT_APPEAR"},
        )

        self.assertEqual(response.status, 400)
        self.assertNotIn("AK_SHOULD_NOT_APPEAR", response.body)
        self.assertIn("credential_in_query", response.body)

    def test_http_post_returns_jsonrpc_parse_error_for_malformed_json(self):
        from weex_mcp.http_app import handle_mcp_post
        from weex_mcp.tenant_context import TenantContext

        response = handle_mcp_post(
            b'{"jsonrpc":',
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        payload = json.loads(response.body)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertIsNone(payload["id"])
        self.assertEqual(payload["error"]["code"], -32700)
        self.assertNotIn("ok", payload)

    def test_http_post_returns_jsonrpc_invalid_request_for_non_object_json(self):
        from weex_mcp.http_app import handle_mcp_post
        from weex_mcp.tenant_context import TenantContext

        for raw_body in (b"[]", b"null", b'"request"'):
            with self.subTest(raw_body=raw_body):
                response = handle_mcp_post(
                    raw_body,
                    context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                )
                payload = json.loads(response.body)
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["jsonrpc"], "2.0")
                self.assertIsNone(payload["id"])
                self.assertEqual(payload["error"]["code"], -32600)
                self.assertNotIn("ok", payload)


if __name__ == "__main__":
    unittest.main()
