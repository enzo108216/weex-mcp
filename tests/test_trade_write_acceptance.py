import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_trade_write_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_trade_write_acceptance", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TradeWriteAcceptanceDriverTests(unittest.TestCase):
    def test_limit_guard_rejects_orders_over_ten_usdt(self):
        with self.assertRaisesRegex(ValueError, "exceeds 10 USDT"):
            MODULE.ensure_order_limit(
                {
                    "symbol": "BTCUSDT",
                    "order_type": "LIMIT",
                    "quantity": "0.001",
                    "price": "70000",
                },
                {"BTCUSDT": Decimal("70000")},
            )

    def test_limit_guard_accepts_small_market_order_when_price_known(self):
        MODULE.ensure_order_limit(
            {
                "symbol": "BTCUSDT",
                "order_type": "MARKET",
                "quantity": "0.0001",
            },
            {"BTCUSDT": Decimal("70000")},
        )

    def test_confirm_arguments_use_preview_id_and_required_confirmation(self):
        arguments = {"market": "futures", "trading_mode": "demo", "symbol": "BTCUSDT"}
        confirmed = MODULE.confirm_arguments(arguments, "preview-token")
        self.assertEqual(confirmed["preview_id"], "preview-token")
        self.assertNotIn("approval_token", confirmed)
        self.assertEqual(confirmed["confirmation"], {"accepted": True})

    def test_call_tool_blocks_unexpected_tools(self):
        with self.assertRaisesRegex(RuntimeError, "unsafe tool blocked"):
            MODULE.validate_tool_allowed("account_get_transfer_records")

    def test_tp_sl_case_requests_conditional_scope_cleanup(self):
        case = next(
            case
            for case in MODULE.base_cases("1234567890", Decimal("2000"))
            if case.name == "tp_sl_place_futures_live"
        )
        self.assertEqual(case.cleanup, "conditional_scope")

    def test_cancel_arguments_prefer_order_id_when_available(self):
        self.assertEqual(
            MODULE.cancel_order_arguments({"order_id": "123", "client_order_id": "abc"}),
            {"market": "futures", "trading_mode": "live", "order_id": "123"},
        )


if __name__ == "__main__":
    unittest.main()
