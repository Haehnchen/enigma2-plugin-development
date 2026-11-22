from functools import lru_cache

from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryPixmapAlphaBlend, MultiContentEntryText
from enigma import RT_HALIGN_LEFT, RT_VALIGN_CENTER, eLabel, eListboxPythonMultiContent, eSize, eTimer
from skin import parseFont

from .chat_layout import ChatBuffer, Span, visible_lines
from .library import Previews


class ChatView(MenuList):
    def __init__(self):
        super().__init__([], content=eListboxPythonMultiContent)
        self.messages = ChatBuffer(maxlen=0)
        self.channel = ""
        self.images = Previews(capacity=96)
        self.timer = eTimer()
        self.timer.callback.append(self.render)
        self.paused = True
        self.status = ""

    def applySkin(self, desktop, parent):
        font = parseFont(dict(self.skinAttributes).get("font", "Regular;14"), parent.scale)
        applied = super().applySkin(desktop, parent)
        self.l.setFont(0, font)
        self.line_height = font.pointSize + max(2, font.pointSize // 3)
        self.icon_size = self.line_height - 2
        height = self.instance.size().height()
        self.messages = ChatBuffer(self.messages, maxlen=height // self.line_height)
        self.l.setItemHeight(height)
        self.enableAutoNavigation(False)

        bounds = eSize(32767, self.line_height * 2)

        def text_width(text):
            return max(1, eLabel.calculateTextSize(font, text, bounds, True).width())

        # Measure an interior space to avoid relying on trailing whitespace in the label bounds.
        space = max(1, text_width("n n") - text_width("nn"))

        @lru_cache(maxsize=512)
        def measure(text):
            return space if text == " " else text_width(text)

        self.measure = measure
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

        y = height - len(lines) * self.line_height
        entry = [True]
        wanted = {}
        for line in lines:
            for x, width, span in line:
                key = (span.image, (icon, icon), 0)
                if span.image:
                    wanted[key] = (self.schedule,)
                    pixmap = self.images.cache.get(key)
                    if pixmap is not None:
                        entry.append(MultiContentEntryPixmapAlphaBlend(
                            pos=(x, y + 1), size=(icon, icon), png=pixmap,
                        ))
                        continue

                text = "…" if span.image and span.image not in failed else span.text
                entry.append(MultiContentEntryText(
                    pos=(x, y), size=(width, self.line_height), flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=text, color=span.color,
                ))
            y += self.line_height

        self.setList([entry])
        self.images.request(wanted)

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
        self.images.cache.clear()
        super().destroy()
