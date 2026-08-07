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


if __name__ == "__main__":
    unittest.main()
