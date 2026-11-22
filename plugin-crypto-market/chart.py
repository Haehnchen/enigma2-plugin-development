from enigma import RT_HALIGN_CENTER, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, eCanvas, eRect, gRGB
from Components.GUIComponent import GUIComponent
from skin import parseFont

from .chart_layout import BACKGROUND, MUTED, draw_chart
from .i18n import _


ALIGNMENT = {"left": RT_HALIGN_LEFT, "center": RT_HALIGN_CENTER, "right": RT_HALIGN_RIGHT}


class CandleChart(GUIComponent):
    GUI_WIDGET = eCanvas

    def applySkin(self, desktop, parent):
        applied = super().applySkin(desktop, parent)
        self.axis_font = parseFont("Regular;15", parent.scale)
        self.status_font = parseFont("Regular;22", parent.scale)
        self.instance.setSize(self.instance.size())
        return applied

    def draw(self, candles, interval, message=""):
        if self.instance is None:
            return

        size = self.instance.size()
        self.instance.clear(gRGB(BACKGROUND))
        draw_chart(
            size.width(), size.height(), candles, interval, self._fill, self._text, message, _("Volume"),
        )

    def _fill(self, x, y, width, height, color):
        self.instance.fillRect(eRect(x, y, width, height), gRGB(color))

    def _text(self, x, y, width, height, text, alignment="left", *, emphasis=False):
        self.instance.writeText(
            eRect(x, y, width, height),
            gRGB(MUTED),
            gRGB(BACKGROUND),
            self.status_font if emphasis else self.axis_font,
            text,
            ALIGNMENT[alignment] | RT_VALIGN_CENTER,
        )
