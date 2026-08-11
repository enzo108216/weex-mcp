import json
import unittest


class OmittedMarketAdapterTests(unittest.TestCase):
    def test_new_public_methods_use_fixed_endpoint_and_normalize_payload(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        calls = []
        responses = [
            [{"symbol": "BTCUSDT", "lastPrice": "1"}],
            [{"symbol": "BTCUSDT", "bidPrice": "1", "bidQty": "2", "askPrice": "3", "askQty": "4"}],
            [{"id": "1", "price": "1", "qty": "2", "time": 3}],
            [{"symbol": "BTCUSDT", "lastFundingRate": "0.1"}],
            [{"symbol": "BTCUSDT", "fundingRate": "0.1", "fundingTime": 1}],
            {"symbol": "BTCUSDT", "openInterest": "2", "time": 1},
            [[1, "1", "2", "0.5", "1.5", "10", 2]],
            ["BTCUSDT"],
            [{"coin": "BTC", "networkList": []}],
        ]

        def transport(url, timeout):
            calls.append(url)
            return json.dumps(responses.pop(0)).encode()

        adapter = WeexMarketRestAdapter(transport=transport, max_attempts=1)
        results = [
            adapter.get_ticker_24h(market="spot", symbol="BTCUSDT"),
            adapter.get_book_ticker(market="futures", symbol="BTCUSDT"),
            adapter.get_recent_trades(market="spot", symbol="BTCUSDT", limit="5"),
            adapter.get_funding_rate(market="futures", mode="current", symbol="BTCUSDT"),
            adapter.get_funding_rate(
                market="futures",
                mode="history",
                symbol="BTCUSDT",
                start_time="1",
                end_time="2",
                limit="5",
            ),
            adapter.get_open_interest(market="futures", symbol="BTCUSDT"),
            adapter.get_advanced_klines(
                market="futures",
                kind="history",
                symbol="BTCUSDT",
                interval="1m",
                price_type="MARK",
                limit="5",
            ),
            adapter.get_catalog(market="futures", catalog="symbols"),
            adapter.get_catalog(market="spot", catalog="coins"),
        ]

        self.assertEqual(
            calls,
            [
                "https://api-spot.weex.com/api/v3/market/ticker/24hr?symbol=BTCUSDT",
                "https://api-contract.weex.com/capi/v3/market/ticker/bookTicker?symbol=BTCUSDT",
                "https://api-spot.weex.com/api/v3/market/trades?symbol=BTCUSDT&limit=5",
                "https://api-contract.weex.com/capi/v3/market/premiumIndex?symbol=BTCUSDT",
                "https://api-contract.weex.com/capi/v3/market/fundingRate?symbol=BTCUSDT&startTime=1&endTime=2&limit=5",
                "https://api-contract.weex.com/capi/v3/market/openInterest?symbol=BTCUSDT",
                "https://api-contract.weex.com/capi/v3/market/historyKlines?symbol=BTCUSDT&interval=1m&limit=5&priceType=MARK",
                "https://api-contract.weex.com/capi/v3/market/apiTradingSymbols",
                "https://api-spot.weex.com/api/v3/coins",
            ],
        )
        self.assertEqual(results[0]["tickers"][0]["symbol"], "BTCUSDT")
        self.assertEqual(results[1]["book_tickers"][0]["askPrice"], "3")
        self.assertEqual(results[2]["trades"][0]["id"], "1")
        self.assertEqual(results[3]["funding_rates"][0]["symbol"], "BTCUSDT")
        self.assertEqual(results[4]["mode"], "history")
        self.assertEqual(results[5]["open_interest"]["openInterest"], "2")
        self.assertEqual(results[6]["kind"], "history")
        self.assertEqual(results[7]["items"], ["BTCUSDT"])
        self.assertEqual(results[8]["items"][0]["coin"], "BTC")

    def test_new_public_methods_fail_closed_on_malformed_items_or_wrong_symbol(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        payloads = iter([[{"symbol": "ETHUSDT", "lastPrice": "1"}], [{"price": "1"}]])
        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: json.dumps(next(payloads)).encode(),
            max_attempts=1,
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_ticker_24h(market="spot", symbol="BTCUSDT")
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_recent_trades(market="spot", symbol="BTCUSDT")

    def test_recent_trades_accept_staging_uuid_identifiers(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        trade_id = "7596b34e-0be8-4eba-a306-4f4ca20386b2"
        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: json.dumps(
                [{"id": trade_id, "price": "63623.94", "qty": "0.047381", "time": 1786470936110}]
            ).encode(),
            max_attempts=1,
        )

        result = adapter.get_recent_trades(market="spot", symbol="BTCUSDT", limit="1")

        self.assertEqual(result["trades"][0]["id"], trade_id)

    def test_funding_rates_accept_negative_finite_values(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        payloads = iter(
            [
                [{"symbol": "BTCUSDT", "lastFundingRate": "-0.0001008"}],
                [{"symbol": "BTCUSDT", "fundingRate": "-0.00010080", "fundingTime": 1786464000000}],
            ]
        )
        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: json.dumps(next(payloads)).encode(),
            max_attempts=1,
        )

        current = adapter.get_funding_rate(market="futures", mode="current", symbol="BTCUSDT")
        history = adapter.get_funding_rate(market="futures", mode="history", symbol="BTCUSDT")

        self.assertEqual(current["funding_rates"][0]["lastFundingRate"], "-0.0001008")
        self.assertEqual(history["funding_rates"][0]["fundingRate"], "-0.00010080")

    def test_open_interest_empty_response_is_a_structured_invalid_response(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(transport=lambda url, timeout: b"[]")

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_open_interest(market="futures", symbol="BTCUSDT")

    def test_new_public_methods_reject_non_numeric_market_fields(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        calls = [
            ("ticker", lambda adapter: adapter.get_ticker_24h(market="spot", symbol="BTCUSDT"), [{"symbol": "BTCUSDT", "lastPrice": "NaN"}]),
            ("book", lambda adapter: adapter.get_book_ticker(market="spot", symbol="BTCUSDT"), [{"symbol": "BTCUSDT", "bidPrice": "oops", "bidQty": "1", "askPrice": "2", "askQty": "1"}]),
            ("trades", lambda adapter: adapter.get_recent_trades(market="spot", symbol="BTCUSDT"), [{"id": "1", "price": "1", "qty": "oops", "time": 3}]),
            ("funding", lambda adapter: adapter.get_funding_rate(market="futures", mode="current", symbol="BTCUSDT"), [{"symbol": "BTCUSDT", "lastFundingRate": "oops"}]),
            ("open_interest", lambda adapter: adapter.get_open_interest(market="futures", symbol="BTCUSDT"), {"symbol": "BTCUSDT", "openInterest": "oops", "time": 1}),
        ]

        for name, call, payload in calls:
            with self.subTest(name=name):
                adapter = WeexMarketRestAdapter(
                    transport=lambda url, timeout, payload=payload: json.dumps(payload).encode(),
                    max_attempts=1,
                )
                with self.assertRaises(WeexRestBusinessError):
                    call(adapter)

    def test_new_public_methods_reject_malformed_response_symbols_as_upstream_errors(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: json.dumps([{"symbol": "@@@", "lastPrice": "1"}]).encode(),
            max_attempts=1,
        )
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_ticker_24h(market="spot", symbol="BTCUSDT")

    def test_funding_and_advanced_kline_constraints_fail_before_transport(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        calls = []
        adapter = WeexMarketRestAdapter(transport=lambda url, timeout: calls.append(url) or b"[]", max_attempts=1)

        with self.assertRaises(ValueError):
            adapter.get_funding_rate(market="spot", mode="current")
        with self.assertRaises(ValueError):
            adapter.get_funding_rate(
                market="futures",
                mode="history",
                symbol="BTCUSDT",
                start_time="1",
                end_time=str(8 * 24 * 60 * 60 * 1000),
            )
        with self.assertRaises(ValueError):
            adapter.get_advanced_klines(
                market="futures",
                kind="index",
                symbol="BTCUSDT",
                interval="1m",
                price_type="MARK",
            )
        self.assertEqual(calls, [])

    def test_history_klines_reject_future_ranges_before_transport(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        calls = []
        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: calls.append(url) or b"[]",
            max_attempts=1,
            time_func=lambda: 100,
        )

        with self.assertRaises(ValueError):
            adapter.get_advanced_klines(
                market="futures",
                kind="history",
                symbol="BTCUSDT",
                interval="1m",
                start_time="100001",
                end_time="100002",
            )
        self.assertEqual(calls, [])


class OmittedAccountAdapterTests(unittest.TestCase):
    def _provider(self):
        from weex_mcp.secret_provider import RequestSecretProvider

        return RequestSecretProvider()

    def _credential(self):
        return {
            "type": "plaintext",
            "api_key": "AK_ACCOUNT",
            "api_secret": "SK_ACCOUNT",
            "api_passphrase": "PP_ACCOUNT",
        }

    def _context(self):
        from weex_mcp.tenant_context import TenantContext

        return TenantContext(tenant_id="anonymous", user_id="anonymous", session_id="anonymous")

    def test_new_read_methods_use_only_user_data_endpoints(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter

        calls = []
        responses = [
            {"canTrade": True, "canDeposit": True, "canWithdraw": False},
            {"symbol": "BTCUSDT", "makerCommissionRate": "0.1", "takerCommissionRate": "0.2"},
            [{"symbol": "BTCUSDT", "marginType": "CROSSED"}],
            [{"algoId": "1", "symbol": "BTCUSDT", "algoStatus": "NEW"}],
            {"orders": [{"algoId": "2", "symbol": "BTCUSDT", "algoStatus": "FINISHED"}], "hasMore": False},
            [{"coinName": "USDT", "status": "SUCCESS", "amount": "1"}],
        ]

        def transport(prepared, timeout):
            calls.append(prepared)
            return json.dumps(responses.pop(0)).encode()

        adapter = WeexAccountRestAdapter(secret_provider=self._provider(), transport=transport, time_func=lambda: 1)
        common = {"credential_argument": self._credential(), "context": self._context()}
        results = [
            adapter.get_config(market="futures", trading_mode="live", **common),
            adapter.get_commission_rate(market="futures", trading_mode="live", symbol="BTCUSDT", **common),
            adapter.get_symbol_config(market="futures", trading_mode="live", symbol="BTCUSDT", **common),
            adapter.get_conditional_orders(
                market="futures",
                trading_mode="live",
                status="open",
                symbol="BTCUSDT",
                page="1",
                limit="10",
                **common,
            ),
            adapter.get_conditional_orders(
                market="futures",
                trading_mode="live",
                status="history",
                start_time="1",
                end_time="2",
                limit="10",
                **common,
            ),
            adapter.get_transfer_records(
                market="spot",
                trading_mode="live",
                coin_id="1",
                from_type="CONTRACT",
                after="1",
                before="2",
                limit="10",
                **common,
            ),
        ]

        self.assertEqual(
            [(call["method"], call["url"].split("?", 1)[0]) for call in calls],
            [
                ("GET", "https://api-contract.weex.com/capi/v3/account/accountConfig"),
                ("GET", "https://api-contract.weex.com/capi/v3/account/commissionRate"),
                ("GET", "https://api-contract.weex.com/capi/v3/account/symbolConfig"),
                ("GET", "https://api-contract.weex.com/capi/v3/openAlgoOrders"),
                ("GET", "https://api-contract.weex.com/capi/v3/allAlgoOrders"),
                ("GET", "https://api-spot.weex.com/api/v3/account/transferRecords"),
            ],
        )
        self.assertEqual(results[0]["config"]["canTrade"], True)
        self.assertEqual(results[1]["commission_rate"]["symbol"], "BTCUSDT")
        self.assertEqual(results[2]["symbol_configs"][0]["symbol"], "BTCUSDT")
        self.assertEqual(results[3]["conditional_orders"][0]["algoId"], "1")
        self.assertFalse(results[4]["has_more"])
        self.assertEqual(results[5]["transfer_records"][0]["status"], "SUCCESS")

        for call in calls:
            self.assertEqual(call["method"], "GET")
            self.assertIsNone(call["data"])

    def test_upstream_account_symbols_fail_as_invalid_responses(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        payloads = iter(
            [
                {"symbol": "@@@", "makerCommissionRate": "0.1", "takerCommissionRate": "0.2"},
                [{"symbol": "@@@", "marginType": "CROSSED"}],
            ]
        )
        adapter = WeexAccountRestAdapter(
            secret_provider=self._provider(),
            transport=lambda prepared, timeout: json.dumps(next(payloads)).encode(),
            time_func=lambda: 1,
        )
        common = {"credential_argument": self._credential(), "context": self._context()}

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_commission_rate(
                market="futures", trading_mode="live", symbol="BTCUSDT", **common
            )
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_symbol_config(
                market="futures", trading_mode="live", symbol="BTCUSDT", **common
            )

    def test_new_account_constraints_reject_unsupported_modes_and_ranges_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter

        calls = []
        adapter = WeexAccountRestAdapter(
            secret_provider=self._provider(),
            transport=lambda prepared, timeout: calls.append(prepared) or b"[]",
        )
        common = {"credential_argument": self._credential(), "context": self._context()}

        with self.assertRaises(ValueError):
            adapter.get_conditional_orders(
                market="futures", trading_mode="demo", status="open", **common
            )
        with self.assertRaises(ValueError):
            adapter.get_transfer_records(market="futures", trading_mode="live", **common)
        with self.assertRaises(ValueError):
            adapter.get_transfer_records(
                market="spot", trading_mode="live", after="2", before="1", **common
            )
        with self.assertRaises(ValueError):
            adapter.get_conditional_orders(
                market="futures",
                trading_mode="live",
                status="history",
                start_time="1",
                **common,
            )
        self.assertEqual(calls, [])

    def test_transfer_record_limit_uses_catalog_integer_range(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter

        calls = []
        adapter = WeexAccountRestAdapter(
            secret_provider=self._provider(),
            transport=lambda prepared, timeout: calls.append(prepared) or b"[]",
        )
        adapter.get_transfer_records(
            market="spot",
            trading_mode="live",
            limit="1000",
            credential_argument=self._credential(),
            context=self._context(),
        )
        self.assertEqual(calls[0]["query"]["limit"], "1000")

    def test_spot_account_config_requires_capability_booleans(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexAccountRestAdapter(
            secret_provider=self._provider(),
            transport=lambda prepared, timeout: b"{}",
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_config(
                market="spot",
                trading_mode="live",
                credential_argument=self._credential(),
                context=self._context(),
            )

    def test_futures_trade_history_enforces_current_seven_day_and_365_day_rules(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter

        now_ms = 400 * 24 * 60 * 60 * 1000
        adapter = WeexAccountRestAdapter(
            secret_provider=self._provider(),
            transport=lambda prepared, timeout: b"[]",
            time_func=lambda: now_ms / 1000,
        )
        common = {"credential_argument": self._credential(), "context": self._context()}

        with self.assertRaises(ValueError):
            adapter.get_trades(
                market="futures",
                trading_mode="live",
                start_time="1",
                end_time=str(8 * 24 * 60 * 60 * 1000),
                **common,
            )
        with self.assertRaises(ValueError):
            adapter.get_trades(
                market="futures",
                trading_mode="live",
                start_time=str(now_ms + 1),
                end_time=str(now_ms + 2),
                **common,
            )
        with self.assertRaises(ValueError):
            adapter.get_trades(
                market="futures",
                trading_mode="live",
                start_time="1",
                end_time="2",
                **common,
            )


if __name__ == "__main__":
    unittest.main()
