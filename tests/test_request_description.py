from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


class RequestDescriptionTests(unittest.TestCase):
    def settings(self):
        from weex_mcp.settings import Settings

        return Settings(
            trade_enabled=True,
            spot_base_url="https://local-spot.weex.tech",
            futures_base_url="https://local-futures.weex.tech",
            request_locale="zh-CN",
        )

    def test_local_library_describes_market_account_and_trade_without_io(self):
        from weex_mcp.local import describe_request

        with (
            patch(
                "weex_mcp.adapters.account._sign",
                side_effect=AssertionError("account signing must not run"),
            ),
            patch(
                "weex_mcp.adapters.trade_rest._prepare_signed_request",
                side_effect=AssertionError("trade signing must not run"),
            ),
        ):
            market = describe_request(
                settings=self.settings(),
                name="market_get_symbol_price",
                arguments={"market": "spot", "symbol": "BTCUSDT"},
            )
            account = describe_request(
                settings=self.settings(),
                name="account_get_balance",
                arguments={"market": "futures", "trading_mode": "live"},
            )
            trade = describe_request(
                settings=self.settings(),
                name="trade_preview_order",
                arguments={
                    "market": "spot",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "1",
                    "client_order_id": "dry-run-1",
                },
            )

        self.assertEqual(market["method"], "GET")
        self.assertEqual(
            market["url"],
            "https://local-spot.weex.tech/api/v3/market/ticker/price?symbol=BTCUSDT",
        )
        self.assertEqual(market["query"], {"symbol": "BTCUSDT"})
        self.assertEqual(market["body"], {})
        self.assertEqual(market["headers"]["User-Agent"], "weex-mcp/0.1 public-market")
        self.assertNotIn("ACCESS-SIGN", market["headers"])

        self.assertEqual(account["method"], "GET")
        self.assertEqual(
            account["url"],
            "https://local-futures.weex.tech/capi/v3/account/balance",
        )
        self.assertEqual(account["headers"]["ACCESS-SIGN"], "***")
        self.assertEqual(
            account["headers"]["User-Agent"],
            "weex-mcp/0.1 account-readonly",
        )
        self.assertEqual(
            account["headers"]["ACCESS-TIMESTAMP"],
            "<generated-at-send>",
        )
        self.assertNotIn("credential", account)

        self.assertEqual(trade["method"], "POST")
        self.assertEqual(
            trade["url"],
            "https://local-spot.weex.tech/api/v3/order",
        )
        self.assertEqual(trade["body"]["newClientOrderId"], "dry-run-1")
        self.assertEqual(trade["headers"]["User-Agent"], "weex-mcp/0.1 trade")
        self.assertEqual(trade["headers"]["ACCESS-SIGN"], "***")

    def test_description_rejects_sensitive_and_non_request_tools(self):
        from weex_mcp.local import describe_request
        from weex_mcp.request_description import RequestDescriptionError

        cases = (
            (
                "account_get_balance",
                {
                    "market": "spot",
                    "trading_mode": "live",
                    "credential": {
                        "type": "credential_ref",
                        "key_id": "key",
                        "ref": "ref",
                    },
                },
            ),
            ("trade_confirm_order", {}),
            ("partner_verify_referrals", {}),
            ("broker_oauth_generate_authorization_url", {}),
            ("unknown_tool", {}),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                with self.assertRaises(RequestDescriptionError):
                    describe_request(
                        settings=self.settings(),
                        name=name,
                        arguments=arguments,
                    )

    def test_description_rejects_simple_leverage_that_requires_live_state(self):
        from weex_mcp.local import describe_request
        from weex_mcp.request_description import RequestDescriptionError

        with self.assertRaises(RequestDescriptionError) as exc_info:
            describe_request(
                settings=self.settings(),
                name="trade_preview_leverage",
                arguments={
                    "market": "futures",
                    "trading_mode": "live",
                    "symbol": "BTCUSDT",
                    "margin_mode": "cross",
                    "leverage": "5",
                },
            )

        self.assertEqual(exc_info.exception.code, "description_requires_live_state")

    def test_all_local_read_and_trade_preview_routes_are_describable(self):
        from weex_mcp.local import describe_request

        read_cases = {
            "market_get_symbol_price": ({"market": "spot", "symbol": "BTCUSDT"}, "/api/v3/market/ticker/price"),
            "market_get_depth": ({"market": "spot", "symbol": "BTCUSDT", "limit": "15"}, "/api/v3/market/depth"),
            "market_get_klines": ({"market": "spot", "symbol": "BTCUSDT", "interval": "1m"}, "/api/v3/market/klines"),
            "market_get_exchange_info": ({"market": "spot", "symbol": "BTCUSDT"}, "/api/v3/exchangeInfo"),
            "market_get_server_time": ({"market": "futures"}, "/capi/v3/market/time"),
            "market_ping": ({"market": "spot"}, "/api/v3/ping"),
            "market_get_ticker_24h": ({"market": "spot", "symbol": "BTCUSDT"}, "/api/v3/market/ticker/24hr"),
            "market_get_book_ticker": ({"market": "futures", "symbol": "BTCUSDT"}, "/capi/v3/market/ticker/bookTicker"),
            "market_get_recent_trades": ({"market": "futures", "symbol": "BTCUSDT", "limit": "10"}, "/capi/v3/market/trades"),
            "market_get_funding_rate": ({"market": "futures", "mode": "current", "symbol": "BTCUSDT"}, "/capi/v3/market/premiumIndex"),
            "market_get_open_interest": ({"market": "futures", "symbol": "BTCUSDT"}, "/capi/v3/market/openInterest"),
            "market_get_advanced_klines": ({"market": "futures", "kind": "history", "symbol": "BTCUSDT", "interval": "1m"}, "/capi/v3/market/historyKlines"),
            "market_get_catalog": ({"market": "spot", "catalog": "coins"}, "/api/v3/coins"),
            "account_get_balance": ({"market": "spot", "trading_mode": "live"}, "/api/v3/account"),
            "account_get_positions": ({"market": "futures", "trading_mode": "live"}, "/capi/v3/account/position/allPosition"),
            "account_get_position": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}, "/capi/v3/account/position/singlePosition"),
            "account_get_orders": ({"market": "futures", "trading_mode": "live", "status": "open"}, "/capi/v3/openOrders"),
            "account_get_trades": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}, "/capi/v3/userTrades"),
            "account_get_bills": ({"market": "futures", "trading_mode": "live", "bill_type": "income"}, "/capi/v3/account/income"),
            "account_get_config": ({"market": "spot", "trading_mode": "live"}, "/api/v3/account"),
            "account_get_commission_rate": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}, "/capi/v3/account/commissionRate"),
            "account_get_symbol_config": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT"}, "/capi/v3/account/symbolConfig"),
            "account_get_conditional_orders": ({"market": "futures", "trading_mode": "live", "status": "open"}, "/capi/v3/openAlgoOrders"),
            "account_get_transfer_records": ({"market": "spot", "trading_mode": "live"}, "/api/v3/account/transferRecords"),
        }
        trade_cases = {
            "trade_preview_order": ({"market": "spot", "trading_mode": "live", "symbol": "BTCUSDT", "side": "BUY", "order_type": "MARKET", "quantity": "1", "client_order_id": "order-1"}, "/api/v3/order"),
            "trade_preview_cancel_order": ({"market": "futures", "trading_mode": "live", "order_id": "1"}, "/capi/v3/order"),
            "trade_preview_leverage": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "cross_leverage": "5"}, "/capi/v3/account/leverage"),
            "trade_preview_margin_mode": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "margin_mode": "cross"}, "/capi/v3/account/marginType"),
            "trade_preview_batch_order": ({"market": "futures", "trading_mode": "live", "orders": [{"symbol": "BTCUSDT", "side": "BUY", "position_side": "LONG", "order_type": "MARKET", "quantity": "1", "client_order_id": "batch-1"}]}, "/capi/v3/batchOrders"),
            "trade_preview_batch_cancel": ({"market": "spot", "trading_mode": "live", "order_ids": ["1"]}, "/api/v3/order/batch"),
            "trade_preview_cancel_scope": ({"market": "spot", "trading_mode": "live", "order_kind": "normal", "scope": "symbol", "symbol": "BTCUSDT"}, "/api/v3/openOrders"),
            "trade_preview_conditional_order": ({"market": "futures", "trading_mode": "live", "symbol": "BTCUSDT", "side": "SELL", "position_side": "LONG", "conditional_type": "STOP_MARKET", "quantity": "1", "trigger_price": "100", "client_algo_id": "algo-1"}, "/capi/v3/algoOrder"),
            "trade_preview_conditional_cancel": ({"market": "futures", "trading_mode": "live", "order_id": "1"}, "/capi/v3/algoOrder"),
            "trade_preview_tp_sl": ({"market": "futures", "trading_mode": "live", "operation": "modify", "order_id": "1", "trigger_price": "100"}, "/capi/v3/modifyTpSlOrder"),
            "trade_preview_close_positions": ({"market": "futures", "trading_mode": "live", "scope": "all", "acknowledge_all_positions": True}, "/capi/v3/closePositions"),
            "trade_preview_position_margin": ({"market": "futures", "trading_mode": "live", "isolated_position_id": "1", "amount": "1", "adjustment": "increase"}, "/capi/v3/account/positionMargin"),
            "trade_preview_auto_append_margin": ({"market": "futures", "trading_mode": "live", "position_id": "1", "enabled": True}, "/capi/v3/account/modifyAutoAppendMargin"),
        }

        self.assertEqual(len(read_cases), 24)
        self.assertEqual(len(trade_cases), 13)
        for name, (arguments, path) in {**read_cases, **trade_cases}.items():
            with self.subTest(name=name):
                description = describe_request(
                    settings=self.settings(),
                    name=name,
                    arguments=arguments,
                )
                self.assertEqual(urlsplit(description["url"]).path, path)
                manifest = description["_meta"]["com.weex/mcp"]["compatibility"]
                self.assertEqual(manifest["runtime_profile"], "local_user")
                self.assertTrue(manifest["trade_enabled"])


if __name__ == "__main__":
    unittest.main()
