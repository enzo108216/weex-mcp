import unittest


class ToolMetadataContractTests(unittest.TestCase):
    def test_every_tool_has_self_describing_metadata_and_output_schema(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = build_default_registry().list_tools()
        self.assertEqual(len(tools), 17)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertIsInstance(tool.get("title"), str)
                self.assertTrue(tool["title"])
                self.assertIsInstance(tool.get("outputSchema"), dict)
                self.assertEqual(tool["outputSchema"]["type"], "object")
                self.assertIn("ok", tool["outputSchema"]["required"])
                annotations = tool.get("annotations")
                self.assertIsInstance(annotations, dict)
                for field in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                    self.assertIsInstance(annotations.get(field), bool)

    def test_trade_preview_and_confirm_risk_annotations_are_distinct(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        for name in (
            "trade_preview_order",
            "trade_preview_cancel_order",
            "trade_preview_leverage",
            "trade_preview_margin_mode",
        ):
            self.assertFalse(tools[name]["annotations"]["destructiveHint"])
        for name in (
            "trade_confirm_order",
            "trade_confirm_cancel_order",
            "trade_confirm_leverage",
            "trade_confirm_margin_mode",
        ):
            self.assertTrue(tools[name]["annotations"]["destructiveHint"])
            self.assertFalse(
                tools[name]["annotations"]["idempotentHint"],
                "stateless confirm tokens can be replayed and must not claim idempotency",
            )

    def test_top_level_fields_have_client_facing_descriptions(self):
        from weex_mcp.tool_registry import build_default_registry

        for tool in build_default_registry().list_tools():
            properties = tool["inputSchema"].get("properties", {})
            with self.subTest(tool=tool["name"]):
                for field in ("market", "trading_mode", "credential"):
                    if field in properties:
                        self.assertTrue(properties[field].get("description"))

    def test_credential_description_names_plaintext_as_default_runtime_path(self):
        from weex_mcp.tool_registry import build_default_registry

        for tool in build_default_registry().list_tools():
            credential = tool["inputSchema"].get("properties", {}).get("credential")
            if credential is None:
                continue
            with self.subTest(tool=tool["name"]):
                description = credential.get("description", "")
                self.assertIn("plaintext", description)
                self.assertIn("host resolver", description)

    def test_input_and_output_schemas_are_valid_draft_2020_12(self):
        from jsonschema import Draft202012Validator

        from weex_mcp.tool_registry import build_default_registry

        for tool in build_default_registry().list_tools():
            with self.subTest(tool=tool["name"]):
                Draft202012Validator.check_schema(tool["inputSchema"])
                Draft202012Validator.check_schema(tool["outputSchema"])
                validator = Draft202012Validator(tool["outputSchema"])
                validator.validate({"ok": True, "data": {}})
                validator.validate({"ok": False, "error": {"code": "invalid_arguments", "message": "Invalid."}})

    def test_trade_preview_structured_content_matches_advertised_output_schema(self):
        from jsonschema import Draft202012Validator

        from weex_mcp.audit import make_fingerprint
        from weex_mcp.secret_provider import ResolvedWeexCredential
        from weex_mcp.service import build_default_service

        class PreviewAdapter:
            def resolve_credential(self, *, credential_argument, context):
                return ResolvedWeexCredential(
                    key_id="inline",
                    api_key=credential_argument["api_key"],
                    api_secret=credential_argument["api_secret"],
                    api_passphrase=credential_argument["api_passphrase"],
                    permissions=frozenset({"TRADE"}),
                    fingerprint=make_fingerprint("resolved_weex_api_key", credential_argument["api_key"]),
                )

            def preview_order(self, *, arguments, credential, credential_argument, context):
                return {
                    "risk_signature": "schema-preview-risk",
                    "summary": {
                        "action": "order",
                        "market": "spot",
                        "trading_mode": "live",
                        "endpoint": "/api/v3/order",
                        "request_body": {
                            "symbol": arguments["symbol"],
                            "side": arguments["side"],
                            "type": arguments["order_type"],
                            "quantity": arguments["quantity"],
                            "newClientOrderId": arguments["client_order_id"],
                        },
                    },
                    "risk": {"status": "review_required", "alerts": []},
                }

        service = build_default_service(trade_enabled=True, trade_adapter=PreviewAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "preview-output-schema",
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
                        "client_order_id": "schema-preview-1",
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK_SCHEMA",
                            "api_secret": "SK_SCHEMA",
                            "api_passphrase": "PP_SCHEMA",
                        },
                    },
                },
            },
            protocol_version="2025-11-25",
        )
        tools = {tool["name"]: tool for tool in service.registry.list_tools()}

        Draft202012Validator(tools["trade_preview_order"]["outputSchema"]).validate(
            response["result"]["structuredContent"]
        )


if __name__ == "__main__":
    unittest.main()
