from functools import lru_cache

from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryPixmapAlphaBlend, MultiContentEntryText
from enigma import RT_HALIGN_LEFT, RT_VALIGN_CENTER, eLabel, eListboxPythonMultiContent, eSize, eTimer
from skin import parseFont

from .chat_layout import ChatBuffer, Span, title_line, visible_lines
from .library import Previews


class InlineTextView(MenuList):
    """Draw text and inline emoji using the active skin's font and scaled widget size."""

    def __init__(self, cache_size):
        super().__init__([], content=eListboxPythonMultiContent)
        self.images = Previews(capacity=cache_size)

    def applySkin(self, desktop, parent):
        font = parseFont(dict(self.skinAttributes).get("font", "Regular;14"), parent.scale)
        applied = super().applySkin(desktop, parent)
        self.font_size = font.pointSize
        self.l.setFont(0, font)
        height = self.instance.size().height()
        self.l.setItemHeight(height)
        self.enableAutoNavigation(False)

        bounds = eSize(32767, height)

        def text_width(text):
            return max(1, eLabel.calculateTextSize(font, text, bounds, True).width())

        # Measure an interior space to avoid relying on trailing whitespace in the label bounds.
        space = max(1, text_width("n n") - text_width("nn"))

        @lru_cache(maxsize=512)
        def measure(text):
            return space if text == " " else text_width(text)

        self.measure = measure
        return applied

    def paint_lines(self, lines, y, line_height, image_ready):
        icon = self.icon_size
        entry = [True]
        wanted = {}
        for line in lines:
            for x, width, span in line:
                text = span.text
                if span.image:
                    key = (span.image, (icon, icon), 0)
                    wanted[key] = (image_ready,)
                    pixmap = self.images.cache.get(key)
                    if pixmap is not None:
                        entry.append(MultiContentEntryPixmapAlphaBlend(
                            pos=(x, y + (line_height - icon) // 2), size=(icon, icon), png=pixmap,
                        ))
                        continue
                    if key not in self.images.cache or self.measure(text) <= 1:
                        text = "…"
                entry.append(MultiContentEntryText(
                    pos=(x, y), size=(width, line_height), flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=text, color=span.color,
                ))
            y += line_height
        self.setList([entry])
        self.images.request(wanted)

    def destroy(self):
        self.images.request({})
        self.images.cache.clear()
        super().destroy()


class StreamTitle(InlineTextView):
    def __init__(self):
        super().__init__(cache_size=24)
        self.text = ""
        self.onVisibilityChange.append(self.render)

    def applySkin(self, desktop, parent):
        applied = super().applySkin(desktop, parent)
        # Match the capital letters, which occupy about three quarters of the font's em.
        self.icon_size = max(1, min(self.instance.size().height(), round(self.font_size * 0.75)))
        self.render()
        return applied

    def setText(self, text):
        text = text.lstrip()
        if self.text != text:
            self.text = text
            self.render()

    def render(self, _=None):
        if not self.instance or not self.visible:
            self.images.request({})
            return
        size = self.instance.size()
        line = title_line(self.text, size.width(), self.measure, self.icon_size)
        self.paint_lines([line], 0, size.height(), self.render)


class ChatView(InlineTextView):
    def __init__(self):
        super().__init__(cache_size=96)
        self.messages = ChatBuffer(maxlen=0)
        self.channel = ""
        self.timer = eTimer()
        self.timer.callback.append(self.render)
        self.paused = True
        self.status = ""

    def applySkin(self, desktop, parent):
        applied = super().applySkin(desktop, parent)
        height = self.instance.size().height()
        self.line_height = self.font_size + max(2, self.font_size // 3)
        self.icon_size = min(height, self.line_height - 2)
        self.messages = ChatBuffer(self.messages, maxlen=height // self.line_height)
        return applied

    def append(self, message):
        self.messages.append(message)
        self.schedule()

    def set_status(self, text):
        self.status = text
        self.schedule()

    def schedule(self, pixmap=None):
        if not self.paused and not self.timer.isActive():
            self.timer.start(500, True)

    def render(self):
        """Draw the visible chat lines as one bottom-aligned list entry."""
        size = self.instance.size()
        height = size.height()
        icon = self.icon_size
        failed = {url for (url, _, _), pixmap in self.images.cache.items() if pixmap is None}
        messages = self.messages.newest_first(self.channel) if self.messages else [(Span(self.status),)]
        lines = visible_lines(messages, self.messages.maxlen, size.width(), self.measure, icon, failed)

        self.paint_lines(lines, height - len(lines) * self.line_height, self.line_height, self.schedule)

    def resume(self):
        self.paused = False
        self.schedule()

    def pause(self):
        self.paused = True
        self.timer.stop()
        self.images.request({})

    def clear(self):
        self.pause()
        self.messages.clear()
        self.setList([])
        for key in list(self.images.cache):
            if self.images.cache[key] is None:
                del self.images.cache[key]

    def destroy(self):
        self.pause()
        self.timer.callback.remove(self.render)
        super().destroy()
