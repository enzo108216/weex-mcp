import unittest


class ToolSchemaTests(unittest.TestCase):
    def test_tool_output_schema_declares_preview_clarification_fields(self):
        from weex_mcp.tool_registry import build_default_registry

        tool = build_default_registry().get_tool("trade_preview_order")

        self.assertIsNotNone(tool)
        output_schema = tool.to_mcp_tool()["outputSchema"]
        self.assertEqual(output_schema["properties"]["clarification_required"], {"type": "boolean"})
        self.assertEqual(output_schema["properties"]["clarification_prompt"], {"type": "string"})

    def test_tools_expose_strict_top_level_input_schemas(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}

        market_schema = tools["market_get_symbol_price"]["inputSchema"]
        self.assertEqual(market_schema["required"], ["market", "symbol"])
        self.assertFalse(market_schema["additionalProperties"])
        self.assertEqual(market_schema["properties"]["market"]["enum"], ["spot", "futures"])

        account_schema = tools["account_get_balance"]["inputSchema"]
        self.assertEqual(account_schema["required"], ["market", "trading_mode", "credential"])
        self.assertFalse(account_schema["additionalProperties"])

        preview_schema = tools["trade_preview_order"]["inputSchema"]
        self.assertEqual(
            preview_schema["required"],
            ["market", "trading_mode", "symbol", "side", "order_type", "quantity", "client_order_id", "credential"],
        )
        self.assertFalse(preview_schema["additionalProperties"])

        confirm_schema = tools["trade_confirm_order"]["inputSchema"]
        self.assertEqual(
            confirm_schema["required"],
            [
                "market",
                "trading_mode",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "client_order_id",
                "credential",
                "confirmation",
            ],
        )
        self.assertFalse(confirm_schema["additionalProperties"])
        self.assertNotIn("idempotency_key", confirm_schema["properties"])
        self.assertIn("preview_id", confirm_schema["properties"])
        self.assertIn("approval_token", confirm_schema["properties"])
        self.assertEqual(confirm_schema["properties"]["confirmation"]["required"], ["accepted"])
        self.assertEqual(set(confirm_schema["properties"]["confirmation"]["properties"]), {"accepted"})

        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments

        base = {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "schema-preview-id",
            "credential": {
                "type": "plaintext",
                "api_key": "AK",
                "api_secret": "SK",
                "api_passphrase": "PP",
            },
            "confirmation": {"accepted": True},
        }
        validate_tool_arguments(base | {"preview_id": "signed"}, confirm_schema)
        validate_tool_arguments(base | {"approval_token": "legacy-signed"}, confirm_schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(base, confirm_schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                base | {"preview_id": "signed", "approval_token": "legacy-signed"},
                confirm_schema,
            )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                base | {"preview_id": "signed", "confirmation": {"accepted": True, "reply_text": "确认"}},
                confirm_schema,
            )

    def test_tools_call_rejects_unknown_top_level_arguments_before_adapter(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service(trade_enabled=False)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "schema_1",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT", "api_key": "MUST_NOT_BE_ACCEPTED"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "invalid_arguments")
        self.assertNotIn("MUST_NOT_BE_ACCEPTED", str(response))

    def test_account_schemas_expose_endpoint_specific_pagination_and_bill_filters(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        orders = tools["account_get_orders"]["inputSchema"]["properties"]
        bills = tools["account_get_bills"]["inputSchema"]["properties"]

        self.assertIn("page", orders)
        for field in (
            "coin_id",
            "asset",
            "biz_type",
            "income_type",
            "month",
            "page",
            "next_key_id",
            "next_key_time",
        ):
            self.assertIn(field, bills)

    def test_kline_schema_excludes_unsupported_time_range_and_declares_market_variants(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        schema = tools["market_get_klines"]["inputSchema"]

        self.assertNotIn("start_time", schema["properties"])
        self.assertNotIn("end_time", schema["properties"])
        self.assertIn("limit", schema["properties"])
        self.assertEqual(len(schema["oneOf"]), 2)

    def test_market_schemas_express_depth_and_kline_catalog_ranges(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        depth = tools["market_get_depth"]["inputSchema"]
        self.assertEqual(depth["properties"]["limit"]["enum"], ["15", "200"])
        for limit in ("15", "200"):
            validate_tool_arguments({"market": "spot", "symbol": "BTCUSDT", "limit": limit}, depth)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments({"market": "spot", "symbol": "BTCUSDT", "limit": "10"}, depth)

        klines = tools["market_get_klines"]["inputSchema"]
        validate_tool_arguments({"market": "spot", "symbol": "BTCUSDT", "interval": "2h"}, klines)
        validate_tool_arguments({"market": "futures", "symbol": "BTCUSDT", "interval": "12h", "limit": "1000"}, klines)
        invalid = (
            {"market": "spot", "symbol": "BTCUSDT", "interval": "3h"},
            {"market": "futures", "symbol": "BTCUSDT", "interval": "2h"},
            {"market": "futures", "symbol": "BTCUSDT", "interval": "1m", "limit": "0"},
            {"market": "futures", "symbol": "BTCUSDT", "interval": "1m", "limit": "1001"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, klines)

    def test_account_schemas_reject_malformed_or_out_of_range_numeric_strings(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        invalid = (
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "single", "order_id": "not-long", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "history", "start_time": "-1", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "history", "limit": "1001", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "open", "limit": "101", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "futures", "trading_mode": "live", "bill_type": "income", "next_key_id": "9223372036854775808", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "tax", "page": "0", "credential": credential},
            ),
        )
        for tool_name, arguments in invalid:
            with self.subTest(tool_name=tool_name, arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, tools[tool_name]["inputSchema"])

    def test_account_schemas_enforce_catalog_time_windows_and_tax_month(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        ninety_days_ms = 90 * 24 * 60 * 60 * 1000
        one_hundred_days_ms = 100 * 24 * 60 * 60 * 1000
        valid = (
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "history", "start_time": "0", "end_time": str(ninety_days_ms), "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "demo", "status": "history", "start_time": "0", "end_time": str(ninety_days_ms), "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "futures", "trading_mode": "live", "bill_type": "income", "start_time": "0", "end_time": str(one_hundred_days_ms), "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "tax", "month": "2026-08", "credential": credential},
            ),
        )
        invalid = (
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "history", "start_time": "0", "end_time": str(ninety_days_ms + 1), "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "demo", "status": "history", "start_time": "0", "end_time": str(ninety_days_ms + 1), "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "futures", "trading_mode": "live", "bill_type": "income", "start_time": "0", "end_time": str(one_hundred_days_ms + 1), "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "tax", "month": "2026-13", "credential": credential},
            ),
        )

        for tool_name, arguments in valid:
            with self.subTest(tool_name=tool_name, arguments=arguments, expected="valid"):
                validate_tool_arguments(arguments, tools[tool_name]["inputSchema"])
        for tool_name, arguments in invalid:
            with self.subTest(tool_name=tool_name, arguments=arguments, expected="invalid"):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, tools[tool_name]["inputSchema"])

    def test_account_schemas_enforce_endpoint_specific_limit_and_page_ranges(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        valid_and_invalid = (
            (
                "account_get_orders",
                {"market": "spot", "trading_mode": "live", "status": "history", "symbol": "BTCUSDT", "limit": "1000", "page": "1", "credential": credential},
                {"market": "spot", "trading_mode": "live", "status": "history", "symbol": "BTCUSDT", "page": "0", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "open", "limit": "100", "page": "0", "credential": credential},
                {"market": "futures", "trading_mode": "live", "status": "open", "limit": "101", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "history", "limit": "1000", "page": "0", "credential": credential},
                {"market": "futures", "trading_mode": "live", "status": "history", "limit": "1001", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "demo", "status": "history", "limit": "1000", "page": "0", "credential": credential},
                {"market": "futures", "trading_mode": "demo", "status": "history", "limit": "1001", "credential": credential},
            ),
            (
                "account_get_trades",
                {"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "limit": "2147483647", "credential": credential},
                {"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "limit": "2147483648", "credential": credential},
            ),
            (
                "account_get_trades",
                {"market": "futures", "trading_mode": "live", "limit": "100", "credential": credential},
                {"market": "futures", "trading_mode": "live", "limit": "101", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "account", "limit": "100", "credential": credential},
                {"market": "spot", "trading_mode": "live", "bill_type": "account", "limit": "101", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "funding", "limit": "100", "page": "1", "credential": credential},
                {"market": "spot", "trading_mode": "live", "bill_type": "funding", "page": "0", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "tax", "limit": "200", "page": "1", "credential": credential},
                {"market": "spot", "trading_mode": "live", "bill_type": "tax", "limit": "201", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "futures", "trading_mode": "live", "bill_type": "income", "limit": "100", "credential": credential},
                {"market": "futures", "trading_mode": "live", "bill_type": "income", "limit": "101", "credential": credential},
            ),
        )

        for tool_name, valid, invalid in valid_and_invalid:
            with self.subTest(tool_name=tool_name, valid=valid):
                validate_tool_arguments(valid, tools[tool_name]["inputSchema"])
            with self.subTest(tool_name=tool_name, invalid=invalid):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(invalid, tools[tool_name]["inputSchema"])

    def test_symbol_price_schema_restricts_price_type_by_market(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        schema = {tool["name"]: tool for tool in build_default_registry().list_tools()}["market_get_symbol_price"]["inputSchema"]
        validate_tool_arguments({"market": "spot", "symbol": "BTCUSDT"}, schema)
        validate_tool_arguments({"market": "futures", "symbol": "BTCUSDT", "price_type": "MARK"}, schema)
        validate_tool_arguments({"market": "futures", "symbol": "BTCUSDT", "price_type": "INDEX"}, schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments({"market": "spot", "symbol": "BTCUSDT", "price_type": "MARK"}, schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments({"market": "futures", "symbol": "BTCUSDT", "price_type": "LAST"}, schema)

    def test_credential_schema_self_describes_all_strict_variants(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential_schema = tools["account_get_balance"]["inputSchema"]["properties"]["credential"]
        self.assertFalse(credential_schema["additionalProperties"])
        self.assertEqual(len(credential_schema["oneOf"]), 3)
        self.assertEqual(credential_schema["oneOf"][0]["required"], ["type", "key_id", "ref"])
        self.assertEqual(credential_schema["oneOf"][1]["required"], ["type", "key_id", "ciphertext", "nonce", "alg"])
        self.assertEqual(
            credential_schema["oneOf"][2]["required"],
            ["type", "api_key", "api_secret", "api_passphrase"],
        )

        base = {"market": "spot", "trading_mode": "live"}
        validate_tool_arguments(
            base | {"credential": {"type": "credential_ref", "key_id": "key", "ref": "ref"}},
            tools["account_get_balance"]["inputSchema"],
        )
        validate_tool_arguments(
            base | {"credential": {"type": "encrypted_envelope", "key_id": "key", "ciphertext": "ct", "nonce": "n", "alg": "A256GCM"}},
            tools["account_get_balance"]["inputSchema"],
        )
        validate_tool_arguments(
            base
            | {
                "credential": {
                    "type": "plaintext",
                    "api_key": "AK_INLINE",
                    "api_secret": "SK_INLINE",
                    "api_passphrase": "PP_INLINE",
                }
            },
            tools["account_get_balance"]["inputSchema"],
        )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                base | {"credential": {"type": "credential_ref", "key_id": "key", "ref": "ref", "unexpected": "x"}},
                tools["account_get_balance"]["inputSchema"],
            )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                base | {"credential": {"type": "credential_ref", "key_id": "key"}},
                tools["account_get_balance"]["inputSchema"],
            )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                base | {"credential": {"type": "plaintext", "api_key": "AK_INLINE", "api_secret": "SK_INLINE"}},
                tools["account_get_balance"]["inputSchema"],
            )

    def test_trade_schema_declares_supported_trigger_fields_and_live_only_mutations(self):
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        order_properties = tools["trade_preview_order"]["inputSchema"]["properties"]
        for field in ("tp_trigger_price", "sl_trigger_price", "tp_working_type", "sl_working_type"):
            self.assertIn(field, order_properties)
        cancel_schema = tools["trade_preview_cancel_order"]["inputSchema"]
        self.assertEqual(cancel_schema["properties"]["trading_mode"]["enum"], ["live"])
        self.assertNotIn("module", tools["trade_preview_order"]["annotations"])
        self.assertIn("_meta", tools["trade_preview_order"])

    def test_trade_cancel_schema_binds_an_order_identity_without_symbol(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        schema = tools["trade_preview_cancel_order"]["inputSchema"]
        self.assertEqual(schema["required"], ["market", "trading_mode", "credential"])
        self.assertNotIn("symbol", schema["properties"])
        self.assertIn(
            {"required": ["order_id"]},
            schema["anyOf"],
        )
        self.assertIn(
            {"required": ["client_order_id"]},
            schema["anyOf"],
        )
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        validate_tool_arguments(
            {"market": "spot", "trading_mode": "live", "order_id": "1001", "credential": credential},
            schema,
        )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {"market": "spot", "trading_mode": "live", "order_id": "not-a-number", "credential": credential},
                schema,
            )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {"market": "spot", "trading_mode": "live", "client_order_id": "x" * 37, "credential": credential},
                schema,
            )

    def test_private_tool_schemas_reject_runtime_unsupported_market_mode_combinations(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        unsupported = (
            ("account_get_balance", {"market": "spot", "trading_mode": "demo", "credential": credential}),
            ("account_get_positions", {"market": "spot", "trading_mode": "live", "credential": credential}),
            ("account_get_orders", {"market": "futures", "trading_mode": "demo", "status": "open", "credential": credential}),
            ("account_get_trades", {"market": "futures", "trading_mode": "demo", "credential": credential}),
            ("account_get_bills", {"market": "futures", "trading_mode": "demo", "credential": credential}),
            (
                "trade_preview_order",
                {"market": "spot", "trading_mode": "demo", "symbol": "BTCUSDT", "side": "BUY", "order_type": "MARKET", "quantity": "1", "credential": credential},
            ),
        )
        for tool_name, arguments in unsupported:
            with self.subTest(tool=tool_name):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, tools[tool_name]["inputSchema"])

    def test_account_schemas_reject_endpoint_incompatible_filters_and_require_catalog_fields(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}

        invalid = (
            (
                "account_get_bills",
                {"market": "spot", "trading_mode": "live", "bill_type": "income", "credential": credential},
            ),
            (
                "account_get_bills",
                {"market": "futures", "trading_mode": "live", "bill_type": "funding", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "futures", "trading_mode": "live", "status": "single", "client_order_id": "client-1", "credential": credential},
            ),
            (
                "account_get_orders",
                {"market": "spot", "trading_mode": "live", "status": "history", "credential": credential},
            ),
            (
                "account_get_trades",
                {"market": "spot", "trading_mode": "live", "credential": credential},
            ),
        )
        for tool_name, arguments in invalid:
            with self.subTest(tool=tool_name, arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, tools[tool_name]["inputSchema"])

    def test_account_schemas_reject_fields_that_endpoint_builders_would_drop(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {
                    "market": "spot",
                    "trading_mode": "live",
                    "status": "open",
                    "order_id": "1",
                    "credential": credential,
                },
                tools["account_get_orders"]["inputSchema"],
            )

    def test_trade_schema_rejects_trigger_fields_for_spot_and_limits_working_types(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        schema = {tool["name"]: tool for tool in build_default_registry().list_tools()}["trade_preview_order"]["inputSchema"]
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        base = {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "1",
            "credential": credential,
        }
        for field in ("tp_trigger_price", "sl_trigger_price", "tp_working_type", "sl_working_type"):
            with self.subTest(field=field):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(base | {field: "1"}, schema)

        valid_futures = base | {
            "market": "futures",
            "trading_mode": "live",
            "position_side": "LONG",
            "tp_trigger_price": "100",
            "tp_working_type": "MARK_PRICE",
        }
        validate_tool_arguments(valid_futures, schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(valid_futures | {"tp_working_type": "anything"}, schema)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments({key: value for key, value in valid_futures.items() if key != "tp_trigger_price"}, schema)

    def test_trade_order_and_confirm_schema_match_runtime_conditions(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        valid_spot_market = {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "schema-order-1",
            "credential": credential,
        }
        valid_futures_limit = valid_spot_market | {
            "market": "futures",
            "position_side": "LONG",
            "order_type": "LIMIT",
            "price": "100.0",
            "time_in_force": "GTC",
        }
        validate_tool_arguments(valid_spot_market, tools["trade_preview_order"]["inputSchema"])
        validate_tool_arguments(valid_futures_limit, tools["trade_preview_order"]["inputSchema"])

        invalid_orders = (
            valid_spot_market | {"market": "futures"},
            valid_futures_limit | {"price": ""},
            valid_spot_market | {"position_side": "LONG"},
            valid_spot_market | {"price": "100"},
            valid_spot_market | {"order_type": "LIMIT", "price": "100", "time_in_force": "POST_ONLY"},
            valid_futures_limit | {"position_side": "SIDEWAYS"},
            valid_futures_limit | {"quantity": "not-a-number"},
            valid_futures_limit | {"client_order_id": "bad id"},
        )
        for arguments in invalid_orders:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, tools["trade_preview_order"]["inputSchema"])

        confirm_schema = tools["trade_confirm_order"]["inputSchema"]
        valid_confirm = valid_spot_market | {
            "preview_id": "signed-token",
            "confirmation": {"accepted": True},
        }
        validate_tool_arguments(valid_confirm, confirm_schema)
        for confirmation in ({"accepted": False}, {"accepted": True, "reply_text": "确认"}):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(valid_confirm | {"confirmation": confirmation}, confirm_schema)

        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "margin_mode": "cross",
                    "leverage": "not-a-number",
                    "credential": credential,
                },
                tools["trade_preview_leverage"]["inputSchema"],
            )


if __name__ == "__main__":
    unittest.main()
