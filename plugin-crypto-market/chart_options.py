from pathlib import Path

from Components.config import ConfigSelection, ConfigSelectionNumber, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen

from .i18n import _
from .market import INTERVAL_LABELS
from .preferences import MAX_CANDLE_COUNT, MIN_CANDLE_COUNT, ChartSettings


class ChartOptions(Screen, ConfigListScreen):
    skin = (Path(__file__).with_name("skins") / "chart-options.xml").read_text(encoding="utf-8")

    def __init__(self, session, settings, persistent=False):
        Screen.__init__(self, session, enableHelp=True)
        self.skinName = "CryptoMarketChartOptions"
        title = _("Plugin settings") if persistent else _("Chart settings")
        self.setTitle(title)
        self["heading"] = StaticText(title)
        self["description"] = StaticText(
            _("Used the next time you open Crypto Market.") if persistent else _("Applies while this view is open.")
        )
        self["key_red"] = StaticText(_("Cancel"))
        self["key_green"] = StaticText(_("Save") if persistent else _("Apply"))

        self.interval = ConfigSelection(choices=list(INTERVAL_LABELS.items()), default=settings.interval)
        self.candle_count = ConfigSelectionNumber(
            MIN_CANDLE_COUNT,
            MAX_CANDLE_COUNT,
            stepwidth=1,
            default=settings.candle_count,
        )

        ConfigListScreen.__init__(self, [
            getConfigListEntry(_("Candle interval"), self.interval),
            getConfigListEntry(_("Number of candles"), self.candle_count),
        ], session=session, fullUI=True)

    def keySave(self):
        # The caller decides whether to persist the settings.
        self.close(ChartSettings(self.interval.value, int(self.candle_count.value)))
