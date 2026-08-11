import unittest


def credential():
    return {
        "type": "plaintext",
        "api_key": "AK_READ",
        "api_secret": "SK_READ",
        "api_passphrase": "PP_READ",
    }


class RecordingMarketAdapter:
    def __init__(self):
        self.calls = []

    def _record(self, method, kwargs):
        self.calls.append((method, kwargs))
        return {"method": method, **kwargs}

    def get_ticker_24h(self, **kwargs):
        return self._record("get_ticker_24h", kwargs)

    def get_book_ticker(self, **kwargs):
        return self._record("get_book_ticker", kwargs)

    def get_recent_trades(self, **kwargs):
        return self._record("get_recent_trades", kwargs)

    def get_funding_rate(self, **kwargs):
        return self._record("get_funding_rate", kwargs)

    def get_open_interest(self, **kwargs):
        return self._record("get_open_interest", kwargs)

    def get_advanced_klines(self, **kwargs):
        return self._record("get_advanced_klines", kwargs)

    def get_catalog(self, **kwargs):
        return self._record("get_catalog", kwargs)


class RecordingAccountAdapter:
    def __init__(self):
        self.calls = []

    def _record(self, method, kwargs):
        self.calls.append((method, kwargs))
        safe_filters = {
            key: value
            for key, value in kwargs.items()
            if key not in {"credential_argument", "context"}
        }
        return {"method": method, "filters": safe_filters}

    def get_config(self, **kwargs):
        return self._record("get_config", kwargs)

    def get_commission_rate(self, **kwargs):
        return self._record("get_commission_rate", kwargs)

    def get_symbol_config(self, **kwargs):
        return self._record("get_symbol_config", kwargs)

    def get_conditional_orders(self, **kwargs):
        return self._record("get_conditional_orders", kwargs)

    def get_transfer_records(self, **kwargs):
        return self._record("get_transfer_records", kwargs)


class OmittedReadCapabilityTests(unittest.TestCase):
    def _call(self, service, name, arguments):
        return service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )["result"]

    def test_all_new_market_tools_dispatch_without_credentials(self):
        from weex_mcp.service import build_default_service

        adapter = RecordingMarketAdapter()
        service = build_default_service(market_adapter=adapter)
        cases = {
            "market_get_ticker_24h": {"market": "spot", "symbol": "BTCUSDT"},
            "market_get_book_ticker": {"market": "futures"},
            "market_get_recent_trades": {"market": "spot", "symbol": "BTCUSDT", "limit": "2"},
            "market_get_funding_rate": {"market": "futures", "mode": "current", "symbol": "BTCUSDT"},
            "market_get_open_interest": {"market": "futures", "symbol": "BTCUSDT"},
            "market_get_advanced_klines": {
                "market": "futures",
                "kind": "index",
                "symbol": "BTCUSDT",
                "interval": "1m",
                "limit": "2",
            },
            "market_get_catalog": {"market": "futures", "catalog": "symbols"},
        }

        for name, arguments in cases.items():
            with self.subTest(name=name):
                result = self._call(service, name, arguments)
                self.assertTrue(result["ok"], result)

        self.assertEqual([method for method, _ in adapter.calls], [name.removeprefix("market_") for name in cases])

    def test_all_new_account_tools_dispatch_request_scoped_credential(self):
        from weex_mcp.service import build_default_service

        adapter = RecordingAccountAdapter()
        service = build_default_service(account_adapter=adapter)
        cases = {
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
                "status": "open",
                "page": "1",
                "limit": "10",
                "credential": credential(),
            },
            "account_get_transfer_records": {
                "market": "spot",
                "trading_mode": "live",
                "after": "1",
                "before": "2",
                "credential": credential(),
            },
        }

        for name, arguments in cases.items():
            with self.subTest(name=name):
                result = self._call(service, name, arguments)
                self.assertTrue(result["ok"], result)

        self.assertEqual([method for method, _ in adapter.calls], [name.removeprefix("account_") for name in cases])
        for _, kwargs in adapter.calls:
            self.assertEqual(kwargs["credential_argument"]["api_secret"], "SK_READ")

    def test_account_new_tools_never_dispatch_trade_like_arbitrary_method(self):
        from weex_mcp.service import build_default_service

        adapter = RecordingAccountAdapter()
        result = self._call(
            build_default_service(account_adapter=adapter),
            "account_get_config",
            {
                "market": "futures",
                "trading_mode": "live",
                "method": "POST",
                "credential": credential(),
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
