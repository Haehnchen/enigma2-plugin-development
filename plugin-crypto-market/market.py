from dataclasses import dataclass
from decimal import Decimal
from json import dumps, load
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://data-api.binance.vision/api/v3/"
COINGECKO_API = "https://api.coingecko.com/api/v3/"
MARKET_LIMIT = 200
DEFAULT_CANDLE_COUNT = 72
TICKER_BATCH_SIZE = 100
INTERVAL_LABELS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1D"}
INTERVALS = tuple(INTERVAL_LABELS)
DEFAULT_INTERVAL = "1h"
DEFAULT_WATCHLIST = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LINKUSDT",
)


@dataclass(frozen=True)
class Pair:
    symbol: str
    base: str
    quote: str

    @property
    def label(self):
        return f"{self.base}/{self.quote}"


@dataclass(frozen=True)
class Ticker:
    price: Decimal
    change: Decimal
    quote_volume: Decimal


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    quote_volume: Decimal


def format_price(price, decimals=None):
    if price is None:
        return "—"

    if decimals is None:
        exponent = price.adjusted()
        if price and exponent < -8:
            return f"{price:.2E}"
        decimals = 2 if exponent >= 1 else max(2, 3 - exponent)
    return f"{price:,.{decimals}f}"


def format_volume(volume):
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if volume >= divisor:
            return f"{volume / divisor:,.2f} {suffix}"
    return f"{volume:,.0f}"


def rank_pairs(pairs, tickers, coin_ranks):
    usdc = tickers.get("USDCUSDT")
    # Assume USDC/USDT parity when no positive conversion price is available.
    rates = {"USDT": Decimal(1), "USDC": usdc.price if usdc and usdc.price > 0 else Decimal(1)}
    by_volume = [
        (pair, tickers[symbol].quote_volume * rates[pair.quote], coin_ranks[pair.base.upper()])
        for symbol, pair in pairs.items()
        if pair.quote in rates and symbol in tickers and tickers[symbol].quote_volume > 0
        and pair.base.upper() in coin_ranks
    ]

    by_volume.sort(key=lambda entry: (-entry[1], entry[0].symbol))
    volume_scale = max(len(by_volume) - 1, 1)
    scored = []
    volume_rank = 0
    previous_volume = None
    for index, (pair, volume, coin_rank) in enumerate(by_volume):
        # Pairs with equal volume share the same rank.
        if volume != previous_volume:
            volume_rank = index
            previous_volume = volume

        coin_score = Decimal(MARKET_LIMIT - coin_rank) / (MARKET_LIMIT - 1)
        volume_score = Decimal(volume_scale - volume_rank) / volume_scale
        scored.append((pair, volume, Decimal("0.60") * coin_score + Decimal("0.40") * volume_score))

    scored.sort(key=lambda entry: (-entry[2], -entry[1], entry[0].symbol))
    return [(pair, volume) for pair, volume, _score in scored[:MARKET_LIMIT]]


def coingecko_ranks():
    rows = request_json(
        COINGECKO_API + "coins/markets",
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": MARKET_LIMIT,
            "page": 1,
            "sparkline": "false",
        },
    )

    ranks = {}
    for rank, row in enumerate(rows[:MARKET_LIMIT], start=1):
        # Keep the highest-ranked coin when symbols collide.
        ranks.setdefault(row["symbol"].upper(), rank)
    if not ranks:
        raise ValueError("CoinGecko returned no ranked coins")
    return ranks


def parse_tickers(rows, symbols):
    return {
        row["symbol"]: Ticker(Decimal(row["lastPrice"]), Decimal(row["priceChangePercent"]), Decimal(row["quoteVolume"]))
        for row in rows
        if row["symbol"] in symbols
    }


def request_json(url, params):
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Enigma2-CryptoMarket/1.0"})
    with urlopen(request, timeout=8) as response:
        return load(response)


class Binance:
    def __init__(self):
        self.pairs = {}
        self.catalog_expires = 0

    def market_snapshot(self):
        if monotonic() >= self.catalog_expires:
            symbols = self._get(
                "exchangeInfo",
                permissions="SPOT",
                showPermissionSets="false",
                symbolStatus="TRADING",
            )["symbols"]
            self.pairs = {
                entry["symbol"]: Pair(entry["symbol"], entry["baseAsset"], entry["quoteAsset"])
                for entry in symbols
                if entry["status"] == "TRADING" and entry["isSpotTradingAllowed"]
            }
            if not self.pairs:
                raise ValueError("Binance returned an empty spot catalogue")
            self.catalog_expires = monotonic() + 3600

        tickers = parse_tickers(self._get("ticker/24hr"), self.pairs)
        if not tickers:
            raise ValueError("Binance returned no spot tickers")
        return self.pairs, tickers

    def watchlist_quotes(self, symbols):
        rows = []
        try:
            for start in range(0, len(symbols), TICKER_BATCH_SIZE):
                batch = symbols[start:start + TICKER_BATCH_SIZE]
                rows.extend(self._get("ticker/24hr", symbols=dumps(batch, separators=(",", ":"))))
        except HTTPError as error:
            if error.code != 400:
                raise

            with error:
                try:
                    code = load(error).get("code")
                except (ValueError, AttributeError):
                    raise error

            if code != -1121:
                raise

            # A delisted symbol can reject the entire batch.
            rows = self._get("ticker/24hr")

        return parse_tickers(rows, set(symbols))

    def candles(self, symbol, interval, candle_count=DEFAULT_CANDLE_COUNT):
        # Field 7 is quote volume. Field 5 is base volume.
        candles = [
            Candle(
                timestamp=row[0],
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                quote_volume=Decimal(row[7]),
            )
            for row in self._get("klines", symbol=symbol, interval=interval, limit=candle_count)
        ]
        if not candles:
            raise ValueError(f"Binance returned no candles for {symbol}")
        return candles

    @staticmethod
    def _get(endpoint, **params):
        return request_json(API + endpoint, params)
