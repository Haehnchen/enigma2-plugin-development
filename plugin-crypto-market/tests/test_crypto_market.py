from decimal import Decimal
from gettext import translation
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit


from .. import market, preferences


D = Decimal


def symbol(name, base, quote, status="TRADING", spot=True):
    return {
        "symbol": name, "baseAsset": base, "quoteAsset": quote,
        "status": status, "isSpotTradingAllowed": spot,
    }


def ticker(name, price, change, volume):
    return {"symbol": name, "lastPrice": price, "priceChangePercent": change, "quoteVolume": volume}


def response(value):
    return BytesIO(json.dumps(value).encode("utf-8"))


class BinanceTest(unittest.TestCase):
    def test_catalog_requests_public_endpoints_and_excludes_non_spot_markets(self):
        exchange_info = {"symbols": [
            symbol("BTCUSDT", "BTC", "USDT"),
            symbol("ETHBTC", "ETH", "BTC"),
            symbol("CLOSEDUSDT", "CLOSED", "USDT", status="BREAK"),
            symbol("FUTUREUSDT", "FUTURE", "USDT", spot=False),
        ]}
        quotes = [
            ticker("BTCUSDT", "50000.25", "+1.25", "1000000.00"),
            ticker("ETHBTC", "0.0501", "-2.35", "40.0"),
            ticker("CLOSEDUSDT", "1", "1", "999999999"),
            ticker("FUTUREUSDT", "1", "1", "999999999"),
        ]
        binance = market.Binance()
        with patch.object(market, "urlopen", side_effect=[
            response(exchange_info), response(quotes),
            response([ticker("BTCUSDT", "51000", "-1", "100")]),
        ]) as fetch:
            pairs, tickers = binance.market_snapshot()
            self.assertEqual(set(pairs), {"BTCUSDT", "ETHBTC"})
            self.assertEqual(tickers["ETHBTC"], market.Ticker(D("0.0501"), D("-2.35"), D("40.0")))
            _, refreshed = binance.market_snapshot()
            self.assertEqual(refreshed["BTCUSDT"].price, D("51000"))

        requests = [urlsplit(call.args[0].full_url) for call in fetch.call_args_list]
        self.assertEqual([request.path for request in requests], [
            "/api/v3/exchangeInfo", "/api/v3/ticker/24hr", "/api/v3/ticker/24hr",
        ])
        self.assertEqual(parse_qs(requests[0].query), {
            "permissions": ["SPOT"], "showPermissionSets": ["false"], "symbolStatus": ["TRADING"],
        })
        self.assertTrue(all(request.netloc == "data-api.binance.vision" for request in requests))
        self.assertFalse(fetch.call_args_list[0].args[0].has_header("X-Mbx-apikey"))
        self.assertEqual(fetch.call_args_list[0].kwargs, {"timeout": 8})

    def test_watchlist_loads_selected_quotes_in_one_request(self):
        rows = [ticker("BTCUSDT", "50000", "1.25", "3500"), ticker("ETHUSDT", "2000", "-2.5", "2500")]
        with patch.object(market, "urlopen", return_value=response(rows)) as fetch:
            tickers = market.Binance().watchlist_quotes(("BTCUSDT", "ETHUSDT"))
        self.assertEqual(tickers["ETHUSDT"].change, D("-2.5"))
        fetch.assert_called_once()
        request = urlsplit(fetch.call_args.args[0].full_url)
        self.assertEqual(request.path, "/api/v3/ticker/24hr")
        self.assertEqual(json.loads(parse_qs(request.query)["symbols"][0]), ["BTCUSDT", "ETHUSDT"])

    def test_long_watchlist_batches_are_bounded(self):
        symbols = [f"ASSET{i}USDT" for i in range(market.TICKER_BATCH_SIZE + 1)]
        with patch.object(market, "urlopen", side_effect=[response([]), response([])]) as fetch:
            market.Binance().watchlist_quotes(symbols)
        batches = [json.loads(parse_qs(urlsplit(call.args[0].full_url).query)["symbols"][0])
                   for call in fetch.call_args_list]
        self.assertEqual(batches, [symbols[:-1], symbols[-1:]])

    def test_removed_symbol_keeps_other_quotes_available(self):
        error = HTTPError(market.API, 400, "Bad Request", None, response({"code": -1121, "msg": "Invalid symbol."}))
        with patch.object(market, "urlopen", side_effect=[
            error, response([ticker("BTCUSDT", "50000", "0", "100"), ticker("ETHUSDT", "2000", "0", "100")]),
        ]) as fetch:
            tickers = market.Binance().watchlist_quotes(("BTCUSDT", "REMOVEDUSDT"))
        self.assertEqual(set(tickers), {"BTCUSDT"})
        self.assertEqual(urlsplit(fetch.call_args.args[0].full_url).query, "")

    def test_rank_combines_60_percent_market_cap_and_40_percent_volume(self):
        pairs = {
            name: market.Pair(name, base, quote)
            for name, base, quote in (
                ("AUSDT", "A", "USDT"), ("BUSDC", "B", "USDC"), ("CUSDT", "C", "USDT"),
                ("AU", "A", "U"), ("ABTC", "A", "BTC"), ("NEWUSDT", "NEW", "USDT"),
            )
        }
        tickers = {
            name: market.Ticker(D("1"), D("0"), D(volume))
            for name, volume in (
                ("AUSDT", "100"), ("BUSDC", "500"), ("CUSDT", "200"),
                ("AU", "3000"), ("ABTC", "4000"), ("NEWUSDT", "5000"),
            )
        }
        tickers["USDCUSDT"] = market.Ticker(D("1.10"), D("0"), D("9000"))
        ranked = market.rank_pairs(pairs, tickers, {"A": 1, "B": 200, "C": 70})
        self.assertEqual([(pair.symbol, volume) for pair, volume in ranked], [
            ("AUSDT", D("100")), ("CUSDT", D("200")), ("BUSDC", D("550")),
        ])

    def test_rank_returns_at_most_200_eligible_pairs(self):
        pairs = {
            f"ASSET{i}{quote}": market.Pair(f"ASSET{i}{quote}", f"ASSET{i}", quote)
            for i in range(110) for quote in ("USDT", "USDC")
        }
        tickers = {name: market.Ticker(D("1"), D("0"), D(i)) for i, name in enumerate(pairs, start=1)}
        ranked = market.rank_pairs(pairs, tickers, {f"ASSET{i}": i + 1 for i in range(110)})
        self.assertEqual(len(ranked), 200)
        self.assertEqual(len({pair.symbol for pair, _ in ranked}), 200)

    def test_klines_use_requested_interval_and_quote_volume(self):
        rows = [[1700000000000, "100.1", "104.9", "99.8", "103.2", "2.5", 1700003599999, "255.6", 45]]
        for interval in market.INTERVALS:
            with self.subTest(interval=interval), patch.object(market, "urlopen", return_value=response(rows)) as fetch:
                candles = market.Binance().candles("BTCUSDT", interval)
                self.assertEqual(candles, [
                    market.Candle(1700000000000, D("100.1"), D("104.9"), D("99.8"), D("103.2"), D("255.6")),
                ])
                request = urlsplit(fetch.call_args.args[0].full_url)
                self.assertEqual(request.path, "/api/v3/klines")
                self.assertEqual(parse_qs(request.query), {"symbol": ["BTCUSDT"], "interval": [interval], "limit": ["72"]})

    def test_candle_errors_are_reported_to_the_caller(self):
        with patch.object(market, "urlopen", return_value=response([])):
            with self.assertRaisesRegex(ValueError, "no candles for BTCUSDT"):
                market.Binance().candles("BTCUSDT", "1h")
        error = HTTPError(market.API, 429, "Too Many Requests", None, None)
        self.addCleanup(error.close)
        with patch.object(market, "urlopen", side_effect=error):
            with self.assertRaises(HTTPError) as caught:
                market.Binance().candles("BTCUSDT", "1h")
        self.assertEqual(caught.exception.code, 429)

    def test_klines_use_the_selected_candle_count(self):
        rows = [[1700000000000, "100.1", "104.9", "99.8", "103.2", "2.5", 1700003599999, "255.6"]]
        with patch.object(market, "urlopen", return_value=response(rows)) as fetch:
            market.Binance().candles("BTCUSDT", "4h", 120)
        query = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query)
        self.assertEqual(query, {"symbol": ["BTCUSDT"], "interval": ["4h"], "limit": ["120"]})


class TranslationTest(unittest.TestCase):
    def test_german_catalog_and_english_fallback(self):
        locale_dir = Path(market.__file__).with_name("locale")
        german = translation("CryptoMarket", locale_dir, languages=["de_DE"])
        english = translation("CryptoMarket", locale_dir, languages=["en_US"], fallback=True)
        self.assertEqual(german.gettext("Crypto Market"), "Krypto-Kurse")
        self.assertEqual(german.gettext("Number of candles"), "Anzahl der Kerzen")
        self.assertEqual(german.gettext("Candles · {time}").format(time="12:00"), "Kerzen · 12:00")
        self.assertEqual(english.gettext("Crypto Market"), "Crypto Market")


class CoinGeckoTest(unittest.TestCase):
    def test_public_market_cap_request_and_symbol_ranks(self):
        rows = [
            {"symbol": "btc"}, {"symbol": "eth"}, {"symbol": "ETH"}, {"symbol": "bnb"},
        ]
        with patch.object(market, "urlopen", return_value=response(rows)) as fetch:
            self.assertEqual(market.coingecko_ranks(), {"BTC": 1, "ETH": 2, "BNB": 4})
        fetch.assert_called_once()
        request = urlsplit(fetch.call_args.args[0].full_url)
        self.assertEqual((request.netloc, request.path), ("api.coingecko.com", "/api/v3/coins/markets"))
        self.assertEqual(parse_qs(request.query), {
            "vs_currency": ["usd"], "order": ["market_cap_desc"], "per_page": ["200"],
            "page": ["1"], "sparkline": ["false"],
        })
        self.assertFalse(any("key" in name.lower() for name, _ in fetch.call_args.args[0].header_items()))


class PreferencesTest(unittest.TestCase):
    def test_write_preferences_preserves_order_and_chart_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "watchlist.json"
            watchlist = ("ETHUSDC", "BTCUSDT")
            settings = preferences.ChartSettings("4h", 120)
            preferences.write_preferences(path, watchlist, settings)
            self.assertEqual(preferences.load_preferences(path), (watchlist, settings))

    def test_failed_replace_preserves_previous_file_and_removes_temporary_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            path.write_bytes(b'{"pairs": []}\n')
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    preferences.write_preferences(path, ("BTCUSDT",), preferences.ChartSettings())
            self.assertEqual(path.read_bytes(), b'{"pairs": []}\n')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_file_uses_the_default_watchlist_without_creating_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            self.assertEqual(preferences.load_preferences(path), (market.DEFAULT_WATCHLIST, preferences.ChartSettings("1h", 72)))
            self.assertFalse(path.exists())

    def test_saved_file_takes_precedence_even_with_no_pairs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            for pairs in (["ETHUSDC"], []):
                with self.subTest(pairs=pairs):
                    path.write_text(json.dumps({"pairs": pairs, "interval": "15m", "candle_count": 120}), encoding="utf-8")
                    self.assertEqual(preferences.load_preferences(path), (tuple(pairs), preferences.ChartSettings("15m", 120)))

    def test_global_interval_is_retained_even_for_an_empty_watchlist(self):
        self.assertEqual(preferences.parse_preferences({"pairs": [], "interval": "1d"}), ((), preferences.ChartSettings("1d", 72)))
        self.assertEqual(
            preferences.parse_preferences({"pairs": ["ETHUSDT", "ETHUSDT", "BTCUSDT"], "interval": "15m"}),
            (("ETHUSDT", "BTCUSDT"), preferences.ChartSettings("15m", 72)),
        )

    def test_missing_or_invalid_pairs_uses_defaults(self):
        for values in (None, {"interval": "4h"}, {"pairs": "BTCUSDT"}):
            with self.subTest(values=values):
                self.assertEqual(preferences.parse_preferences(values), (market.DEFAULT_WATCHLIST, preferences.ChartSettings()))

    def test_invalid_candle_count_preserves_the_rest_of_the_settings(self):
        for value in (True, 11, 241, "120"):
            with self.subTest(value=value):
                self.assertEqual(
                    preferences.parse_preferences({"pairs": [], "interval": "4h", "candle_count": value}),
                    ((), preferences.ChartSettings("4h", 72)),
                )


if __name__ == "__main__":
    unittest.main()
