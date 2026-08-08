import json
import unittest


class FakeMarketAdapter:
    def __init__(self):
        self.calls = []

    def get_symbol_price(self, *, market, symbol, price_type=None):
        self.calls.append((market, symbol, price_type))
        return {
            "market": market,
            "symbol": symbol,
            "price": "123.45",
            "source": "fake_market_adapter",
            "raw": {"symbol": symbol, "price": "123.45"},
        }


class MarketAdapterTests(unittest.TestCase):
    def test_market_adapter_rejects_catalog_invalid_depth_and_kline_arguments_before_transport(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        requests = []
        adapter = WeexMarketRestAdapter(transport=lambda url, timeout: requests.append(url) or b"[]")
        invalid_calls = (
            lambda: adapter.get_depth(market="spot", symbol="BTCUSDT", limit="10"),
            lambda: adapter.get_klines(market="spot", symbol="BTCUSDT", interval="3h"),
            lambda: adapter.get_klines(market="futures", symbol="BTCUSDT", interval="2h"),
            lambda: adapter.get_klines(market="futures", symbol="BTCUSDT", interval="1m", limit="1001"),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(requests, [])

    def test_kline_urls_only_include_fields_supported_by_each_market_catalog(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        requested_urls = []

        def fake_transport(url, timeout_seconds):
            requested_urls.append(url)
            return b"[]"

        adapter = WeexMarketRestAdapter(transport=fake_transport)
        adapter.get_klines(
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            start_time="100",
            end_time="200",
            limit="10",
        )
        adapter.get_klines(
            market="futures",
            symbol="BTCUSDT",
            interval="1m",
            start_time="100",
            end_time="200",
            limit="10",
        )

        self.assertEqual(
            requested_urls[0],
            "https://api-spot.weex.com/api/v3/market/klines?symbol=BTCUSDT&interval=1m",
        )
        self.assertEqual(
            requested_urls[1],
            "https://api-contract.weex.com/capi/v3/market/klines?symbol=BTCUSDT&interval=1m&limit=10",
        )

    def test_market_tool_uses_injected_adapter_and_normalizes_symbol(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        adapter = FakeMarketAdapter()
        service = build_default_service(market_adapter=adapter)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "market_1",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "btc/usdt"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(response["result"]["data"]["price"], "123.45")
        self.assertEqual(response["result"]["data"]["source"], "fake_market_adapter")
        self.assertEqual(adapter.calls, [("spot", "BTCUSDT", None)])

    def test_weex_market_rest_adapter_builds_spot_and_futures_urls(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        requested_urls = []

        def fake_transport(url, timeout_seconds):
            requested_urls.append((url, timeout_seconds))
            symbol = "ETHUSDT" if "ETHUSDT" in url else "BTCUSDT"
            return json.dumps({"symbol": symbol, "price": "50000.01"}).encode()

        adapter = WeexMarketRestAdapter(transport=fake_transport, timeout_seconds=2.5)

        spot = adapter.get_symbol_price(market="spot", symbol="BTCUSDT")
        futures = adapter.get_symbol_price(market="futures", symbol="ETHUSDT", price_type="MARK")

        self.assertEqual(spot["price"], "50000.01")
        self.assertIn("https://api-spot.weex.com/api/v3/market/ticker/price?symbol=BTCUSDT", requested_urls[0][0])
        self.assertEqual(requested_urls[0][1], 2.5)
        self.assertIn("https://api-contract.weex.com/capi/v3/market/symbolPrice?", requested_urls[1][0])
        self.assertIn("symbol=ETHUSDT", requested_urls[1][0])
        self.assertIn("priceType=MARK", requested_urls[1][0])
        self.assertEqual(futures["source"], "weex_public_rest")

    def test_spot_price_rejects_price_type_and_futures_accepts_only_catalog_values(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        requested_urls = []
        adapter = WeexMarketRestAdapter(transport=lambda url, timeout: requested_urls.append(url) or b'{"symbol":"BTCUSDT","price":"1"}')
        with self.assertRaises(ValueError):
            adapter.get_symbol_price(market="spot", symbol="BTCUSDT", price_type="MARK")
        for price_type in ("INDEX", "MARK"):
            adapter.get_symbol_price(market="futures", symbol="BTCUSDT", price_type=price_type)
        with self.assertRaises(ValueError):
            adapter.get_symbol_price(market="futures", symbol="BTCUSDT", price_type="LAST")
        self.assertEqual(len(requested_urls), 2)

    def test_symbol_price_rejects_response_for_a_different_symbol(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: b'{"symbol":"ETHUSDT","price":"1"}'
        )
        with self.assertRaises(WeexRestBusinessError) as ctx:
            adapter.get_symbol_price(market="spot", symbol="BTCUSDT")
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_exchange_info_rejects_response_for_a_different_requested_symbol(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        for response in (
            b'{"symbols":[{"symbol":"ETHUSDT"}]}',
            b'{"symbol":["ETHUSDT"]}',
        ):
            with self.subTest(response=response):
                adapter = WeexMarketRestAdapter(
                    transport=lambda url, timeout, response=response: response
                )
                with self.assertRaises(WeexRestBusinessError) as ctx:
                    adapter.get_exchange_info(market="futures", symbol="BTCUSDT")
                self.assertEqual(ctx.exception.code, "invalid_response")

    def test_exchange_info_accepts_matching_requested_symbol(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter

        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: b'{"symbols":[{"symbol":"BTCUSDT"},{"symbol":"ETHUSDT"}]}'
        )
        result = adapter.get_exchange_info(market="spot", symbol="btc/usdt")

        self.assertEqual(result["symbol"], "BTCUSDT")

    def test_market_adapter_error_fails_closed_without_leaking_url(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        class BrokenAdapter:
            def get_symbol_price(self, *, market, symbol, price_type=None):
                raise RuntimeError("https://api-spot.weex.com/secret?api_key=AK")

        service = build_default_service(market_adapter=BrokenAdapter())
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "market_2",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "upstream_error")
        self.assertNotIn("AK", str(response))

    def test_weex_market_rest_adapter_rejects_business_error_payload(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        def fake_transport(url, timeout_seconds):
            return json.dumps({"code": "400001", "msg": "bad symbol", "data": None}).encode()

        adapter = WeexMarketRestAdapter(transport=fake_transport)

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_symbol_price(market="spot", symbol="BTCUSDT")

    def test_market_tool_returns_error_when_price_missing(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        def fake_transport(url, timeout_seconds):
            return json.dumps({"code": "0", "msg": "success", "data": {}}).encode()

        service = build_default_service(market_adapter=WeexMarketRestAdapter(transport=fake_transport))
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "market_3",
                "method": "tools/call",
                "params": {
                    "name": "market_get_symbol_price",
                    "arguments": {"market": "spot", "symbol": "BTCUSDT"},
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "upstream_error")

    def test_market_price_rejects_explicit_success_false_even_with_data(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: b'{"success":false,"data":{"price":"1"}}'
        )
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_symbol_price(market="spot", symbol="BTCUSDT")

    def test_market_depth_rejects_explicit_success_false_even_with_order_book(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        adapter = WeexMarketRestAdapter(
            transport=lambda url, timeout: b'{"success":false,"data":{"bids":[],"asks":[]}}'
        )
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_depth(market="spot", symbol="BTCUSDT")

    def test_market_rejects_nested_business_failure(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        for response in (
            b'{"code":"0","data":{"success":false,"price":"1"}}',
            b'{"code":"0","data":{"code":"400001","msg":"bad symbol","price":"1"}}',
        ):
            with self.subTest(response=response):
                adapter = WeexMarketRestAdapter(transport=lambda url, timeout, response=response: response)
                with self.assertRaises(WeexRestBusinessError):
                    adapter.get_symbol_price(market="spot", symbol="BTCUSDT")

    def test_market_rejects_empty_and_non_finite_prices(self):
        from weex_mcp.adapters.market import WeexMarketRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError

        for value in ("", "NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                adapter = WeexMarketRestAdapter(
                    transport=lambda url, timeout, value=value: json.dumps({"price": value}).encode()
                )
                with self.assertRaises(WeexRestBusinessError):
                    adapter.get_symbol_price(market="spot", symbol="BTCUSDT")


if __name__ == "__main__":
    unittest.main()
