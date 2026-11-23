from functools import lru_cache

from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
from enigma import RT_ELLIPSIS, eLabel, eListboxPythonMultiContent, eSize

from .chat_layout import EMOJI, title_line


class ChannelCards:
    """Use the grid skin's drawing entries, expanding a mixed title into text and pixmaps."""

    def __init__(self, grid):
        template = grid.master.template
        self.elements = template["modes"][grid.mode][1]
        self.pixmaps = (eListboxPythonMultiContent.TYPE_PIXMAP, eListboxPythonMultiContent.TYPE_PIXMAP_ALPHATEST,
                        eListboxPythonMultiContent.TYPE_PIXMAP_ALPHABLEND)
        sizes = {entry[5]: entry[3:5] for entry in self.elements if entry[0] in self.pixmaps}
        self.preview_size = sizes[grid.indexNames["preview"]]
        self.avatar_size = sizes[grid.indexNames["avatar"]]
        title = next((e for e in self.elements if e[0] == eListboxPythonMultiContent.TYPE_TEXT
                      and e[7] == grid.indexNames["title"]), None)
        self.title_entry = title
        if title is not None:
            font = template["fonts"][title[5]]
            self.icon_size = max(1, min(title[4], round(font.pointSize * 0.75)))
            bounds = eSize(32767, title[4])
            icon_size = self.icon_size

            def measure(value):
                return eLabel.calculateTextSize(font, value, bounds, True).width()

            @lru_cache(maxsize=128)
            def layout(text):
                return title_line(text, abs(title[3]), measure, icon_size, ellipsis=True)

            self.title_layout = layout
        # Each row now supplies its own drawing entries to the native grid.
        grid.master.content.setTemplate(None)

    def draw(self, card, emoji_image):
        entries = [card]
        for entry in self.elements:
            if entry is self.title_entry and EMOJI.search(card.title):
                entries.extend(self.title_entries(entry, card.title, emoji_image))
            elif entry[0] == eListboxPythonMultiContent.TYPE_TEXT and isinstance(entry[7], int):
                entries.append((*entry[:7], card[entry[7]], *entry[8:]))
            elif entry[0] in self.pixmaps and isinstance(entry[5], int):
                entries.append((*entry[:5], card[entry[5]], *entry[6:]))
            else:
                entries.append(entry)
        return entries

    def title_entries(self, entry, text, emoji_image):
        for offset, width, span in self.title_layout(text):
            x = entry[1] + offset
            if span.image:
                pixmap = emoji_image(span.image, self.icon_size)
                if pixmap is not None:
                    yield MultiContentEntryPixmapAlphaBlend(
                        pos=(x, entry[2] + (entry[4] - width) // 2), size=(width, width), png=pixmap)
                    continue
            # The full line already has its ellipsis. Let each run use its full width.
            yield (entry[0], x, entry[2], width, entry[4], entry[5], entry[6] & ~RT_ELLIPSIS,
                   "…" if span.image else span.text, *entry[8:])
