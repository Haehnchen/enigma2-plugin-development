from dataclasses import dataclass
from pathlib import Path
from time import time

from Components.ActionMap import ActionMap
from Components.Input import Input
from Components.Label import Label
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryRectangle, MultiContentEntryText
from enigma import RT_ELLIPSIS, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, eListboxPythonMultiContent, gFont
from Screens.ChoiceBox import ChoiceBox
from Screens.InputBox import InputBox
from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from twisted.internet.threads import deferToThread

from . import _
from .catalog import Catalog, JoynError, LIVE, clean, clock, is_search_prompt, runtime, search_query
from .player import Player


@dataclass
class Page:
    query: str
    title: str
    index: int = 0


class ChannelList(MenuList):
    def __init__(self):
        super().__init__([], content=eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", 24))
        self.l.setFont(1, gFont("Regular", 21))

    def show_items(self, items, live):
        height = 60 if live or any(item.video_metadata() for item in items if not item.folder) else 40
        self.l.setItemHeight(height)
        rows = []
        timestamp = int(time())
        flags = RT_HALIGN_LEFT | RT_VALIGN_CENTER | RT_ELLIPSIS
        for item in items:
            title = ("» " if item.folder else "") + item.title
            if live:
                title = item.channel or title
                current, _ = item.now_and_next(timestamp)
                subtitle = item.programme_title(current)
                timing = f"{clock(current.start)} – {clock(current.end)}" if current else ""
            else:
                subtitle = item.video_metadata() if not item.folder else ""
                timing = runtime(item.duration) if item.duration and not item.folder else ""

            title_height = 28 if subtitle else height - 4
            text_width = 618 if timing else 800
            parts = [
                item,
                MultiContentEntryRectangle(pos=(0, 1), size=(840, height - 3),
                                           backgroundColor="#172637", backgroundColorSelected="#294b6d"),
                MultiContentEntryText(pos=(14, 2), size=(text_width, title_height), font=0, flags=flags,
                                      text=title, color="#ffffff", color_sel="#ffffff"),
            ]
            if subtitle:
                parts.append(MultiContentEntryText(pos=(14, 30), size=(text_width, 25), font=1, flags=flags,
                                                   text=subtitle, color="#bfcfe3", color_sel="#e1edfd"))
            if timing:
                parts.append(MultiContentEntryText(pos=(641, 2), size=(180, height - 4), font=1,
                                                   flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=timing,
                                                   color="#bdd8f2", color_sel="#ffffff"))
            rows.append(parts)
        self.setList(rows)


class Browser(Screen):
    skin = (Path(__file__).parent / "skins/browser.xml").read_text(encoding="utf-8")

    def __init__(self, session):
        super().__init__(session)
        self.catalog = Catalog()
        self.page = Page(LIVE, _("Live TV"))
        self.history = []
        self.entries = []
        self.request_id = 0
        self.pending = False
        self.closed = False

        self["items"] = ChannelList()
        self["heading"] = Label()
        self["status"] = Label()
        self["details"] = Label()
        for color, title in (("red", _("Live TV")), ("green", _("Catalog")),
                             ("yellow", _("Search")), ("blue", _("Refresh"))):
            self["key_" + color] = Label(title)

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "red": lambda: self.switch(LIVE, _("Live TV")),
            "green": lambda: self.switch("", _("Catalog")),
            "yellow": self.search,
            "blue": self.refresh,
            "ok": self.select, "right": self.select,
            "up": self["items"].goLineUp, "down": self["items"].goLineDown,
            "cancel": self.back, "left": self.back,
        })
        self["items"].onSelectionChanged.append(self.show_details)
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.finish)

    def start(self):
        self["items"].enableAutoNavigation(False)
        self.load(self.page)

    def switch(self, query, title):
        self.history.clear()
        self.load(Page(query, title))

    def refresh(self):
        if not self.pending:
            self.page.index = self["items"].getCurrentIndex()
            self.load(self.page)

    def search(self):
        if self.pending:
            return

        def entered(text):
            query = search_query(text)
            if query and not getattr(self, "closed", True):
                self.open_page(query, _("Search"))

        self.session.openWithCallback(entered, VirtualKeyBoard, title=_("Search"), text="")

    def open_page(self, query, title):
        self.page.index = self["items"].getCurrentIndex()
        self.history.append(self.page)
        self.load(Page(query, title))

    def back(self):
        if self.history:
            self.load(self.history.pop())
        else:
            self.close()

    def load(self, page):
        self.page = page
        self.setTitle("Joyn")
        self["heading"].setText(page.title)
        self["status"].setText(_("Loading…"))
        self.entries = []
        self["items"].setList([])
        self.show_details()
        self.request(page.query, self.loaded)

    def request(self, query, done, responses=()):
        """Discard late replies when the user has already opened another page."""
        self.request_id += 1
        request_id = self.request_id
        self.pending = True

        def success(payload):
            if getattr(self, "closed", True) or request_id != self.request_id:
                return
            self.pending = False
            dialogs = payload.get("dialogs", [])
            if dialogs:
                self.show_dialog(query, done, responses, dialogs[0])
            else:
                done(payload)

        def failure(error):
            if getattr(self, "closed", True) or request_id != self.request_id:
                return
            self.pending = False
            value = error.value
            self["status"].setText(str(value) if isinstance(value, JoynError)
                                   else _("Joyn could not complete the request."))

        deferToThread(self.catalog.request, query, responses).addCallbacks(success, failure)

    def show_dialog(self, query, done, responses, dialog):
        title = clean(dialog.get("heading")) or _("Search")
        kind = dialog.get("kind")

        def answer(flag, value):
            if getattr(self, "closed", True):
                return
            if value is not None:
                self.request(query, done, (*responses, (flag, str(value))))
            else:
                self["status"].setText("")

        if kind == "select":
            choices = [(clean(text), index) for index, text in enumerate(dialog.get("options", []))]
            if choices:
                self.session.openWithCallback(lambda selection: answer("--select", selection[1] if selection else None),
                                              ChoiceBox, title=title, list=choices)
                return
        elif kind == "input":
            if dialog.get("hidden") or dialog.get("input_type") == 1:
                self.session.openWithCallback(lambda value: answer("--input", value), InputBox, title=title,
                                              text=dialog.get("default", ""), type=Input.PIN)
            else:
                self.session.openWithCallback(lambda value: answer("--input", value), VirtualKeyBoard,
                                              title=title, text=dialog.get("default", ""))
            return
        self["status"].setText(_("This Joyn dialog is unavailable."))

    def loaded(self, payload):
        updates = payload.get("container_updates", [])
        if updates and updates[0].partition("?")[2] != self.page.query.lstrip("?"):
            self.load(Page("?" + updates[0].partition("?")[2], self.page.title, self.page.index))
            return
        self.entries = self.catalog.items(payload, catalog_root=not self.page.query)
        self["items"].show_items(self.entries, self.page.query == LIVE)
        if self.entries:
            self["items"].setCurrentIndex(min(max(0, self.page.index), len(self.entries) - 1))
        self["status"].setText(self.notice(payload) or ("" if self.entries else _("No content available.")))
        self.show_details()

    def show_details(self):
        index = self["items"].getCurrentIndex()
        details = ""
        if 0 <= index < len(self.entries):
            item = self.entries[index]
            details = item.description
            if item.live and item.schedule:
                current, following = item.now_and_next(int(time()))
                lines = []
                if current and current.subtitle and current.subtitle != current.title:
                    lines.append(current.subtitle)
                if following and following.title:
                    lines.append(following.next_label())
                details = "\n".join(lines)
        self["details"].setText(details)

    def notice(self, payload):
        notifications = payload.get("notifications", [])
        return clean(notifications[-1].get("message")) if notifications else ""

    def select(self):
        index = self["items"].getCurrentIndex()
        if self.pending or not 0 <= index < len(self.entries):
            return
        item = self.entries[index]
        if is_search_prompt(item.query):
            self.search()
            return
        if item.folder:
            self.open_page(item.query, item.title)
            return
        self["status"].setText(_("Opening stream…"))
        self.request(item.query, lambda payload: self.selected(item, payload))

    def selected(self, item, payload):
        resolved = payload.get("resolved") or {}
        stream = resolved.get("stream") or {}
        if resolved.get("succeeded") and stream.get("url"):
            self["status"].setText("")
            self.session.open(Player, item, stream, self.catalog)
        elif payload.get("items") or payload.get("container_updates"):
            self.page.index = self["items"].getCurrentIndex()
            self.history.append(self.page)
            updates = payload.get("container_updates", [])
            page = Page(updates[0] if updates else item.query, item.title)
            if updates:
                self.load(page)
            else:
                self.page = page
                self["heading"].setText(page.title)
                self.loaded(payload)
        else:
            self["status"].setText(self.notice(payload) or _("Joyn did not return a playable stream."))

    def finish(self):
        self.closed = True
