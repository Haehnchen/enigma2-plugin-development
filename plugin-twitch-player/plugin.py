from collections import namedtuple
from functools import partial
from pathlib import Path

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Sources.List import List
from enigma import eListboxPythonMultiContent, eTimer
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen

from .i18n import _
from .library import Library
from .player import Player
from .settings import Settings


COLUMNS = 4
PAGE = 8
Card = namedtuple("Card", "name title game stats placeholder initial preview avatar")


class Browser(Screen):
    skin_path = str(Path(__file__).parent)
    skin = (Path(__file__).with_name("skins") / "channels.xml").read_text(encoding="utf-8")

    def __init__(self, session, library=None, selected=None):
        super().__init__(session)
        self.skinName = "TwitchChannels"
        self.owner = library is None
        self.library = Library() if self.owner else library
        self.selected = selected
        self.live_names = []
        self.page_rows = []
        self.preview_size = None
        self.stats_timer = eTimer()
        self.stats_timer.callback.append(self.update_badges)

        self.setTitle(_("Twitch channels"))
        self["channels"] = List(indexNames={field: i for i, field in enumerate(Card._fields)})
        self["status"] = Label()
        self["empty"] = Label()
        self["key_ok"] = Label(_("Play"))
        self["key_green"] = Label(_("Refresh"))
        self["key_blue"] = Label(_("Settings"))
        self["key_exit"] = Label(_("Back"))

        self["actions"] = ActionMap(["DirectionActions", "OkCancelActions", "ColorActions", "MenuActions"], {
            "left": lambda: self.move_by(-1), "right": lambda: self.move_by(1),
            "up": lambda: self.move_by(-COLUMNS), "down": lambda: self.move_by(COLUMNS),
            "chplus": lambda: self.move_by(PAGE), "chminus": lambda: self.move_by(-PAGE),
            "ok": self.select, "cancel": self.close, "green": self.library.refresh,
            "blue": self.settings, "menu": self.settings,
        }, -1)
        self.onLayoutFinish.append(self.start)
        self.onShown.append(self.resume)
        self.onExecEnd.append(self.suspend)
        self.onClose.append(self.stop)

    def start(self):
        grid = self["channels"]
        grid.enableAutoNavigation(False)

        # Use the active skin's scaled pixmap rectangles for the Twitch requests.
        elements = grid.master.template["modes"][grid.mode][1]
        pixmaps = (eListboxPythonMultiContent.TYPE_PIXMAP, eListboxPythonMultiContent.TYPE_PIXMAP_ALPHATEST,
                   eListboxPythonMultiContent.TYPE_PIXMAP_ALPHABLEND)
        sizes = {entry[5]: entry[3:5] for entry in elements if entry[0] in pixmaps}
        self.preview_size = sizes[grid.indexNames["preview"]]
        self.avatar_size = sizes[grid.indexNames["avatar"]]

        self.library.listeners.append(self.render)
        if self.owner:
            self.library.start(self.preview_size, self.avatar_size[0])

    def resume(self):
        self.render()
        self.stats_timer.start(60000)

    def suspend(self):
        self.stats_timer.stop()
        self.library.previews.show([])

    def stop(self):
        self.library.listeners.remove(self.render)
        if self.owner:
            self.library.close()

    def render(self):
        """Keep the selected channel on screen when a refresh reorders the live list."""
        if not self.execing or not self.preview_size:
            return

        channels = self.library.channels
        self.live_names = [name for name, row in channels.items() if row.live]
        if self.selected not in self.live_names:
            self.selected = self.live_names[0] if self.live_names else None
        index = self.live_names.index(self.selected) if self.live_names else 0
        page = index // PAGE
        self.page_rows = [channels[name] for name in self.live_names[page * PAGE:(page + 1) * PAGE]]

        empty = ""
        if not channels and not self.library.pending:
            empty = _("Add a channel or connect Twitch in Settings.")
        elif not self.live_names and self.library.revision:
            empty = _("No channels are live right now.")
        self["empty"].setText(empty)

        online = len(self.live_names)
        pages = (online + PAGE - 1) // PAGE
        page_label = f"{page + 1}/{pages}" if pages else ""
        count = _("{online}/{total} online").format(
            online=online if self.library.revision or not channels else "–", total=len(channels))
        self["status"].setText("   ·   ".join(filter(None, (self.library.status, count, page_label))))

        loader = self.library.previews
        cards, previews, avatars = [], {}, {}
        for i, row in enumerate(self.page_rows):
            name = row.display or row.login
            preview = (row.image, self.preview_size, self.library.revision)
            avatar = (row.avatar, self.avatar_size, 0)
            cards.append(Card(
                name, row.title, row.game, row.live_stats(), row.login[:2].upper(), name[:1].upper(),
                loader.cached(preview), loader.cached(avatar),
            ))
            for field, key, batch in (("preview", preview, previews), ("avatar", avatar, avatars)):
                if key[0]:
                    batch.setdefault(key, []).append(partial(self.image_loaded, i, field))

        self["channels"].list = cards
        if cards:
            self["channels"].index = index % PAGE
        loader.request(previews | avatars)

    def image_loaded(self, index, field, pixmap):
        if pixmap is not None:
            grid = self["channels"]
            grid.updateEntry(index, grid.list[index]._replace(**{field: pixmap}))

    def update_badges(self):
        for i, row in enumerate(self.page_rows):
            text = row.live_stats()
            grid = self["channels"]
            card = grid.list[i]
            if card.stats != text:
                grid.updateEntry(i, card._replace(stats=text))

    def move_by(self, step):
        names = self.live_names
        if names:
            index = names.index(self.selected) if self.selected in names else 0
            target = max(0, min(len(names) - 1, index + step))
            self.selected = names[target]
            if target // PAGE == index // PAGE:
                self["channels"].index = target % PAGE
            else:
                self.render()

    def settings(self):
        self.session.open(Settings, self.library)

    def select(self):
        row = self.library.channels.get(self.selected)
        if row and row.live:
            if self.owner:
                self.session.open(Player, self.library, row)
            else:
                self.close(row)


def main(session, **kwargs):
    session.open(Browser)


def Plugins(**kwargs):
    return PluginDescriptor(name="Twitch Player", description=_("Twitch channels, live streams and chat"),
                            icon="plugin.png", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main)
