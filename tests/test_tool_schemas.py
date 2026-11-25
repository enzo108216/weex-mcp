import unittest


class ToolSchemaTests(unittest.TestCase):
    def test_registry_projection_filters_modules_and_credential_modes_without_cross_build_mutation(self):
        from weex_mcp.tool_registry import build_default_registry

        try:
            local_registry = build_default_registry(
                enabled_modules={"market", "account", "trade"},
                credential_modes={"credential_ref"},
            )
        except TypeError as exc:
            self.fail(f"registry projection API is missing: {exc}")
        local_tools = {tool["name"]: tool for tool in local_registry.list_tools()}

        self.assertEqual(len(local_tools), 50)
        self.assertFalse(any(tool["_meta"]["com.weex/mcp"]["module"] == "broker" for tool in local_tools.values()))
        self.assertFalse(local_registry.has_tool("broker_oauth_generate_authorization_url"))
        self.assertIsNone(local_registry.get_tool("broker_oauth_exchange_code_for_api_key"))
        local_credential = local_tools["account_get_balance"]["inputSchema"]["properties"]["credential"]
        self.assertEqual([variant["properties"]["type"]["const"] for variant in local_credential["oneOf"]], ["credential_ref"])
        self.assertNotIn("api_key", local_credential["properties"])

        default_tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        default_credential = default_tools["account_get_balance"]["inputSchema"]["properties"]["credential"]
        self.assertEqual(len(default_tools), 52)
        self.assertEqual(
            [variant["properties"]["type"]["const"] for variant in default_credential["oneOf"]],
            ["credential_ref", "encrypted_envelope", "plaintext"],
        )
        self.assertIn("api_key", default_credential["properties"])

    def test_registry_projection_rejects_empty_or_unknown_modules_and_credential_modes(self):
        from weex_mcp.tool_registry import build_default_registry

        invalid_cases = (
            ({"enabled_modules": set()}, "enabled_modules"),
            ({"enabled_modules": {"market", "unknown"}}, "enabled_modules"),
            ({"credential_modes": set()}, "credential_modes"),
            ({"credential_modes": {"plaintext", "unknown"}}, "credential_modes"),
        )
        for kwargs, message in invalid_cases:
            with self.subTest(kwargs=kwargs):
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        build_default_registry(**kwargs)
                except TypeError as exc:
                    self.fail(f"registry projection API is missing: {exc}")

    def test_delegated_authorization_is_explicitly_enabled_and_mutually_exclusive(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        try:
            registry = build_default_registry(
                enabled_modules={"market", "account", "trade"},
                credential_modes={"credential_ref"},
                allow_delegated_authorization=True,
            )
        except TypeError as exc:
            self.fail(f"delegated authorization schema support is missing: {exc}")
        schema = registry.get_tool("trade_confirm_order").input_schema
        self.assertNotIn("confirmation", schema["required"])
        self.assertIn("delegated_authorization", schema["properties"])
        self.assertEqual(schema["properties"]["delegated_authorization"]["required"], ["ref"])

        base = {
            "market": "spot",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.001",
            "client_order_id": "delegated-schema-id",
            "credential": {"type": "credential_ref", "key_id": "key", "ref": "profile"},
            "preview_id": "signed",
        }
        validate_tool_arguments(base | {"confirmation": {"accepted": True}}, schema)
        validate_tool_arguments(base | {"delegated_authorization": {"ref": "authorization-ref"}}, schema)
        for invalid in (
            base,
            base
            | {
                "confirmation": {"accepted": True},
                "delegated_authorization": {"ref": "authorization-ref"},
            },
        ):
            with self.assertRaises(SchemaValidationError):
                validate_tool_arguments(invalid, schema)

        default_schema = build_default_registry().get_tool("trade_confirm_order").input_schema
        self.assertNotIn("delegated_authorization", default_schema["properties"])
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {
                    **base,
                    "credential": {
                        "type": "plaintext",
                        "api_key": "AK",
                        "api_secret": "SK",
                        "api_passphrase": "PP",
                    },
                    "delegated_authorization": {"ref": "authorization-ref"},
                },
                default_schema,
            )

        with self.assertRaisesRegex(ValueError, "allow_delegated_authorization"):
            build_default_registry(allow_delegated_authorization="true")

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
        self.assertEqual(market_schema["required"], ["market"])
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
        self.assertNotIn("delegated_authorization", confirm_schema["properties"])
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

    def test_t3_account_and_market_schemas_encode_host_independent_contracts(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        self.assertTrue(
            {
                "account_get_position",
                "market_get_server_time",
                "market_ping",
            }.issubset(tools)
        )

        credential = {
            "type": "credential_ref",
            "key_id": "key",
            "ref": "ref",
        }
        position = tools["account_get_position"]["inputSchema"]
        self.assertEqual(
            position["required"],
            ["market", "trading_mode", "symbol", "credential"],
        )
        self.assertEqual(position["properties"]["market"]["enum"], ["futures"])
        self.assertEqual(position["properties"]["trading_mode"]["enum"], ["live"])
        validate_tool_arguments(
            {
                "market": "futures",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "credential": credential,
            },
            position,
        )
        for arguments in (
            {"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "credential": credential},
            {"market": "futures", "trading_mode": "demo", "symbol": "BTCUSDT", "credential": credential},
        ):
            with self.subTest(tool="account_get_position", arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, position)

        server_time = tools["market_get_server_time"]["inputSchema"]
        self.assertEqual(server_time["required"], ["market"])
        for market in ("spot", "futures"):
            validate_tool_arguments({"market": market}, server_time)

        ping = tools["market_ping"]["inputSchema"]
        validate_tool_arguments({"market": "spot"}, ping)
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments({"market": "futures"}, ping)

        symbol_price = tools["market_get_symbol_price"]["inputSchema"]
        validate_tool_arguments({"market": "spot"}, symbol_price)
        validate_tool_arguments(
            {"market": "spot", "symbols": ["BTCUSDT", "ETHUSDT"]},
            symbol_price,
        )
        for arguments in (
            {"market": "spot", "symbol": "BTCUSDT", "symbols": ["ETHUSDT"]},
            {"market": "futures", "symbols": ["BTCUSDT"]},
            {"market": "spot", "symbols": []},
            {"market": "spot", "symbols": ["BTCUSDT", "BTCUSDT"]},
        ):
            with self.subTest(tool="market_get_symbol_price", arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, symbol_price)

        exchange_info = tools["market_get_exchange_info"]["inputSchema"]
        validate_tool_arguments(
            {
                "market": "spot",
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "symbol_status": "TRADING",
            },
            exchange_info,
        )
        for status in ("TRADING", "HALT", "BREAK"):
            validate_tool_arguments(
                {"market": "spot", "symbol_status": status},
                exchange_info,
            )
        for arguments in (
            {"market": "spot", "symbol": "BTCUSDT", "symbols": ["ETHUSDT"]},
            {"market": "spot", "symbol_status": "UNKNOWN"},
            {"market": "futures", "symbols": ["BTCUSDT"]},
            {"market": "futures", "symbol_status": "TRADING"},
        ):
            with self.subTest(tool="market_get_exchange_info", arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, exchange_info)

    def test_partner_schemas_are_typed_strict_local_user_contracts(self):
        from jsonschema import Draft202012Validator

        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        registry = build_default_registry(
            enabled_modules={"partner"},
            credential_modes={"credential_ref"},
        )
        tools = {tool["name"]: tool for tool in registry.list_tools()}
        self.assertEqual(
            set(tools),
            {
                "partner_get_affiliate_uids",
                "partner_get_channel_user_trade_and_asset",
                "partner_get_affiliate_commission",
                "partner_query_sub_channel_transactions",
                "partner_verify_referrals",
                "partner_get_referral_assets",
                "partner_get_referral_deal_data",
            },
        )
        credential = {"type": "credential_ref", "key_id": "key", "ref": "ref"}
        forbidden = {"endpoint", "path", "method", "base_url", "timeout", "locale"}
        for name, tool in tools.items():
            with self.subTest(name=name):
                schema = tool["inputSchema"]
                Draft202012Validator.check_schema(schema)
                self.assertFalse(schema["additionalProperties"])
                self.assertTrue(forbidden.isdisjoint(schema["properties"]))
                self.assertEqual(tool["_meta"]["com.weex/mcp"]["module"], "partner")
                self.assertEqual(tool["_meta"]["com.weex/mcp"]["permission"], "USER_DATA")
                credential_schema = schema["properties"]["credential"]
                self.assertEqual(
                    [variant["properties"]["type"]["const"] for variant in credential_schema["oneOf"]],
                    ["credential_ref"],
                )

        common = tools["partner_get_affiliate_uids"]["inputSchema"]
        validate_tool_arguments(
            {
                "credential": credential,
                "uid": "1",
                "start_time": "0",
                "end_time": "9223372036854775807",
                "page": "1",
                "page_size": "100",
                "expected_environment": "partner_test",
            },
            common,
        )
        validate_tool_arguments(
            {"credential": credential, "page": "9223372036854775808"},
            common,
        )
        for arguments in (
            {"credential": credential, "uid": "0"},
            {"credential": credential, "uid": "01"},
            {"credential": credential, "uid": "9223372036854775808"},
            {"credential": credential, "start_time": "1"},
            {"credential": credential, "page": "0"},
            {"credential": credential, "page_size": "101"},
            {"credential": credential, "expected_environment": "unknown"},
        ):
            with self.subTest(schema="common", arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, common)

        commission = tools["partner_get_affiliate_commission"]["inputSchema"]
        validate_tool_arguments(
            {"credential": credential, "coin": "BTC", "product_type": "FUTURES"},
            commission,
        )
        for field, value in (("coin", "ETH"), ("product_type", "MARGIN")):
            with self.assertRaises(SchemaValidationError):
                validate_tool_arguments({"credential": credential, field: value}, commission)

        sub_channel = tools["partner_query_sub_channel_transactions"]["inputSchema"]
        validate_tool_arguments(
            {
                "credential": credential,
                "product_type": "SPOT",
                "start_time": "0",
                "end_time": "1",
            },
            sub_channel,
        )
        validate_tool_arguments(
            {
                "credential": credential,
                "product_type": "FUTURES",
                "start_time": None,
                "end_time": None,
                "expected_environment": "partner_test",
            },
            sub_channel,
        )
        for arguments in (
            {
                "credential": credential,
                "product_type": "SPOT",
                "start_time": None,
                "end_time": None,
            },
            {
                "credential": credential,
                "product_type": "SPOT",
                "start_time": None,
                "end_time": None,
                "expected_environment": "partner_production",
            },
            {
                "credential": credential,
                "product_type": "SPOT",
                "start_time": None,
                "end_time": "1",
                "expected_environment": "partner_test",
            },
        ):
            with self.subTest(schema="sub_channel", arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, sub_channel)

        verify = tools["partner_verify_referrals"]["inputSchema"]
        validate_tool_arguments(
            {
                "credential": credential,
                "user_ids": [str(value) for value in range(1, 101)],
                "expected_environment": "partner_test",
            },
            verify,
        )
        for user_ids in ([], ["1", "1"], ["0"], [str(value) for value in range(1, 102)]):
            with self.subTest(schema="verify", user_ids=user_ids):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments({"credential": credential, "user_ids": user_ids}, verify)

        assets = tools["partner_get_referral_assets"]["inputSchema"]
        self.assertEqual(assets["required"], ["credential", "user_id", "start_time", "end_time"])
        validate_tool_arguments(
            {
                "credential": credential,
                "user_id": "1",
                "start_time": "2026-01-01",
                "end_time": "2026-01-31",
                "expected_environment": "partner_production",
            },
            assets,
        )
        with self.assertRaises(SchemaValidationError):
            validate_tool_arguments(
                {
                    "credential": credential,
                    "user_id": "1",
                    "start_time": "2026-1-1",
                    "end_time": "2026-01-31",
                },
                assets,
            )

        deal = tools["partner_get_referral_deal_data"]["inputSchema"]
        self.assertNotIn("maxItems", deal["properties"]["user_ids"])
        validate_tool_arguments(
            {
                "credential": credential,
                "user_ids": [str(value) for value in range(1, 102)],
                "start_time": "2026-01-01",
                "end_time": "2026-01-31",
                "expected_environment": "partner_test",
            },
            deal,
        )

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

    def test_leverage_schema_supports_simple_and_explicit_atomic_modes(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments
        from weex_mcp.tool_registry import build_default_registry

        tools = {tool["name"]: tool for tool in build_default_registry().list_tools()}
        preview = tools["trade_preview_leverage"]["inputSchema"]
        confirm = tools["trade_confirm_leverage"]["inputSchema"]
        self.assertTrue(preview["x-weex-deferConditionalValidation"])
        self.assertTrue(confirm["x-weex-deferConditionalValidation"])
        credential = {
            "type": "plaintext",
            "api_key": "AK",
            "api_secret": "SK",
            "api_passphrase": "PP",
        }
        base = {
            "market": "futures",
            "trading_mode": "live",
            "symbol": "BTCUSDT",
            "credential": credential,
        }
        valid = (
            base | {"margin_mode": "cross", "leverage": "5"},
            base | {"margin_mode": "isolated", "position_side": "LONG", "leverage": "3"},
            base | {"cross_leverage": "2"},
            base | {"margin_mode": "isolated", "isolated_long_leverage": "3"},
            base
            | {
                "margin_mode": "cross",
                "cross_leverage": "2",
                "isolated_long_leverage": "3",
                "isolated_short_leverage": "4",
            },
        )
        for arguments in valid:
            with self.subTest(arguments=arguments):
                validate_tool_arguments(arguments, preview)

        invalid = (
            base,
            base | {"margin_mode": "cross"},
            base | {"margin_mode": "cross", "leverage": "5", "cross_leverage": "2"},
            base | {"position_side": "LONG", "isolated_long_leverage": "3"},
            base | {"cross_leverage": "0"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, preview)

        validate_tool_arguments(base, preview, defer_conditional=True)
        validate_tool_arguments(
            base
            | {
                "preview_id": "signed",
                "confirmation": {"accepted": True},
            },
            confirm,
            defer_conditional=True,
        )

        validate_tool_arguments(
            base
            | {
                "cross_leverage": "2",
                "isolated_short_leverage": "4",
                "preview_id": "signed",
                "confirmation": {"accepted": True},
            },
            confirm,
        )

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
