from functools import partial
from pathlib import Path

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.PluginComponent import plugins
from Components.Sources.StaticText import StaticText
from enigma import eServiceReference, eTimer, iPlayableService
from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from twisted.internet.threads import deferToThread

from .chat import Chat
from .chat_view import ChatView, StreamTitle
from .i18n import _
from .lcd_title import LcdTitle
from .quality import StreamQuality
from .twitch import bouquet_login, bouquet_reference, format_count, player_reference


class TwitchPlaybackSummary(Screen):
    skin = (Path(__file__).with_name("skins") / "player-summary.xml").read_text(encoding="utf-8")

    def __init__(self, session, parent):
        super().__init__(session, parent)
        self["channel"] = StaticText()
        self["streamTitle"] = LcdTitle()
        self["game"] = StaticText()
        self["avatar"] = Pixmap()
        self["viewerCount"] = StaticText()
        self["followerCount"] = StaticText()
        self["duration"] = StaticText()
        self.timer = eTimer()
        self.timer.callback.append(self.update_stream)
        self.onShow.append(self.resume)
        self.onHide.append(self.timer.stop)

    def resume(self):
        self.update_stream()
        self.timer.start(60000)

    def update_stream(self):
        player = self.parent
        row = player.row
        playing = player.is_playing()
        self["channel"].text = row.display or row.login
        self["streamTitle"].setText((row.title if playing else "") or player.playback_status)
        self["game"].text = row.game if playing else ""
        self["viewerCount"].text = format_count(row.viewers) if playing and row.live else "–"
        self["followerCount"].text = (row.follower_count() if playing else "") or "–"
        self["duration"].text = (row.live_duration() if playing else "") or "–"


class Player(Screen):
    skin = (Path(__file__).with_name("skins") / "player.xml").read_text(encoding="utf-8")
    INFO = ("info_bg", "initial", "name", "category", "details",
            "icon_ok", "icon_green", "icon_yellow", "icon_red", "icon_exit",
            "key_ok", "key_green", "key_yellow", "key_red", "key_exit")
    CHAT = ("chat_bg", "chat_name", "chat_position_icon", "chat_position_key", "chat_text")

    def __init__(self, session, library, row, bouquet_ref=None, previous=None):
        super().__init__(session)
        self.skinName = "TwitchPlayback"
        self.library = library
        self.row = row
        self.bouquet_ref = bouquet_ref
        self.previous = previous if bouquet_ref is not None else session.nav.getCurrentlyPlayingServiceOrGroup()
        self.ref = None
        self.quality = None
        self.request = 0
        self.info_visible = True
        self.chat = None
        self.playback_status = ""

        for name in self.INFO + ("chat_bg", "chat_name", "chat_top_left"):
            if name == "details":
                self[name] = StreamTitle()
            else:
                self[name] = Pixmap() if name.startswith("icon_") else Label()
        self["chat_text"] = ChatView()
        self["chat_position_key"] = Label(_("Position"))
        for name in ("avatar", "cover", "chat_position_icon"):
            self[name] = Pixmap()
        for key, caption in (("ok", _("Channels")), ("green", _("Channels")), ("yellow", _("Chat")),
                             ("red", _("Resolution")), ("exit", _("Close"))):
            self["key_" + key].setText(caption)

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions", "InfobarEPGActions"], {
            "cancel": self.close, "ok": self.ok_pressed, "InfoPressed": self.toggle_info,
            "left": self.browse, "right": self.browse,
            "green": self.browse, "yellow": self.toggle_chat, "red": self.choose_quality,
        }, -1)
        self.bind_receiver_menus()
        self["chat_actions"] = ActionMap(["NumberActions"], {"1": self.cycle_chat_position}, -1)
        self["chat_actions"].setEnabled(False)

        self.info_timer = eTimer()
        self.info_timer.callback.append(self.hide_info)
        self.library.listeners.append(self.update)
        session.nav.event.append(self.service_event)
        self.onLayoutFinish.append(self.start)
        self.onShown.append(self.resume)
        self.onExecEnd.append(self.suspend)
        self.onClose.append(self.stop)

    def createSummary(self):
        return TwitchPlaybackSummary

    def bind_receiver_menus(self):
        from Screens.InfoBar import InfoBar

        infobar = InfoBar.instance
        if infobar is None:
            return
        # Keep the receiver's menus and extension list active only while this player has focus.
        for name in ("MenuActions", "InstantExtensionsActions"):
            if name in infobar:
                actions = infobar[name]
                self.onExecBegin.append(actions.execBegin)
                self.onExecEnd.append(actions.execEnd)
        if hasattr(infobar, "ButtonSetupGlobal"):
            self["blue_shortcuts"] = ActionMap(["ButtonSetupActions"], {
                key: partial(infobar.ButtonSetupGlobal, key) for key in ("blue", "blue_long")
            }, -10)
            self.onExecEnd.append(infobar.clearLongKeyPressed)

    def start(self):
        self["chat_top_left"].hide()
        self.chat_positions = {name: self[name].instance.position() for name in self.CHAT}
        self.position_chat()
        self.visibility(self.CHAT, False)
        self.play(self.row)

    def resume(self):
        self.position_chat()
        if self.chat:
            self["chat_text"].resume()
        self.update()

    def suspend(self):
        self.library.previews.show([])
        self["chat_text"].pause()

    def play(self, row):
        """Resolve the requested channel while the current service keeps playing."""
        self.request += 1
        request = self.request
        self.playback_status = _("Opening stream…")
        self.show_info()

        wrapper = next((p for p in plugins.getPlugins(PluginDescriptor.WHERE_PLAYSERVICE)
                        if p.name == "StreamlinkWrapper"), None)
        if wrapper is None:
            self.failed(request, _("Install the StreamlinkWrapper plugin to play streams."))
            return
        # Only the Streamlink lookup runs in the worker. Navigation belongs to the GUI thread.
        service = eServiceReference(bouquet_reference(row.login))
        service.setName(row.display or row.login)
        deferToThread(wrapper, service=service).addCallbacks(
            partial(self.resolved, request, row),
            lambda failure: self.failed(request, _("Streamlink could not open this channel.")))

    def resolved(self, request, row, result, quality=None):
        if request != getattr(self, "request", None):
            return

        url, error = result
        if error or not url:
            self.failed(request, _("No playable stream. The channel may be offline."))
            return

        nav = self.session.nav
        previous = nav.getCurrentlyPlayingServiceOrGroup()
        if self.bouquet_ref is not None:
            if previous is None or (previous != self.bouquet_ref and previous != self.ref):
                return  # The receiver changed channels while Streamlink was resolving this request.
            logical = (self.bouquet_ref.toString() if bouquet_login(self.bouquet_ref.getPath()) == row.login
                       else player_reference(row))
            ref = eServiceReference(logical)
            ref.setAlternativeUrl(url)
        else:
            ref = eServiceReference(4097, 0, url)
            ref.setData(0, 1)
        ref.setName(row.display or row.login)
        failed = nav.playService(ref, forceRestart=previous is not None and ref == previous)
        current = nav.getCurrentlyPlayingServiceOrGroup()
        if failed or not current:
            if previous and (not current or current != previous):
                nav.playService(previous)
            message = _("Could not switch resolution.") if quality else _("The receiver could not start playback.")
            self.failed(request, message)
            return

        channel_changed = self.row.login != row.login
        self.ref = ref
        self.row = row
        self.quality = quality
        self.playback_status = ""
        self["key_red"].setText(_("Resolution") + (" · " + quality if quality else ""))

        if self.chat and channel_changed:
            self.stop_chat()
            self.start_chat()
        self.show_info()

    def failed(self, request, message):
        if request == getattr(self, "request", None):
            self.playback_status = message
            self.show_info()

    def is_playing(self):
        current = self.session.nav.getCurrentlyPlayingServiceOrGroup()
        return self.ref is not None and current is not None and current == self.ref

    def service_event(self, event):
        if not self.is_playing():
            return
        if event in (iPlayableService.evTuneFailed, iPlayableService.evStreamError, iPlayableService.evEOF):
            self.failed(self.request, _("Playback interrupted. Choose a channel with Green."))
        elif event == iPlayableService.evGstreamerPlayStarted:
            self.playback_status = ""
            self.show_info()

    def update(self):
        self.row = self.library.channels.get(self.row.login, self.row)
        display = self.row.display or self.row.login
        self["name"].setText(display)
        self["initial"].setText(display[:1].upper())
        followers = self.row.follower_count() if self.is_playing() else ""
        follower_stats = _("{count} followers").format(count=followers) if followers else ""
        self["category"].setText(" · ".join(filter(None, (self.row.game, follower_stats, self.row.live_stats()))))
        self["details"].setText(self.playback_status or self.row.title)
        self.summaries.update_stream()
        self.update_images()

    def update_images(self):
        if not self.execing:
            return
        avatar = self.row.profile_image or self.row.avatar
        images = []
        if self.info_visible and self["cover"].instance:
            images.extend(((self["avatar"], avatar, 0), (self["cover"], self.row.cover, 0)))
        for summary in self.summaries:
            if summary.shown and summary["avatar"].skinAttributes:
                images.append((summary["avatar"], avatar if self.is_playing() else "", 0))
        self.library.previews.show(images)

    def visibility(self, names, visible):
        for name in names:
            self[name].setVisible(visible)

    def show_info(self):
        self.info_visible = True
        self.visibility(self.INFO, True)
        self.update()
        self.info_timer.stop()
        if not self.playback_status:
            self.info_timer.start(5200, True)

    def hide_info(self):
        self.info_visible = False
        self.visibility(self.INFO + ("avatar", "cover"), False)
        self.update_images()

    def ok_pressed(self):
        if self.info_visible:
            self.browse()
        else:
            self.show_info()

    def toggle_info(self):
        if self.info_visible:
            self.hide_info()
        else:
            self.show_info()

    def browse(self):
        from .plugin import Browser
        self.session.openWithCallback(self.chosen, Browser, self.library, self.row.login)

    def chosen(self, row=None):
        if row:
            self.play(row)

    def choose_quality(self):
        login = self.row.login
        self.session.openWithCallback(partial(self.quality_chosen, login), StreamQuality, login, self.quality)

    def quality_chosen(self, login, choice=None):
        if choice is None or login != self.row.login:
            return

        name, url, best = choice
        same_quality = name == self.quality or (self.quality is None and best)
        if self.ref and not self.playback_status and same_quality:
            return

        self.request += 1
        self.resolved(self.request, self.row, (url, None), quality=name)

    def toggle_chat(self):
        if self.chat:
            self.stop_chat()
        else:
            self.start_chat()

    def position_chat(self):
        """Move the chat widgets together using the active skin's corner positions."""
        if hasattr(self, "chat_positions"):
            origin = self.chat_positions["chat_bg"]
            corner = self["chat_top_left"].instance.position()
            vertical, side = self.library.prefs.position.split("-")
            x = {"left": corner.x(), "center": (corner.x() + origin.x()) // 2, "right": origin.x()}[side]
            y = {"top": corner.y(), "middle": (corner.y() + origin.y()) // 2, "bottom": origin.y()}[vertical]
            for name, point in self.chat_positions.items():
                self[name].move(point.x() + x - origin.x(), point.y() + y - origin.y())

    def cycle_chat_position(self):
        try:
            self.library.prefs.cycle_chat_position()
        except OSError:
            self.session.open(MessageBox, _("Could not save Twitch settings."), type=MessageBox.TYPE_INFO)
            return
        self.position_chat()

    def start_chat(self):
        view = self["chat_text"]
        view.channel = self.row.login
        view.set_status(_("Connecting to chat…"))
        view.resume()
        self["chat_name"].setText("#" + self.row.login)
        self.visibility(self.CHAT, True)
        self["chat_actions"].setEnabled(True)
        self.chat = Chat(self.row.login, view.append, self.chat_status)

    def chat_status(self, connected):
        if self.chat:
            status = _("Waiting for messages…") if connected else _("Reconnecting to chat…")
            self["chat_text"].set_status(status)

    def stop_chat(self):
        self.chat.close()
        self.chat = None
        self["chat_actions"].setEnabled(False)
        self["chat_text"].clear()
        self.visibility(self.CHAT, False)

    def stop(self):
        self.request += 1
        self.library.listeners.remove(self.update)
        self.session.nav.event.remove(self.service_event)
        self.info_timer.stop()
        if self.chat:
            self.stop_chat()

        current = self.session.nav.getCurrentlyPlayingServiceOrGroup()
        if current is None or current == self.ref or current == self.bouquet_ref:
            self.session.nav.stopService()
            if self.previous:
                self.session.nav.playService(self.previous)
