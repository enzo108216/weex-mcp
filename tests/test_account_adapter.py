import json
import unittest


class FakeSecretProvider:
    def resolve(self, *, credential_argument, context, required_permission):
        from weex_mcp.secret_provider import ResolvedWeexCredential

        return ResolvedWeexCredential(
            key_id=credential_argument["key_id"],
            api_key="AK_TEST",
            api_secret="SK_TEST",
            api_passphrase="PP_TEST",
            permissions=frozenset([required_permission]),
            fingerprint="fp_test",
        )


class AccountAdapterTests(unittest.TestCase):
    def test_futures_order_history_enforces_90_day_catalog_window_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        common = {
            "market": "futures",
            "status": "history",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }
        ninety_days_ms = 90 * 24 * 60 * 60 * 1000

        for trading_mode in ("live", "demo"):
            with self.subTest(trading_mode=trading_mode, boundary="valid"):
                adapter.get_orders(
                    trading_mode=trading_mode,
                    start_time="0",
                    end_time=str(ninety_days_ms),
                    **common,
                )
            with self.subTest(trading_mode=trading_mode, boundary="too_wide"):
                with self.assertRaises(ValueError):
                    adapter.get_orders(
                        trading_mode=trading_mode,
                        start_time="0",
                        end_time=str(ninety_days_ms + 1),
                        **common,
                    )

        self.assertEqual(len(requests), 2)

    def test_futures_income_enforces_100_day_catalog_window_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b'{"items":[]}',
        )
        common = {
            "market": "futures",
            "trading_mode": "live",
            "bill_type": "income",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }
        one_hundred_days_ms = 100 * 24 * 60 * 60 * 1000

        adapter.get_bills(start_time="0", end_time=str(one_hundred_days_ms), **common)
        with self.assertRaises(ValueError):
            adapter.get_bills(start_time="0", end_time=str(one_hundred_days_ms + 1), **common)

        self.assertEqual(len(requests), 1)

    def test_spot_tax_rejects_invalid_month_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        common = {
            "market": "spot",
            "trading_mode": "live",
            "bill_type": "tax",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }

        adapter.get_bills(month="2026-08", **common)
        for month in ("2026-8", "2026-00", "2026-13", "not-a-month"):
            with self.subTest(month=month):
                with self.assertRaises(ValueError):
                    adapter.get_bills(month=month, **common)

        self.assertEqual(len(requests), 1)

    def test_account_adapter_rejects_invalid_catalog_numeric_filters_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        common = {
            "trading_mode": "live",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }
        invalid_calls = (
            lambda: adapter.get_orders(market="futures", status="single", order_id="not-long", **common),
            lambda: adapter.get_orders(market="futures", status="history", start_time="-1", **common),
            lambda: adapter.get_orders(market="futures", status="history", limit="1001", **common),
            lambda: adapter.get_orders(market="futures", status="open", limit="101", **common),
            lambda: adapter.get_bills(market="futures", bill_type="income", next_key_id="9223372036854775808", **common),
            lambda: adapter.get_bills(market="spot", bill_type="tax", page="0", **common),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(requests, [])

    def test_spot_demo_account_queries_fail_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        common = {
            "market": "spot",
            "trading_mode": "demo",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }

        calls = (
            lambda: adapter.get_orders(status="history", **common),
            lambda: adapter.get_trades(**common),
            lambda: adapter.get_bills(**common),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()

        self.assertEqual(requests, [])

    def test_futures_positions_filters_symbol_locally_without_query_parameter(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            return json.dumps(
                [
                    {"symbol": "BTCUSDT", "size": "1"},
                    {"symbol": "ETHUSDT", "size": "2"},
                ]
            ).encode()

        adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
        result = adapter.get_positions(
            market="futures",
            trading_mode="live",
            symbol="BTCUSDT",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(captured["query"], {})
        self.assertEqual(result["positions"], [{"symbol": "BTCUSDT", "size": "1"}])

    def test_demo_positions_normalize_simulated_symbols_before_filtering(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            return json.dumps([
                {"symbol": "BTCSUSDT", "size": "1"},
                {"symbol": "ETHSUSDT", "size": "2"},
            ]).encode()

        adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
        result = adapter.get_positions(
            market="futures",
            trading_mode="demo",
            symbol="BTCUSDT",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(captured["query"], {})
        self.assertEqual(result["positions"], [{"symbol": "BTCUSDT", "size": "1"}])

    def test_demo_order_history_sends_simulated_symbol_and_filters_display_symbol_locally(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            return json.dumps([
                {"symbol": "BTCSUSDT", "orderId": "1", "status": "FILLED"},
                {"symbol": "ETHSUSDT", "orderId": "2", "status": "FILLED"},
            ]).encode()

        adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
        result = adapter.get_orders(
            market="futures",
            trading_mode="demo",
            status="history",
            symbol="BTCUSDT",
            limit="20",
            page="0",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(captured["query"], {"symbol": "BTCSUSDT", "limit": "20", "page": "0"})
        self.assertEqual(result["orders"], [{"symbol": "BTCUSDT", "orderId": "1", "status": "FILLED"}])

    def test_spot_single_order_uses_orig_client_order_id_only(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            return json.dumps({"orderId": "1", "clientOrderId": "client-1", "status": "FILLED"}).encode()

        adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
        adapter.get_orders(
            market="spot",
            trading_mode="live",
            status="single",
            client_order_id="client-1",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(captured["url"], "https://api-spot.weex.com/api/v3/order?origClientOrderId=client-1")
        self.assertEqual(captured["query"], {"origClientOrderId": "client-1"})

    def test_order_history_uses_catalog_fields_and_includes_page(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        for market in ("spot", "futures"):
            with self.subTest(market=market):
                captured = {}

                def fake_transport(prepared, timeout_seconds, *, _captured=captured):
                    _captured.update(prepared)
                    return b"[]"

                adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
                adapter.get_orders(
                    market=market,
                    trading_mode="live",
                    status="history",
                    symbol="BTCUSDT",
                    start_time="100",
                    end_time="200",
                    limit="10",
                    page="2",
                    credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
                    context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                )

                self.assertEqual(
                    captured["query"],
                    {"symbol": "BTCUSDT", "startTime": "100", "endTime": "200", "limit": "10", "page": "2"},
                )

    def test_open_and_single_order_queries_only_send_endpoint_catalog_fields(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        cases = (
            ("spot", "open", {"symbol": "BTCUSDT"}),
            (
                "futures",
                "open",
                {"symbol": "BTCUSDT", "orderId": "1", "startTime": "100", "endTime": "200", "limit": "10", "page": "2"},
            ),
            ("futures", "single", {"orderId": "1"}),
        )
        for market, status, expected_query in cases:
            with self.subTest(market=market, status=status):
                captured = {}

                def fake_transport(prepared, timeout_seconds, *, _captured=captured, _status=status):
                    _captured.update(prepared)
                    return b"[]" if _status != "single" else b'{"orderId":"1","status":"FILLED"}'

                adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
                filters = {
                    ("spot", "open"): {"symbol": "BTCUSDT"},
                    ("futures", "open"): {"symbol": "BTCUSDT", "order_id": "1", "start_time": "100", "end_time": "200", "limit": "10", "page": "2"},
                    ("futures", "single"): {"order_id": "1"},
                }[(market, status)]
                adapter.get_orders(
                    market=market,
                    trading_mode="live",
                    status=status,
                    **filters,
                    credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
                    context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                )

                self.assertEqual(captured["query"], expected_query)

    def test_bill_queries_map_normalized_fields_per_endpoint_catalog(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        cases = (
            (
                "spot",
                "account",
                "/api/v3/account/bills",
                {"coinId": "7", "bizType": "trade_out", "after": "100", "before": "200", "limit": "10"},
            ),
            (
                "spot",
                "funding",
                "/api/v3/account/fundingBills",
                {"coinId": "7", "bizType": "trade_out", "startTime": "100", "endTime": "200", "pageIndex": "2", "pageSize": "10"},
            ),
            (
                "spot",
                "tax",
                "/api/v3/tax/income",
                {"coin": "USDT", "bizType": "trade_out", "month": "2026-08", "limit": "10", "page": "2"},
            ),
            (
                "futures",
                "income",
                "/capi/v3/account/income",
                {
                    "asset": "USDT",
                    "symbol": "BTCUSDT",
                    "incomeType": "position_funding",
                    "startTime": "100",
                    "endTime": "200",
                    "limit": "10",
                    "nextKeyId": "99",
                    "nextKeyTime": "88",
                },
            ),
        )
        for market, bill_type, expected_path, expected_body in cases:
            with self.subTest(market=market, bill_type=bill_type):
                captured = {}

                def fake_transport(prepared, timeout_seconds, *, _captured=captured):
                    _captured.update(prepared)
                    return b"[]"

                adapter = WeexAccountRestAdapter(secret_provider=FakeSecretProvider(), transport=fake_transport)
                bill_filters = {
                    ("spot", "account"): {"coin_id": "7", "biz_type": "trade_out", "start_time": "100", "end_time": "200", "limit": "10"},
                    ("spot", "funding"): {"coin_id": "7", "biz_type": "trade_out", "start_time": "100", "end_time": "200", "page": "2", "limit": "10"},
                    ("spot", "tax"): {"asset": "USDT", "biz_type": "trade_out", "month": "2026-08", "limit": "10", "page": "2"},
                    ("futures", "income"): {"asset": "USDT", "symbol": "BTCUSDT", "income_type": "position_funding", "start_time": "100", "end_time": "200", "limit": "10", "next_key_id": "99", "next_key_time": "88"},
                }[(market, bill_type)]
                adapter.get_bills(
                    market=market,
                    trading_mode="live",
                    bill_type=bill_type,
                    **bill_filters,
                    credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
                    context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                )

                self.assertTrue(captured["url"].endswith(expected_path))
                self.assertEqual(captured["body"], expected_body)

    def test_account_queries_reject_catalog_required_filters_before_transport(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        common = {
            "trading_mode": "live",
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }

        calls = (
            lambda: adapter.get_orders(market="spot", status="history", **common),
            lambda: adapter.get_orders(market="spot", status="single", **common),
            lambda: adapter.get_orders(market="futures", status="single", **common),
            lambda: adapter.get_trades(market="spot", **common),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()

        self.assertEqual(requests, [])

    def test_order_query_rejects_filters_not_supported_by_endpoint_instead_of_dropping_them(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        requests = []
        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: requests.append(prepared) or b"[]",
        )
        with self.assertRaises(ValueError):
            adapter.get_orders(
                market="spot",
                trading_mode="live",
                status="open",
                symbol="BTCUSDT",
                order_id="1",
                start_time="100",
                limit="10",
                credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
                context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
            )
        self.assertEqual(requests, [])

    def test_private_resource_normalization_rejects_garbage_objects(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        common = {
            "credential_argument": {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            "context": TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        }
        for name, call in (
            ("positions", lambda adapter: adapter.get_positions(market="futures", trading_mode="live", **common)),
            ("orders", lambda adapter: adapter.get_orders(market="futures", trading_mode="live", status="open", **common)),
            ("trades", lambda adapter: adapter.get_trades(market="spot", trading_mode="live", symbol="BTCUSDT", **common)),
            ("bills", lambda adapter: adapter.get_bills(market="futures", trading_mode="live", bill_type="income", **common)),
        ):
            with self.subTest(resource=name):
                adapter = WeexAccountRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    transport=lambda prepared, timeout: b'{"data":{"garbage":"x"}}',
                )
                with self.assertRaises(WeexRestBusinessError) as ctx:
                    call(adapter)
                self.assertEqual(ctx.exception.code, "invalid_response")

    def test_paginated_bill_object_with_items_remains_valid(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: b'{"data":{"items":[{"income":"1"}],"nextKeyId":"2"}}',
        )
        result = adapter.get_bills(
            market="futures",
            trading_mode="live",
            bill_type="income",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(result["bills"]["items"], [{"income": "1"}])
        self.assertEqual(result["bills"]["nextKeyId"], "2")

    def test_account_list_responses_reject_non_object_items(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        context = TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a")
        credential = {"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"}
        cases = (
            (
                "balances",
                b'["garbage"]',
                lambda adapter: adapter.get_balance(
                    market="spot", trading_mode="live", credential_argument=credential, context=context
                ),
            ),
            (
                "positions",
                b'["garbage"]',
                lambda adapter: adapter.get_positions(
                    market="futures", trading_mode="live", credential_argument=credential, context=context
                ),
            ),
            (
                "orders",
                b'["garbage"]',
                lambda adapter: adapter.get_orders(
                    market="futures", trading_mode="live", status="open", credential_argument=credential, context=context
                ),
            ),
            (
                "trades",
                b'["garbage"]',
                lambda adapter: adapter.get_trades(
                    market="spot", trading_mode="live", symbol="BTCUSDT", credential_argument=credential, context=context
                ),
            ),
            (
                "bills",
                b'["garbage"]',
                lambda adapter: adapter.get_bills(
                    market="futures", trading_mode="live", bill_type="income", credential_argument=credential, context=context
                ),
            ),
            (
                "paginated_bills",
                b'{"data":{"items":["garbage"],"nextKeyId":"2"}}',
                lambda adapter: adapter.get_bills(
                    market="futures", trading_mode="live", bill_type="income", credential_argument=credential, context=context
                ),
            ),
        )
        for name, response_body, call in cases:
            with self.subTest(resource=name):
                adapter = WeexAccountRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    transport=lambda prepared, timeout, response_body=response_body: response_body,
                )
                with self.assertRaises(WeexRestBusinessError) as ctx:
                    call(adapter)
                self.assertEqual(ctx.exception.code, "invalid_response")

    def test_account_empty_lists_remain_valid(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: b"[]",
        )
        result = adapter.get_balance(
            market="spot",
            trading_mode="live",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(result["balances"], [])

    def test_account_uses_provider_permissions_when_request_omits_permissions(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.secret_provider import StaticMappingSecretProvider
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        provider = StaticMappingSecretProvider(
            {
                "credref_provider_only": {
                    "api_key": "AK",
                    "api_secret": "SK",
                    "api_passphrase": "PP",
                    "permissions": ["USER_DATA"],
                }
            }
        )
        adapter = WeexAccountRestAdapter(
            secret_provider=provider,
            transport=lambda prepared, timeout: b'{"balances": []}',
        )
        service = build_default_service(account_adapter=adapter)
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account_provider_only",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "credential_ref",
                            "ref": "credref_provider_only",
                            "key_id": "key_provider_only",
                        },
                    },
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(response["result"]["data"]["market"], "spot")

    def test_default_request_provider_signs_plaintext_account_call_without_self_asserted_permission(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.secret_provider import RequestSecretProvider
        from weex_mcp.service import build_default_service

        private_calls = []

        def transport(prepared, timeout):
            private_calls.append(prepared)
            return b'{"balances": []}'

        adapter = WeexAccountRestAdapter(
            secret_provider=RequestSecretProvider(),
            transport=transport,
        )
        response = build_default_service(account_adapter=adapter).handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account-inline-provider",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "plaintext",
                            "api_key": "AK_INLINE",
                            "api_secret": "SK_INLINE",
                            "api_passphrase": "PP_INLINE",
                        },
                    },
                },
            }
        )

        self.assertTrue(response["result"]["ok"])
        self.assertEqual(len(private_calls), 1)
        self.assertEqual(private_calls[0]["method"], "GET")
        self.assertEqual(private_calls[0]["url"], "https://api-spot.weex.com/api/v3/account")
        serialized = json.dumps(response, sort_keys=True)
        self.assertNotIn("AK_INLINE", serialized)
        self.assertNotIn("SK_INLINE", serialized)
        self.assertNotIn("PP_INLINE", serialized)

    def test_account_tool_fails_closed_when_secret_provider_unconfigured(self):
        from weex_mcp.service import build_default_service
        from weex_mcp.tenant_context import TenantContext

        service = build_default_service()
        response = service.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "account_1",
                "method": "tools/call",
                "params": {
                    "name": "account_get_balance",
                    "arguments": {
                        "market": "spot",
                        "trading_mode": "live",
                        "credential": {
                            "type": "credential_ref",
                            "ref": "credref_a",
                            "key_id": "key_a",
                            "permissions": ["USER_DATA"],
                        },
                    },
                },
            },
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertFalse(response["result"]["ok"])
        self.assertEqual(response["result"]["error"]["code"], "secret_provider_unconfigured")
        self.assertNotIn("credref_a", str(response))

    def test_weex_account_adapter_builds_signed_spot_balance_request(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            captured["timeout_seconds"] = timeout_seconds
            return json.dumps({"balances": [{"asset": "USDT", "free": "1", "locked": "0"}]}).encode()

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=fake_transport,
            time_func=lambda: 1234.567,
            timeout_seconds=3.0,
        )
        result = adapter.get_balance(
            market="spot",
            trading_mode="live",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(result["market"], "spot")
        self.assertEqual(result["balances"][0]["asset"], "USDT")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "https://api-spot.weex.com/api/v3/account")
        self.assertEqual(captured["headers"]["ACCESS-KEY"], "AK_TEST")
        self.assertEqual(captured["headers"]["ACCESS-PASSPHRASE"], "PP_TEST")
        self.assertEqual(captured["headers"]["ACCESS-TIMESTAMP"], "1234567")
        self.assertIn("ACCESS-SIGN", captured["headers"])
        self.assertEqual(captured["timeout_seconds"], 3.0)

    def test_weex_account_adapter_uses_sim_balance_endpoint_for_futures_demo(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.tenant_context import TenantContext

        captured = {}

        def fake_transport(prepared, timeout_seconds):
            captured.update(prepared)
            return json.dumps({"data": [{"marginCoin": "USDT", "availableBalance": "9"}]}).encode()

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=fake_transport,
            time_func=lambda: 1234.567,
        )
        result = adapter.get_balance(
            market="futures",
            trading_mode="demo",
            credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
            context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
        )

        self.assertEqual(result["market"], "futures")
        self.assertEqual(result["trading_mode"], "demo")
        self.assertEqual(captured["url"], "https://api-contract.weex.com/capi/v3/sim/balance")

    def test_weex_account_adapter_rejects_business_error_payload(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        def fake_transport(prepared, timeout_seconds):
            return json.dumps({"code": "401001", "msg": "invalid signature", "data": None}).encode()

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=fake_transport,
            time_func=lambda: 1234.567,
        )

        with self.assertRaises(WeexRestBusinessError):
            adapter.get_balance(
                market="futures",
                trading_mode="demo",
                credential_argument={"type": "credential_ref", "ref": "credref_a", "key_id": "key_a"},
                context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
            )

    def test_account_balance_rejects_explicit_success_false_even_with_balances(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        adapter = WeexAccountRestAdapter(
            secret_provider=FakeSecretProvider(),
            transport=lambda prepared, timeout: b'{"success":false,"data":{"balances":[{"asset":"USDT"}]}}',
        )
        with self.assertRaises(WeexRestBusinessError):
            adapter.get_balance(
                market="spot",
                trading_mode="live",
                credential_argument={"type": "credential_ref", "ref": "ref", "key_id": "key"},
                context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
            )

    def test_account_balance_rejects_nested_business_failure(self):
        from weex_mcp.adapters.account import WeexAccountRestAdapter
        from weex_mcp.adapters.rest_errors import WeexRestBusinessError
        from weex_mcp.tenant_context import TenantContext

        for response in (
            b'{"code":"0","data":{"success":false,"balances":[]}}',
            b'{"code":"0","data":{"code":"401001","msg":"invalid signature","balances":[]}}',
        ):
            with self.subTest(response=response):
                adapter = WeexAccountRestAdapter(
                    secret_provider=FakeSecretProvider(),
                    transport=lambda prepared, timeout, response=response: response,
                )
                with self.assertRaises(WeexRestBusinessError):
                    adapter.get_balance(
                        market="futures",
                        trading_mode="demo",
                        credential_argument={"type": "credential_ref", "ref": "ref", "key_id": "key"},
                        context=TenantContext(tenant_id="tenant_a", user_id="user_a", session_id="session_a"),
                    )


if __name__ == "__main__":
    unittest.main()
