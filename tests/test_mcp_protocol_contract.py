import json
import unittest


class FakeMarketAdapter:
    def get_symbol_price(self, *, market, symbol, price_type=None):
        return {"market": market, "symbol": symbol, "price": "1", "source": "fake"}


class McpProtocolContractTests(unittest.TestCase):
    def test_invalid_jsonrpc_request_ids_are_rejected(self):
        from weex_mcp.service import build_default_service

        service = build_default_service()
        for request_id in ({"nested": "id"}, ["list", "id"], True):
            with self.subTest(request_id=request_id):
                response = service.handle_jsonrpc(
                    {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}}
                )

                self.assertEqual(response["id"], None)
                self.assertEqual(response["error"]["code"], -32600)

    def test_all_modern_jsonrpc_results_declare_result_type(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(market_adapter=FakeMarketAdapter())
        listed = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}
        )
        discovered = service.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "discover", "method": "server/discover", "params": {}}
        )

        self.assertEqual(listed["result"]["resultType"], "complete")
        self.assertEqual(discovered["result"]["resultType"], "complete")

    def test_modern_server_discover_reports_supported_version_and_tools(self):
        from weex_mcp import __version__
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "discover", "method": "server/discover", "params": {}}
        )

        self.assertEqual(response["result"]["supportedVersions"], ["2026-07-28"])
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual(response["result"]["resultType"], "complete")
        self.assertEqual(response["result"]["cacheScope"], "public")
        self.assertIsInstance(response["result"]["ttlMs"], int)
        self.assertGreaterEqual(response["result"]["ttlMs"], 0)
        self.assertEqual(
            response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"],
            {"name": "weex-mcp", "version": __version__},
        )

    def test_tools_list_declares_required_2026_cache_metadata(self):
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "list-cache", "method": "tools/list", "params": {}}
        )

        self.assertEqual(response["result"]["cacheScope"], "public")
        self.assertIsInstance(response["result"]["ttlMs"], int)
        self.assertGreaterEqual(response["result"]["ttlMs"], 0)

    def test_unknown_tool_is_a_protocol_level_jsonrpc_error(self):
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "unknown-tool",
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            }
        )

        self.assertNotIn("result", response)
        self.assertEqual(response["error"]["code"], -32602)

    def test_modern_initialize_request_is_not_treated_as_legacy_handshake(self):
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "modern-init",
                "method": "initialize",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        )

        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("2026-07-28", response["error"]["message"])

    def test_codex_and_claude_legacy_initialize_versions_are_supported(self):
        from weex_mcp.service import build_default_service

        service = build_default_service()
        for version in ("2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                response = service.handle_jsonrpc(
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
                )

                self.assertEqual(response["result"]["protocolVersion"], version)
                self.assertEqual(response["result"]["serverInfo"]["name"], "weex-mcp")
                self.assertIn("tools", response["result"]["capabilities"])
                self.assertNotIn("resultType", response["result"])

    def test_legacy_tools_list_and_call_do_not_depend_on_modern_result_fields(self):
        from weex_mcp.service import build_default_service

        service = build_default_service(market_adapter=FakeMarketAdapter())
        for version in ("2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                listed = service.handle_jsonrpc(
                    {"jsonrpc": "2.0", "id": "legacy-list", "method": "tools/list", "params": {}},
                    protocol_version=version,
                )
                called = service.handle_jsonrpc(
                    {
                        "jsonrpc": "2.0",
                        "id": "legacy-call",
                        "method": "tools/call",
                        "params": {
                            "name": "market_get_symbol_price",
                            "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                        },
                    },
                    protocol_version=version,
                )

                self.assertEqual(len(listed["result"]["tools"]), 47)
                self.assertEqual(sum(tool["name"].startswith("trade_") for tool in listed["result"]["tools"]), 26)
                self.assertNotIn("resultType", listed["result"])
                self.assertNotIn("cacheScope", listed["result"])
                self.assertNotIn("ttlMs", listed["result"])
                self.assertTrue(called["result"]["ok"])
                self.assertNotIn("resultType", called["result"])

    def test_tools_call_returns_standard_mcp_content_with_structured_payload(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(market_adapter=FakeMarketAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "mcp_contract_1",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        result = response["result"]
        self.assertTrue(result["ok"])
        self.assertIn("structuredContent", result)
        self.assertEqual(result["structuredContent"]["data"]["price"], "1")
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"])["data"]["symbol"], "BTCUSDT")
        self.assertFalse(result["isError"])

    def test_tool_metadata_uses_mcp_meta_for_nonstandard_fields(self):
        from weex_mcp.tool_registry import build_default_registry

        tool = build_default_registry().list_tools()[0]
        self.assertNotIn("module", tool["annotations"])
        self.assertNotIn("permission", tool["annotations"])
        self.assertIn("_meta", tool)

    def test_tools_list_wire_schema_preserves_sensitive_property_declarations(self):
        from weex_mcp.service import build_default_service

        response = build_default_service().handle_jsonrpc(
            {"jsonrpc": "2.0", "id": "wire-schema", "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        credential_schema = tools["account_get_balance"]["inputSchema"]["properties"]["credential"]
        self.assertEqual(credential_schema["properties"]["ciphertext"]["type"], "string")
        self.assertEqual(credential_schema["oneOf"][1]["properties"]["ciphertext"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
