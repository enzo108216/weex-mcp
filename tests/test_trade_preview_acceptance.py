import importlib.util
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_trade_preview_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_trade_preview_acceptance", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class TradePreviewAcceptanceDriverTests(unittest.TestCase):
    def test_missing_position_has_no_identifier(self):
        self.assertIsNone(MODULE.position_id(None))

    def test_batch_order_item_does_not_include_top_level_market_fields(self):
        batch_case = next(arguments for case, _, arguments in MODULE.cases({}) if case == "batch_order_futures")
        self.assertNotIn("market", batch_case["orders"][0])
        self.assertNotIn("trading_mode", batch_case["orders"][0])


if __name__ == "__main__":
    unittest.main()
