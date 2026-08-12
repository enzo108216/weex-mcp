from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_demo_full_smoke.py"


class DemoFullSmokeSafetyTests(unittest.TestCase):
    def _load_module(self):
        self.assertTrue(SCRIPT_PATH.is_file(), "demo full smoke script must exist")
        spec = importlib.util.spec_from_file_location("run_demo_full_smoke", SCRIPT_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_private_transport_allows_exact_demo_catalog(self):
        module = self._load_module()
        transport_type = getattr(module, "DemoOnlyPrivateTransport", None)
        self.assertTrue(callable(transport_type))
        seen = []

        def delegate(prepared, timeout_seconds):
            seen.append((prepared["method"], prepared["url"], timeout_seconds))
            return b"{}"

        transport = transport_type(delegate=delegate)
        expected = {
            ("GET", "/capi/v3/account/accountConfig"),
            ("GET", "/capi/v3/sim/balance"),
            ("GET", "/capi/v3/sim/position/allPosition"),
            ("GET", "/capi/v3/sim/order/history"),
            ("POST", "/capi/v3/sim/order"),
        }
        self.assertEqual(set(transport.allowed_requests), expected)
        for method, path in sorted(expected):
            transport({"method": method, "url": f"https://api-contract.weex.com{path}", "headers": {}, "data": None}, 1.5)

        self.assertEqual(len(seen), 5)
        self.assertEqual(set(transport.calls), expected)

    def test_private_transport_blocks_live_before_delegate(self):
        module = self._load_module()
        transport_type = getattr(module, "DemoOnlyPrivateTransport", None)
        self.assertTrue(callable(transport_type))
        seen = []
        transport = transport_type(delegate=lambda prepared, timeout: seen.append(prepared) or b"{}")

        with self.assertRaisesRegex(RuntimeError, "blocked non-demo private request"):
            transport(
                {
                    "method": "POST",
                    "url": "https://api-contract.weex.com/capi/v3/order",
                    "headers": {},
                    "data": b"{}",
                },
                1.5,
            )

        self.assertEqual(seen, [])
        self.assertEqual(transport.calls, [])

    def test_secret_guard_rejects_any_credential_value_in_report(self):
        module = self._load_module()
        guard = getattr(module, "assert_secret_free", None)
        self.assertTrue(callable(guard))
        secrets = ["key-material", "secret-material", "passphrase-material"]

        guard({"result": "clean", "nested": ["order-1"]}, secrets)
        with self.assertRaisesRegex(RuntimeError, "credential material"):
            guard({"result": "secret-material"}, secrets)

    def test_demo_order_helper_uses_stateless_confirm_contract(self):
        module = self._load_module()

        class FakeService:
            def __init__(self):
                self.calls = []

            def handle_jsonrpc(self, request, *, context=None):
                params = request["params"]
                self.calls.append((params["name"], dict(params["arguments"])))
                if params["name"] == "trade_preview_order":
                    return {"result": {"ok": True, "preview_id": "signed-preview"}}
                return {"result": {"ok": True, "result": {"success": True}}}

        service = FakeService()
        credential = {
            "type": "plaintext",
            "api_key": "AK_TEST",
            "api_secret": "SK_TEST",
            "api_passphrase": "PP_TEST",
        }
        context = object()

        _, _, confirmation = module._submit_demo_market_order(
            service,
            context,
            credential,
            symbol="BTCUSDT",
            side="BUY",
            position_side="LONG",
            quantity="0.001",
            label="open",
        )

        preview_arguments = service.calls[0][1]
        confirm_arguments = service.calls[1][1]
        self.assertEqual(confirm_arguments["preview_id"], "signed-preview")
        self.assertEqual(confirm_arguments["client_order_id"], preview_arguments["client_order_id"])
        self.assertEqual([name for name, _ in service.calls], ["trade_preview_order", "trade_confirm_order"])
        for field in ("market", "trading_mode", "symbol", "side", "position_side", "order_type", "quantity", "credential"):
            self.assertEqual(confirm_arguments[field], preview_arguments[field])
        self.assertNotIn("approval_token", confirmation)
        self.assertNotIn("idempotency_key", confirmation)
        script_source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("trade_confirm_order idempotent retry", script_source)
        self.assertNotIn('"idempotent_retry"', script_source)
        self.assertNotIn("cached exchange result", script_source)

    def test_demo_smoke_builds_plaintext_request_credential(self):
        module = self._load_module()

        credential = module._credential_argument(
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
        )

        self.assertEqual(
            credential,
            {
                "type": "plaintext",
                "api_key": "AK_TEST",
                "api_secret": "SK_TEST",
                "api_passphrase": "PP_TEST",
            },
        )


if __name__ == "__main__":
    unittest.main()
