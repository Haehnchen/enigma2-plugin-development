"""Drive the Joyn screens with Enigma2 remote-control actions."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from time import time
from traceback import print_exc
from types import SimpleNamespace
from unittest.mock import patch

from enigma import eActionMap, eTimer, quitMainloop
from keyids import KEYIDS
from Components.PluginComponent import plugins
from Plugins.Plugin import PluginDescriptor
from Plugins.Extensions.JoynPlayer.browser import Browser
from Plugins.Extensions.JoynPlayer.catalog import Catalog, LIVE, clock
from Plugins.Extensions.JoynPlayer.player import Player
from Screens.PluginBrowser import PluginBrowser
from Screens.VirtualKeyBoard import VirtualKeyBoard


def listing(*rows):
    return {"items": [{"query": query, "is_folder": folder, "label": title}
                      for query, folder, title in rows]}


def media_server():
    plugin = Path("/usr/lib/enigma2/python/Plugins/Extensions/JoynPlayer")
    data = (plugin / ".build/gui-test.ts").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def fixture_request(media_url):
    now = int(time())
    live = listing(("?mode=play_live&stream_type=LIVE", False, "[B]Das Erste[/B] - Tagesschau"))
    live["items"][0]["epg"] = [
        {"start": now - 300, "end": now + 300, "title": "Tagesschau", "subtitle": "Nachrichten"},
        {"start": now + 300, "end": now + 2100, "title": "Wetter"},
    ]
    episodes = listing(("?mode=play_video", False, "Episode 1"),
                       ("?mode=add_fav", False, "Add to Watchlist"))
    episodes["items"][0].update(info={"tvshowtitle": "Series", "season": 1, "episode": 1, "duration": 2640},
                               plot="Beschreibung der Folge")

    stream = {"resolved": {"succeeded": True, "stream": {"url": media_url}}}
    pages = {
        LIVE: live,
        "": listing(("?mode=movies", True, "Movies"), ("?mode=series", True, "Series"), ("?mode=search", False, "Search")),
        "?mode=series": listing(("?mode=season", True, "Season 1")),
        "?mode=season": episodes,
        "?mode=play_video": stream,
        "?mode=play_live&stream_type=LIVE": stream,
        "?mode=search&search_term=Joko+%26+Klaas": listing(("?mode=series", True, "Search result")),
    }

    def request(catalog, query, responses=()):
        return pages[query]

    return request


class GuiTest:
    def __init__(self, session):
        self.session = session
        self.server = media_server()
        Catalog.request = fixture_request(f"http://127.0.0.1:{self.server.server_port}/episode.ts")
        self.steps = self.exercise()
        self.timer = eTimer()
        self.timer.callback.append(self.advance)
        self.timer.start(200, True)

    def advance(self):
        try:
            delay = next(self.steps)
        except StopIteration:
            print("[JoynTest] PASS: Live EPG, VOD timeline, queued seek, catalog, search and exit.", flush=True)
            quitMainloop(0)
        except Exception:
            print_exc()
            quitMainloop(1)
        else:
            self.timer.start(delay, True)

    def exercise(self):
        """Each yield lets Enigma2 process key events, timers and screen transitions."""
        yield from self.browse_to_episode()
        yield from self.check_playback()
        yield from self.search_and_exit()

    def browse_to_episode(self):
        self.previous_dialog = self.session.open(PluginBrowser)
        descriptor = next(p for p in plugins.getPlugins(PluginDescriptor.WHERE_EXTENSIONSMENU) if p.name == "Joyn")
        index, icon = next((index, icon) for index, (entry, _, _, icon) in
                           enumerate(self.previous_dialog.pluginList) if entry == descriptor)
        assert descriptor.iconString == "plugin.png" and descriptor.icon is not None
        assert (icon.size().width(), icon.size().height()) == (100, 40)
        self.previous_dialog.currentList.setCurrentIndex(index)
        self.previous_dialog.keySelect()
        self.screen = self.session.current_dialog
        assert isinstance(self.screen, Browser)
        yield 300

        row = self.screen["items"].getList()[0]
        assert row[0].channel == "Das Erste" and row[0].programme == "Tagesschau"
        current, _ = row[0].now_and_next(int(time()))
        timing = f"{clock(current.start)} – {clock(current.end)}"
        assert any(timing in part for part in row[1:])
        assert "Wetter" in self.screen["details"].getText()
        assert all(self.screen["key_" + color].getText() for color in ("red", "green", "yellow", "blue"))
        self.screen["actions"].action("OkCancelActions", "ok")
        yield 650

        player = self.session.current_dialog
        assert isinstance(player, Player)
        assert player["title"].getText() == "Tagesschau"
        assert "Wetter" in player["status"].getText()
        assert player["progress"].visible and 45 <= player["progress"].getValue() <= 55
        assert not player["key_yellow"].visible and not player["seek_marker"].visible
        player["actions"].action("OkCancelActions", "cancel")
        yield 300

        self.screen["actions"].action("ColorActions", "green")
        yield 300

        assert [item.title for item in self.screen.entries] == ["Movies", "Series", "Search"]
        self.screen["actions"].action("DirectionActions", "down")
        assert self.screen["items"].getCurrentIndex() == 1
        self.screen["actions"].action("OkCancelActions", "ok")
        yield 300

        assert [item.title for item in self.screen.entries] == ["Season 1"]
        self.screen["actions"].action("OkCancelActions", "ok")
        yield 300

        assert [item.title for item in self.screen.entries] == ["Episode 1"]
        assert any("44:00" in part for part in self.screen["items"].getList()[0][1:])
        self.screen["actions"].action("OkCancelActions", "ok")
        yield 1000

    def check_playback(self):
        player = self.session.current_dialog
        assert isinstance(player, Player), type(player)
        assert player.ref is not None and player.ref.type == 4097, player["status"].getText()
        assert player.ref == self.session.nav.getCurrentlyPlayingServiceOrGroup()
        assert "Series" in player["context"].getText() and player["status"].getText() == "Beschreibung der Folge"
        player["actions"].action("OkCancelActions", "ok")
        assert not player["panel"].visible and not player["progress"].visible
        player["actions"].action("InfobarEPGActions", "InfoPressed")
        assert player["panel"].visible and player["time_left"].getText()

        # Record helper restarts while the local test stream keeps playing.
        player.transport.child = SimpleNamespace(poll=lambda: 0)
        player.position = 120
        player.show_info()
        assert player["time_right"].getText() == "44:00" and player["progress"].visible

        seeks = []
        player.open_stream = seeks.append
        # Deliver make/repeat/break through the installed plugin keymap.
        key = eActionMap.getInstance().keyPressed
        right, left = KEYIDS["KEY_RIGHT"], KEYIDS["KEY_LEFT"]
        key("generic", right, 0)
        assert player["seek_caption"].getText().startswith("+00:10")
        yield 1100

        assert not seeks
        key("generic", right, 2)
        assert player["seek_caption"].getText().startswith("+00:40")
        assert player["key_right"].getText() == "+30 s"
        for held, caption in ((3.1, "+60 s"), (6.1, "+120 s")):
            with patch("Plugins.Extensions.JoynPlayer.player.monotonic", return_value=player.pressed_at + held):
                key("generic", right, 2)
                assert player["key_right"].getText() == caption
        key("generic", right, 1)
        key("generic", left, 0)
        key("generic", left, 1)
        assert player["seek_caption"].getText().startswith("+03:30")
        assert player["seek_marker"].visible
        target = player.seek_target
        yield 500

        assert not seeks, "A new key press must restart the debounce timer"
        yield 500

        assert seeks == [target], seeks
        player.transport.child = None
        player["actions"].action("OkCancelActions", "cancel")
        yield 300

    def search_and_exit(self):
        assert self.session.current_dialog is self.screen
        assert [item.title for item in self.screen.entries] == ["Episode 1"]
        self.screen["actions"].action("OkCancelActions", "cancel")
        yield 300

        assert [item.title for item in self.screen.entries] == ["Season 1"]
        self.screen["actions"].action("OkCancelActions", "cancel")
        yield 300

        assert self.screen["items"].getCurrentIndex() == 1
        self.screen["actions"].action("ColorActions", "yellow")
        yield 300

        dialog = self.session.current_dialog
        assert isinstance(dialog, VirtualKeyBoard)
        dialog.close("Joko & Klaas")
        yield 300

        assert [item.title for item in self.screen.entries] == ["Search result"]
        self.screen["actions"].action("ColorActions", "blue")
        yield 300

        assert self.screen.entries[0].title == "Search result"
        self.screen["actions"].action("OkCancelActions", "cancel")
        yield 300

        assert self.screen["items"].getCurrentIndex() == 1
        self.screen["actions"].action("DirectionActions", "down")
        self.screen["actions"].action("OkCancelActions", "ok")
        yield 300

        dialog = self.session.current_dialog
        assert isinstance(dialog, VirtualKeyBoard)
        dialog.close(None)
        yield 300

        assert self.session.current_dialog is self.screen
        assert self.screen["items"].getCurrentIndex() == 2
        self.screen["actions"].action("ColorActions", "red")
        yield 300

        assert self.screen.page.query == LIVE
        assert self.screen["items"].getList()
        self.screen["actions"].action("OkCancelActions", "cancel")
        yield 300

        assert self.session.current_dialog is self.previous_dialog
        assert not getattr(self.screen, "execing", False)


def start(reason, session=None, **kwargs):
    if reason == PluginDescriptor.REASON_START:
        session.joyn_test = GuiTest(session)


def Plugins(**kwargs):
    return PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=start)
