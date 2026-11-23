from Components.GUIComponent import GUIComponent
from Components.Pixmap import Pixmap
from enigma import RT_HALIGN_LEFT, RT_VALIGN_CENTER, eCanvas, eLabel, ePoint, eRect, eSize, eTimer, gRGB

from .chat_layout import emoji_spans
from .library import Previews


class LcdTitle(GUIComponent):
    """Scroll a pre-rendered line so the LCD never reshapes text on animation frames."""

    GUI_WIDGET = eLabel

    def __init__(self):
        super().__init__()
        self.text = ""
        self.active = False
        self.canvas = None
        self.icons = []
        self.images = Previews()
        self.timer = eTimer()
        self.timer.callback.append(self.scroll)

    def setText(self, text):
        if self.text != text:
            self.text = text
            if self.active:
                self.draw()

    def onShow(self):
        self.active = True
        self.draw()

    def onHide(self):
        self.active = False
        self.timer.stop()
        self.images.request({})

    def draw(self):
        if self.instance is None:
            return
        self.timer.stop()
        self.images.request({})
        self.icons.clear()
        self.canvas = eCanvas(self.instance)
        size = self.instance.size()
        font = self.instance.getFont()
        color = self.instance.getForegroundColor(1)  # LCD window style.
        height = size.height()
        icon = max(1, min(height, round(font.pointSize * 0.75)))
        bounds = eSize(32767, height)
        marker = eLabel.calculateTextSize(font, "|", bounds, True).width()
        runs = []
        x = 0
        for span in emoji_spans(self.text.replace("\r", " ").replace("\n", " ")):
            width = icon if span.image else max(0, eLabel.calculateTextSize(
                font, span.text + "|", bounds, True).width() - marker)
            runs.append((x, width, span))
            x += width
        self.line_width = x
        strip = eSize(max(1, x), height)
        self.canvas.resize(strip)
        self.canvas.setSize(strip)
        background = gRGB(0)  # The summary has a black background.
        self.canvas.clear(background)
        for x, width, span in runs:
            if span.image:
                image = Pixmap()
                image.GUIcreate(self.canvas)
                image.instance.setAlphatest(2)
                image.resize(icon, icon)
                image.move(x, (height - icon) // 2)
                self.icons.append((image, span.image, 0))
            else:
                self.canvas.writeText(eRect(x, 0, width, height), color, background, font,
                                      span.text, RT_HALIGN_LEFT | RT_VALIGN_CENTER)
        self.images.show(self.icons)
        self.canvas.show()
        self.offset = 0
        if self.line_width > size.width():
            self.timer.start(2000, True)

    def scroll(self):
        self.offset -= 2
        if self.offset <= -self.line_width:
            # Re-enter from the right and pause again when the title reaches the left edge.
            self.offset = self.instance.size().width()
        elif self.offset == -1:
            self.offset = 0
        self.canvas.move(ePoint(self.offset, 0))
        self.timer.start(2000 if self.offset == 0 else 40, True)

    def preWidgetRemove(self, instance):
        self.onHide()
        self.icons.clear()
        self.canvas = None

    def destroy(self):
        self.timer.callback.remove(self.scroll)
        super().destroy()
