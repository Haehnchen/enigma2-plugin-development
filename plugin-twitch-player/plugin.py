from collections import namedtuple
from functools import partial
from pathlib import Path

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Sources.List import List
from enigma import eTimer
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen

from .channel_grid import ChannelCards
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
        self.cards = None
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

        self.cards = ChannelCards(grid)

        self.library.listeners.append(self.render)
        if self.owner:
            self.library.start(self.cards.preview_size, self.cards.avatar_size[0])

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
        if not self.execing or not self.cards:
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

        wanted = {"preview": {}, "avatar": {}, "emoji": {}}
        self["channels"].list = [self.draw_card(i, wanted) for i in range(len(self.page_rows))]
        if self.page_rows:
            self["channels"].index = index % PAGE
        self.library.previews.request({
            key: [partial(self.image_loaded, i) for i in rows]
            for batch in wanted.values() for key, rows in batch.items()
        })

    def draw_card(self, index, wanted=None):
        row = self.page_rows[index]
        name = row.display or row.login

        def image(kind, url, size, revision=0):
            key = (url, size, revision)
            if wanted is not None and url:
                wanted[kind].setdefault(key, set()).add(index)
            return self.library.previews.cached(key)

        card = Card(name, row.title, row.game, row.live_stats(), row.login[:2].upper(), name[:1].upper(),
                    image("preview", row.image, self.cards.preview_size, self.library.revision),
                    image("avatar", row.avatar, self.cards.avatar_size))
        return self.cards.draw(card, lambda url, size: image("emoji", url, (size, size)))

    def image_loaded(self, index, pixmap):
        if pixmap is not None:
            self["channels"].updateEntry(index, self.draw_card(index))

    def update_badges(self):
        for i, row in enumerate(self.page_rows):
            text = row.live_stats()
            grid = self["channels"]
            if grid.list[i][0].stats != text:
                grid.updateEntry(i, self.draw_card(i))

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
                self.session.openWithCallback(partial(self.close, True), Player, self.library, row)
            else:
                self.close(row)


def main(session, onClose=None, **kwargs):
    def browser_closed(left_player=False):
        if left_player and onClose:
            onClose()

    session.openWithCallback(browser_closed, Browser)


def Plugins(**kwargs):
    return PluginDescriptor(name="Twitch Player", description=_("Twitch channels, live streams and chat"),
                            icon="plugin.png", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main,
                            closeMode=PluginDescriptor.DO_CLOSE_RECURSIVE)
