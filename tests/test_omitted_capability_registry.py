import copy
import unittest

MARKET_NEW = {
    "market_get_ticker_24h",
    "market_get_book_ticker",
    "market_get_recent_trades",
    "market_get_funding_rate",
    "market_get_open_interest",
    "market_get_advanced_klines",
    "market_get_catalog",
}

ACCOUNT_NEW = {
    "account_get_config",
    "account_get_commission_rate",
    "account_get_symbol_config",
    "account_get_conditional_orders",
    "account_get_transfer_records",
}

TRADE_SUFFIXES = {
    "batch_order",
    "batch_cancel",
    "cancel_scope",
    "conditional_order",
    "conditional_cancel",
    "tp_sl",
    "close_positions",
    "position_margin",
    "auto_append_margin",
}

TRADE_NEW = {
    f"trade_{phase}_{suffix}"
    for phase in ("preview", "confirm")
    for suffix in TRADE_SUFFIXES
}


def credential():
    return {
        "type": "plaintext",
        "api_key": "AK_TEST",
        "api_secret": "SK_TEST",
        "api_passphrase": "PP_TEST",
    }


def confirmation():
    return {"accepted": True}


class OmittedCapabilityRegistryTests(unittest.TestCase):
    def _tools(self):
        from weex_mcp.tool_registry import build_default_registry

        return {tool["name"]: tool for tool in build_default_registry().list_tools()}

    def test_registry_has_exact_47_target_tools(self):
        tools = self._tools()

        self.assertEqual(len(tools), 47)
        self.assertTrue(MARKET_NEW.issubset(tools))
        self.assertTrue(ACCOUNT_NEW.issubset(tools))
        self.assertTrue(TRADE_NEW.issubset(tools))

    def test_every_new_schema_is_valid_draft_2020_12_and_strict(self):
        from jsonschema import Draft202012Validator

        for name in MARKET_NEW | ACCOUNT_NEW | TRADE_NEW:
            with self.subTest(name=name):
                schema = self._tools()[name]["inputSchema"]
                Draft202012Validator.check_schema(schema)
                self.assertFalse(schema["additionalProperties"])
                self.assertNotIn("path", schema.get("properties", {}))
                self.assertNotIn("method", schema.get("properties", {}))
                self.assertNotIn("base_url", schema.get("properties", {}))
                self.assertNotIn("body", schema.get("properties", {}))

    def test_new_read_schemas_accept_contract_examples(self):
        from weex_mcp.schema_validation import validate_tool_arguments

        examples = {
            "market_get_ticker_24h": {"market": "spot", "symbol": "BTCUSDT"},
            "market_get_book_ticker": {"market": "futures"},
            "market_get_recent_trades": {"market": "spot", "symbol": "BTCUSDT", "limit": "1000"},
            "market_get_funding_rate": {
                "market": "futures",
                "mode": "history",
                "symbol": "BTCUSDT",
                "start_time": "1000",
                "end_time": "2000",
                "limit": "100",
            },
            "market_get_open_interest": {"market": "futures", "symbol": "BTCUSDT"},
            "market_get_advanced_klines": {
                "market": "futures",
                "kind": "history",
                "symbol": "BTCUSDT",
                "interval": "1m",
                "price_type": "MARK",
                "limit": "100",
            },
            "market_get_catalog": {"market": "spot", "catalog": "coins"},
            "account_get_config": {"market": "spot", "trading_mode": "live", "credential": credential()},
            "account_get_commission_rate": {
                "market": "futures",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "credential": credential(),
            },
            "account_get_symbol_config": {
                "market": "futures",
                "trading_mode": "live",
                "credential": credential(),
            },
            "account_get_conditional_orders": {
                "market": "futures",
                "trading_mode": "live",
                "status": "history",
                "start_time": "1000",
                "end_time": "2000",
                "limit": "1000",
                "credential": credential(),
            },
            "account_get_transfer_records": {
                "market": "spot",
                "trading_mode": "live",
                "coin_id": "1",
                "limit": "100",
                "credential": credential(),
            },
        }

        for name, arguments in examples.items():
            with self.subTest(name=name):
                validate_tool_arguments(arguments, self._tools()[name]["inputSchema"])

    def test_new_trade_schemas_accept_preview_and_matching_confirm_examples(self):
        from weex_mcp.schema_validation import validate_tool_arguments

        previews = {
            "batch_order": {
                "market": "futures",
                "trading_mode": "live",
                "orders": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "position_side": "LONG",
                        "order_type": "LIMIT",
                        "quantity": "0.001",
                        "price": "100",
                        "time_in_force": "POST_ONLY",
                        "client_order_id": "batch-1",
                    }
                ],
                "credential": credential(),
            },
            "batch_cancel": {
                "market": "spot",
                "trading_mode": "live",
                "order_ids": ["1", "2"],
                "credential": credential(),
            },
            "cancel_scope": {
                "market": "futures",
                "trading_mode": "live",
                "order_kind": "conditional",
                "scope": "all",
                "acknowledge_all_symbols": True,
                "credential": credential(),
            },
            "conditional_order": {
                "market": "futures",
                "trading_mode": "live",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "position_side": "SHORT",
                "conditional_type": "STOP_MARKET",
                "quantity": "0.001",
                "trigger_price": "100",
                "client_algo_id": "algo-1",
                "credential": credential(),
            },
            "conditional_cancel": {
                "market": "futures",
                "trading_mode": "live",
                "order_id": "1",
                "credential": credential(),
            },
            "tp_sl": {
                "market": "futures",
                "trading_mode": "live",
                "operation": "place",
                "symbol": "BTCUSDT",
                "client_algo_id": "tp-1",
                "plan_type": "TAKE_PROFIT",
                "trigger_price": "100",
                "position_side": "LONG",
                "quantity": "0",
                "acknowledge_full_position": True,
                "credential": credential(),
            },
            "close_positions": {
                "market": "futures",
                "trading_mode": "live",
                "scope": "all",
                "acknowledge_all_positions": True,
                "credential": credential(),
            },
            "position_margin": {
                "market": "futures",
                "trading_mode": "live",
                "isolated_position_id": "1",
                "amount": "1.5",
                "adjustment": "increase",
                "credential": credential(),
            },
            "auto_append_margin": {
                "market": "futures",
                "trading_mode": "live",
                "position_id": "1",
                "enabled": True,
                "credential": credential(),
            },
        }

        for suffix, arguments in previews.items():
            with self.subTest(suffix=suffix, phase="preview"):
                validate_tool_arguments(arguments, self._tools()[f"trade_preview_{suffix}"]["inputSchema"])
            confirm_arguments = copy.deepcopy(arguments)
            confirm_arguments.update({"preview_id": "signed", "confirmation": confirmation()})
            with self.subTest(suffix=suffix, phase="confirm"):
                validate_tool_arguments(confirm_arguments, self._tools()[f"trade_confirm_{suffix}"]["inputSchema"])

    def test_new_trade_schemas_block_demo_and_implicit_broad_scope(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments

        invalid = {
            "trade_preview_batch_order": {
                "market": "futures",
                "trading_mode": "demo",
                "orders": [],
                "credential": credential(),
            },
            "trade_preview_batch_cancel": {
                "market": "spot",
                "trading_mode": "live",
                "order_ids": ["1"],
                "client_order_ids": ["a"],
                "credential": credential(),
            },
            "trade_preview_cancel_scope": {
                "market": "futures",
                "trading_mode": "live",
                "order_kind": "normal",
                "scope": "all",
                "credential": credential(),
            },
            "trade_preview_tp_sl": {
                "market": "futures",
                "trading_mode": "live",
                "operation": "place",
                "symbol": "BTCUSDT",
                "client_algo_id": "tp-1",
                "plan_type": "STOP_LOSS",
                "trigger_price": "100",
                "position_side": "LONG",
                "quantity": "0",
                "credential": credential(),
            },
            "trade_preview_close_positions": {
                "market": "futures",
                "trading_mode": "live",
                "scope": "all",
                "credential": credential(),
            },
        }

        for name, arguments in invalid.items():
            with self.subTest(name=name):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, self._tools()[name]["inputSchema"])

    def test_new_account_time_ranges_require_both_bounds(self):
        from weex_mcp.schema_validation import SchemaValidationError, validate_tool_arguments

        invalid = {
            "account_get_trades": {
                "market": "futures",
                "trading_mode": "live",
                "start_time": "1000",
                "credential": credential(),
            },
            "account_get_conditional_orders": {
                "market": "futures",
                "trading_mode": "live",
                "status": "history",
                "end_time": "2000",
                "credential": credential(),
            },
        }

        for name, arguments in invalid.items():
            with self.subTest(name=name):
                with self.assertRaises(SchemaValidationError):
                    validate_tool_arguments(arguments, self._tools()[name]["inputSchema"])


if __name__ == "__main__":
    unittest.main()
