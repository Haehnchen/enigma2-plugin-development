from datetime import datetime
from os import getenv
from pathlib import Path
from time import monotonic

from enigma import (
    RT_HALIGN_CENTER,
    RT_HALIGN_LEFT,
    RT_HALIGN_RIGHT,
    RT_VALIGN_CENTER,
    eListboxPythonMultiContent,
    eTimer,
    gFont,
)
from twisted.internet.defer import DeferredList
from twisted.internet.threads import deferToThread

from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText
from Components.Sources.StaticText import StaticText
from Plugins.Plugin import PluginDescriptor
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Tools.Directories import SCOPE_CONFIG, resolveFilename
from skin import parseFont

from .chart import CandleChart
from .chart_layout import DOWN, MUTED, UP
from .chart_options import ChartOptions
from .i18n import _
from .market import INTERVAL_LABELS, Binance, Pair, coingecko_ranks, format_price, format_volume, rank_pairs
from .preferences import ChartSettings, load_preferences, write_preferences


WHITE = 0xECF3FF
QUOTE_REFRESH_MS = 60_000
CHART_REFRESH_MS = 30_000
CATALOG_REFRESH_SECONDS = 60

WATCH_COLUMNS = (
    (8, 139, RT_HALIGN_LEFT),
    (146, 106, RT_HALIGN_RIGHT),
    (254, 94, RT_HALIGN_RIGHT),
)
PICKER_COLUMNS = (
    (12, 52, RT_HALIGN_LEFT),
    (82, 274, RT_HALIGN_LEFT),
    (368, 176, RT_HALIGN_RIGHT),
    (590, 92, RT_HALIGN_CENTER),
)


class MarketList(MenuList):
    def __init__(self, columns):
        super().__init__([], content=eListboxPythonMultiContent)
        self.columns = columns
        self.scale = ((1, 1), (1, 1))
        self.row_height = 39

        self.l.setItemHeight(self.row_height)
        self.l.setFont(0, gFont("Regular", 19))
        self.l.setBuildFunc(self.build_row)

    def applySkin(self, desktop, parent):
        self.scale = parent.scale
        self.row_height = round(39 * self.scale[1][0] / self.scale[1][1])
        self.l.setItemHeight(self.row_height)
        self.l.setFont(0, parseFont("Regular;19", self.scale))
        return super().applySkin(desktop, parent)

    def build_row(self, symbol, cells):
        numerator, denominator = self.scale[0]
        # MultiContent reserves the first slot for row data.
        return [None] + [
            MultiContentEntryText(
                pos=(round(x * numerator / denominator), 0),
                size=(round(width * numerator / denominator), self.row_height),
                flags=alignment | RT_VALIGN_CENTER,
                text=text,
                color=color,
                color_sel=color,
            )
            for (x, width, alignment), (text, color) in zip(self.columns, cells)
        ]

    def navigation(self):
        return ActionMap(["NavigationActions"], {
            "up": self.goLineUp,
            "down": self.goLineDown,
            "pageUp": self.goPageUp,
            "pageDown": self.goPageDown,
            "top": self.goTop,
            "bottom": self.goBottom,
        }, -1)


class PairPicker(Screen):
    skin = (Path(__file__).with_name("skins") / "pair-picker.xml").read_text(encoding="utf-8")

    def __init__(self, session, ranked, watchlist):
        super().__init__(session)
        self.skinName = "CryptoMarketPairPicker"
        title = _("Add trading pair")
        self.setTitle(title)
        self.watched = set(watchlist)

        self["heading"] = StaticText(title)
        self["summary"] = StaticText("")
        self["pair_column"] = StaticText(_("Pair"))
        self["volume_column"] = StaticText(_("Volume · USDT equivalent"))
        self["watchlist_column"] = StaticText(_("Watchlist"))
        self["key_green"] = StaticText(_("Add"))
        self["notice"] = StaticText("")
        self["pairs"] = MarketList(PICKER_COLUMNS)
        self.set_catalog(ranked)

        self["navigation"] = self["pairs"].navigation()
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": self.choose,
            "green": self.choose,
            "cancel": self.close,
        }, -1)
        self["pairs"].onSelectionChanged.append(lambda: self["notice"].setText(""))
        self.onLayoutFinish.append(self.layout_finished)

    def layout_finished(self):
        self["pairs"].enableAutoNavigation(False)

    def set_catalog(self, ranked, unavailable=False):
        widget = self["pairs"]
        if not ranked:
            widget.setList([])
            self["summary"].setText(
                _("The market list is unavailable.") if unavailable else _("Loading market list …")
            )
            return

        self["summary"].setText(
            _("CoinGecko 60% · Binance 24h volume 40% · {count} pairs").format(count=len(ranked))
        )

        selected = widget.getCurrent()
        index = next(
            (index for index, (pair, _) in enumerate(ranked) if selected and pair.symbol == selected[0]),
            0,
        )
        widget.setList([
            (pair.symbol, (
                (str(row), MUTED),
                (pair.label, WHITE),
                (format_volume(volume), WHITE),
                ("X" if pair.symbol in self.watched else "", MUTED),
            ))
            for row, (pair, volume) in enumerate(ranked, start=1)
        ])
        widget.setCurrentIndex(index)

    def choose(self):
        row = self["pairs"].getCurrent()
        if row is not None:
            if row[0] in self.watched:
                self["notice"].setText(_("This pair is already in your watchlist."))
            else:
                self.close(row[0])


class CryptoMarket(Screen):
    skin = (Path(__file__).with_name("skins") / "dashboard.xml").read_text(encoding="utf-8")

    def __init__(self, session):
        super().__init__(session)
        self.setTitle(_("Crypto Market"))
        self.settings = Path(
            getenv("ENIGMA2_CRYPTO_SETTINGS") or resolveFilename(SCOPE_CONFIG, "crypto-market.json")
        )
        self.watchlist, self.default_chart = load_preferences(self.settings)
        self.chart_settings = self.default_chart

        self.binance = Binance()
        self.pairs = {}
        self.tickers = {}
        self.ranked = []

        self.selected = None
        self.chart_cache = {}
        self.chart_failures = set()
        self.chart_pending = None
        self.quotes_pending = False
        self.catalog_pending = False
        self.catalog_updated = None
        self.pair_picker = None
        self.replacing_list = False
        self.closed = False

        self["watchlist"] = MarketList(WATCH_COLUMNS)
        self["chart"] = CandleChart()
        self["pair_column"] = StaticText(_("Pair"))
        self["price_column"] = StaticText(_("Price"))
        self["key_green"] = StaticText(_("Add pair"))
        self["key_yellow"] = StaticText(_("Chart settings"))
        self["key_blue"] = StaticText(_("Plugin settings"))
        self["key_menu"] = StaticText("")
        self["key_exit"] = StaticText(_("EXIT  Close"))
        for name in ("pair_title", "pair_price", "candle_summary", "chart_status", "key_red"):
            self[name] = StaticText("")

        self["navigation"] = self["watchlist"].navigation()
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "MenuActions"], {
            "ok": self.choose_chart_settings,
            "yellow": self.choose_chart_settings,
            "green": self.choose_pair,
            "blue": self.choose_plugin_settings,
            "red": self.confirm_remove,
            "menu": self.choose_move,
            "cancel": self.close,
        }, -1)
        self["watchlist"].onSelectionChanged.append(self.selection_changed)

        self.chart_timer = eTimer()
        self.chart_timer.callback.append(self.refresh_chart)
        self.quote_timer = eTimer()
        self.quote_timer.callback.append(self.refresh_quotes)
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.stop)

    def start(self):
        self["watchlist"].enableAutoNavigation(False)
        self.replace_rows()

        if self.watchlist:
            self.set_quote_status(_("loading …"))
            self.refresh_quotes()
        else:
            self.refresh_catalog()

        self.quote_timer.start(QUOTE_REFRESH_MS)

    def stop(self):
        self.closed = True
        self.chart_timer.stop()
        self.quote_timer.stop()

    def pair(self, symbol):
        # Default pairs can be shown before the exchange catalog arrives.
        return self.pairs.get(symbol) or (
            Pair(symbol, symbol[:-4], "USDT") if symbol.endswith("USDT") else Pair(symbol, symbol, "")
        )

    def pair_label(self, symbol):
        pair = self.pair(symbol)
        return pair.label if pair.quote else symbol

    def watch_row(self, symbol):
        ticker = self.tickers.get(symbol)
        change = "—" if ticker is None else f"{ticker.change:+.2f}%"
        color = MUTED if not ticker or not ticker.change else (UP if ticker.change > 0 else DOWN)

        return (symbol, (
            (self.pair_label(symbol), WHITE),
            (format_price(ticker.price if ticker else None), WHITE),
            (change, color),
        ))

    def replace_rows(self, select=None):
        widget = self["watchlist"]
        previous_index = widget.getCurrentIndex()
        symbols = list(self.watchlist)

        # Replacing the list can fire a selection event before the index is restored.
        self.replacing_list = True
        widget.setList([self.watch_row(symbol) for symbol in symbols])
        if symbols:
            index = symbols.index(select) if select in symbols else max(0, min(previous_index, len(symbols) - 1))
            widget.setCurrentIndex(index)
        self.replacing_list = False
        self["key_menu"].setText(_("MENU  Reorder") if len(symbols) > 1 else "")
        self.selection_changed()

    def refresh_quotes(self):
        if not self.closed and self.watchlist and not self.quotes_pending:
            symbols = self.watchlist
            self.quotes_pending = True
            deferToThread(self.binance.watchlist_quotes, symbols).addCallbacks(
                lambda tickers: self.quotes_done(symbols, tickers),
                lambda error: self.quotes_done(symbols, None, error),
            )

    def set_quote_status(self, status=""):
        heading = _("Price ({status})").format(status=status) if status else _("Price")
        self["price_column"].setText(heading)

    def quotes_done(self, symbols, tickers, error=None):
        # A worker can finish after Enigma2 has cleared the screen attributes.
        if getattr(self, "closed", True):
            return

        self.quotes_pending = False
        if not self.watchlist:
            self.set_quote_status()
        elif error:
            print(f"[CryptoMarket] Binance quotes failed: {error.getErrorMessage()}")
            self.set_quote_status(_("last known") if self.tickers else _("unavailable"))
        else:
            self.tickers = {symbol: tickers[symbol] for symbol in self.watchlist if symbol in tickers}
            self.update_quote_rows()
            self.set_quote_status(datetime.now().strftime("%H:%M"))

        # Pairs added during the request need a new batch.
        if any(symbol not in symbols for symbol in self.watchlist):
            self.refresh_quotes()
        if self.catalog_updated is None:
            self.refresh_catalog()

    def refresh_catalog(self):
        if not self.closed and not self.catalog_pending:
            self.catalog_pending = True
            DeferredList([
                deferToThread(self.binance.market_snapshot),
                deferToThread(coingecko_ranks),
            ], consumeErrors=True).addCallback(self.catalog_done)

    def catalog_done(self, responses):
        if getattr(self, "closed", True):
            return

        self.catalog_pending = False
        (binance_ok, snapshot), (gecko_ok, ranks) = responses
        if binance_ok:
            self.pairs, tickers = snapshot
            self.update_quote_rows()
            if gecko_ok:
                self.ranked = rank_pairs(self.pairs, tickers, ranks)
                self.catalog_updated = monotonic()
        else:
            print(f"[CryptoMarket] Binance market list failed: {snapshot.getErrorMessage()}")

        if not gecko_ok:
            print(f"[CryptoMarket] CoinGecko market list failed: {ranks.getErrorMessage()}")
        if self.pair_picker is not None and self.session.current_dialog is self.pair_picker:
            self.pair_picker.set_catalog(self.ranked, unavailable=not self.ranked)

    def update_quote_rows(self):
        for index, symbol in enumerate(self.watchlist):
            self["watchlist"].updateEntry(index, self.watch_row(symbol))
        self.show_pair_heading()

    def selection_changed(self):
        if self.replacing_list:
            return

        row = self["watchlist"].getCurrent()
        symbol = row[0] if row else None
        key = (symbol, self.chart_settings) if symbol else None
        if key == self.selected:
            if key is None:
                self.show_chart()
            return

        self.selected = key
        self.show_chart()
        self.chart_timer.stop()
        if key:
            # Debounce rapid navigation before fetching candles.
            self.chart_timer.start(180, True)

    def show_pair_heading(self):
        if not self.selected:
            self["pair_title"].setText(_("No pairs selected"))
            self["pair_price"].setText("")
            self["key_red"].setText("")
            return

        symbol, settings = self.selected
        pair = self.pair(symbol)
        ticker = self.tickers.get(symbol)
        self["pair_title"].setText(f"{self.pair_label(symbol)}  ·  {INTERVAL_LABELS[settings.interval]}")
        self["pair_price"].setText(f"{format_price(ticker.price)} {pair.quote}" if ticker else "")
        self["key_red"].setText(_("Remove pair"))

    def refresh_chart(self):
        if not self.closed and self.selected and self.chart_pending is None:
            key = self.selected
            self.chart_pending = key
            symbol, settings = key
            deferToThread(self.binance.candles, symbol, settings.interval, settings.candle_count).addCallbacks(
                lambda candles: self.chart_done(key, candles),
                lambda error: self.chart_done(key, None, error),
            )

    def chart_done(self, key, candles, error=None):
        if getattr(self, "closed", True):
            return

        self.chart_pending = None
        if error:
            symbol, settings = key
            print(f"[CryptoMarket] Binance chart failed ({symbol} {settings.interval}): {error.getErrorMessage()}")
            self.chart_failures.add(key)
        else:
            self.chart_failures.discard(key)
            # Move the refreshed entry to the end before evicting the oldest.
            self.chart_cache.pop(key, None)
            self.chart_cache[key] = (candles, datetime.now())
            if len(self.chart_cache) > 40:
                self.chart_cache.pop(next(iter(self.chart_cache)))

        if key == self.selected:
            self.show_chart()
            self.chart_timer.start(CHART_REFRESH_MS, True)
        elif self.selected:
            # The selection changed while this request was in flight.
            self.chart_timer.start(1, True)

    def show_chart(self):
        self.show_pair_heading()
        if not self.selected:
            self["chart"].draw([], self.chart_settings.interval, _("Press Green to add a trading pair"))
            self["chart_status"].setText("")
            self["candle_summary"].setText("")
            return

        interval = self.selected[1].interval
        cached = self.chart_cache.get(self.selected)
        failed = self.selected in self.chart_failures

        if cached:
            candles, updated = cached
            last = candles[-1]
            self["candle_summary"].setText(
                f"O {format_price(last.open)}   H {format_price(last.high)}   "
                f"L {format_price(last.low)}   C {format_price(last.close)}"
            )
            label = _("Candles · {time} · last known") if failed else _("Candles · {time}")
            self["chart_status"].setText(label.format(time=updated.strftime("%H:%M:%S")))
            self["chart"].draw(candles, interval)
        else:
            self["candle_summary"].setText("")
            self["chart_status"].setText("")
            self["chart"].draw([], interval, _("Candles unavailable") if failed else _("Loading candles …"))

    def choose_chart_settings(self):
        self.session.openWithCallback(self.apply_chart_settings, ChartOptions, self.chart_settings)

    def apply_chart_settings(self, settings=None):
        if isinstance(settings, ChartSettings):
            self.chart_settings = settings
            self.selection_changed()

    def choose_plugin_settings(self):
        self.session.openWithCallback(
            self.apply_plugin_settings, ChartOptions, self.default_chart, persistent=True,
        )

    def apply_plugin_settings(self, settings=None):
        if isinstance(settings, ChartSettings):
            self.save_preferences(self.watchlist, settings)

    def choose_pair(self):
        self.pair_picker = self.session.openWithCallback(self.pair_chosen, PairPicker, self.ranked, self.watchlist)

        if self.catalog_updated is None or monotonic() - self.catalog_updated >= CATALOG_REFRESH_SECONDS:
            self.refresh_catalog()

    def pair_chosen(self, symbol=None):
        self.pair_picker = None
        if symbol and symbol not in self.watchlist:
            if self.save_preferences((*self.watchlist, symbol), self.default_chart):
                self.replace_rows(select=symbol)
                self.refresh_quotes()

    def choose_move(self):
        if not self.selected or len(self.watchlist) < 2:
            return

        symbol = self.selected[0]
        index = self.watchlist.index(symbol)
        choices = []
        if index > 0:
            choices.append((_("Move up"), -1))
        if index < len(self.watchlist) - 1:
            choices.append((_("Move down"), 1))

        self.session.openWithCallback(
            lambda choice=None: self.move_pair(symbol, choice),
            ChoiceBox,
            text=self.pair_label(symbol),
            windowTitle=_("Reorder watchlist"),
            choiceList=choices,
        )

    def move_pair(self, symbol, choice=None):
        if not choice or symbol not in self.watchlist:
            return

        watchlist = list(self.watchlist)
        index = watchlist.index(symbol)
        target = index + choice[1]
        if not 0 <= target < len(watchlist):
            return

        watchlist[index], watchlist[target] = watchlist[target], watchlist[index]
        if self.save_preferences(tuple(watchlist), self.default_chart):
            self.replace_rows(select=symbol)

    def confirm_remove(self):
        if self.selected:
            symbol = self.selected[0]
            self.session.openWithCallback(
                lambda confirmed: self.remove_pair(symbol, confirmed),
                MessageBox,
                _("Remove {pair} from the watchlist?").format(pair=self.pair_label(symbol)),
                type=MessageBox.TYPE_YESNO,
                default=False,
            )

    def remove_pair(self, symbol, confirmed):
        if confirmed and symbol in self.watchlist:
            watchlist = tuple(pair for pair in self.watchlist if pair != symbol)
            if self.save_preferences(watchlist, self.default_chart):
                self.tickers.pop(symbol, None)
                self.replace_rows()
                if not watchlist:
                    self.set_quote_status()

    def save_preferences(self, watchlist, settings):
        try:
            write_preferences(self.settings, watchlist, settings)
        except OSError as error:
            print(f"[CryptoMarket] Could not save settings: {error}")
            self.session.open(MessageBox, _("Crypto Market settings could not be saved."), type=MessageBox.TYPE_ERROR)
            return False

        self.watchlist = watchlist
        self.default_chart = settings
        return True


def main(session, **kwargs):
    session.open(CryptoMarket)


def Plugins(**kwargs):
    return PluginDescriptor(
        name=_("Crypto Market"),
        description=_("Cryptocurrency prices and candlestick charts"),
        icon="plugin.png",
        where=PluginDescriptor.WHERE_PLUGINMENU,
        fnc=main,
    )
