import re
from pathlib import Path
from time import monotonic, time

from Components.config import config
from enigma import eDVBDB, eEPGCache, eServiceCenter, eServiceReference, eTimer, iPlayableService
from Screens import Standby
from Screens.InfoBar import InfoBar
from Tools.Directories import SCOPE_CONFIG, resolveFilename

from .i18n import _
from .library import Library
from .player import Player
from .twitch import Channel, bouquet_login, format_count, player_reference


def section_marker(number, name):
    ref = eServiceReference(1, eServiceReference.isMarker, "")
    ref.setData(0, number)
    ref.setName(name)
    return ref.toString()


def epg_key(ref):
    return (ref.getUnsignedData(1), ref.getUnsignedData(3), ref.getUnsignedData(2))


def live_epg_keys(refs):
    keys = set()
    for ref in refs:
        if ref.flags & eServiceReference.isMarker and ref.getData(0) == 2:
            break
        if bouquet_login(ref.getPath()):
            keys.add(epg_key(ref))
    return keys


class TwitchBouquet:
    def __init__(self, session):
        self.session = session
        self.library = Library(self.publish)
        self.timer = eTimer()
        self.timer.callback.append(self.refresh)
        self.due = False
        self.generation = -1
        self.last_attempt = monotonic()

        self.launch_timer = eTimer()
        self.launch_timer.callback.append(self.open_player)
        self.pending_ref = None
        self.player = None
        self.previous = session.nav.getCurrentlyPlayingServiceOrGroup()
        session.nav.event.append(self.service_event)
        session.nav.playServiceExtensions.append(self.play_service)
        config.misc.standbyCounter.addNotifier(self.enter_standby, initial_call=False)
        session.onShutdown.append(self.close)
        self.configure()

    @property
    def filename(self):
        return f"userbouquet.twitchplayer_{self.library.prefs.bouquet_id}.tv"

    def existing_services(self):
        if not Path(resolveFilename(SCOPE_CONFIG, self.filename)).is_file():
            return []
        root = eServiceReference(f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{self.filename}" ORDER BY bouquet')
        services = eServiceCenter.getInstance().list(root)
        return services.getContent("R", True) if services else []

    def configure(self):
        self.timer.stop()
        prefs = self.library.prefs
        if not prefs.bouquet_id:
            return
        exists = Path(resolveFilename(SCOPE_CONFIG, self.filename)).is_file()
        db = eDVBDB.getInstance()
        if prefs.bouquet_enabled:
            if not exists:
                db.addOrUpdateBouquet("Twitch Live", self.filename, [], False)
                self.reload_list()
            self.refresh()
        elif exists:
            keys = live_epg_keys(self.existing_services())
            bar = InfoBar.instance
            if bar and self.is_own_root(bar.servicelist.getRoot()):
                # Listing the deleted root would recreate an empty bouquet in OpenATV's database.
                bar.servicelist.clearPath()
                bar.servicelist.enterPath(bar.servicelist.bouquet_root)
            db.removeBouquet("^" + re.escape(self.filename) + "$")
            for key in keys:
                eEPGCache.getInstance().flushEPG(*key)
            self.reload_list()

    def refresh(self):
        if not self.library.prefs.bouquet_enabled:
            return
        self.last_attempt = monotonic()
        self.schedule()
        self.due = True
        self.library.refresh()

    @property
    def interval(self):
        prefs = self.library.prefs
        return prefs.standby_interval if self.session.isStandby else prefs.interval

    def schedule(self):
        if self.library.prefs.bouquet_enabled:
            delay = self.interval * 60 - (monotonic() - self.last_attempt)
            self.timer.start(max(1, round(delay * 1000)), True)

    def enter_standby(self, counter):
        Standby.inStandby.onClose.append(self.leave_standby)
        self.library.timer.stop()
        self.schedule()

    def leave_standby(self):
        if self.library.foreground:
            self.library.timer.start(90000)
        self.schedule()

    def publish(self, online, offline):
        prefs = self.library.prefs
        if not prefs.bouquet_enabled or (not self.due and self.generation == self.library.generation):
            return
        old = self.existing_services()
        stale = live_epg_keys(old)
        refs = []
        cache = eEPGCache.getInstance()
        now = int(time())
        # Cover a switch to the longer interval while a snapshot is still on screen.
        duration = (max(prefs.interval, prefs.standby_interval) + 1) * 60
        for number, title, rows in ((1, _("Online"), online), (2, _("Offline"), offline)):
            refs.append(section_marker(number, title))
            for row in rows:
                ref = eServiceReference(player_reference(row))
                ref.setName(row.display or row.login)
                refs.append(ref.toString())
                if not row.live:
                    continue
                stale.discard(epg_key(ref))
                viewers = _("{count} viewers").format(count=row.viewers)
                title_parts = (format_count(row.viewers), row.game, row.title or row.display or row.login)
                event_title = " · ".join(filter(None, title_parts))
                event = (now, duration, event_title, viewers, row.game, 0, 1)
                cache.importEvents(refs[-1], [event])
        for key in stale:
            cache.flushEPG(*key)
        if [ref.toString() for ref in old] != refs:
            eDVBDB.getInstance().addOrUpdateBouquet("Twitch Live", self.filename, refs, False)
            self.reload_list()
        self.due = False
        self.generation = self.library.generation

    def reload_list(self):
        bar = InfoBar.instance
        if bar and bar.servicelist:
            selection = bar.servicelist
            root = selection.getRoot()
            if root and (root == selection.bouquet_root or self.is_own_root(root)):
                selection.servicelist.resetRoot()

    def is_own_root(self, root):
        return root and f'FROM BOUQUET "{self.filename}"' in root.getPath()

    def service_event(self, event):
        if event == iPlayableService.evStart:
            ref = self.session.nav.getCurrentlyPlayingServiceOrGroup()
            if ref and not bouquet_login(ref.getPath()):
                self.previous = ref

    def play_service(self, navigation, ref, event, info_bar):
        if not bouquet_login(ref.getPath()) or ref.getAlternativeUrl():
            return ref
        self.pending_ref = ref
        self.launch_timer.start(1, True)
        # Navigation must finish closing the channel selection before opening the player.
        return ref, True

    def open_player(self):
        ref = self.pending_ref
        if ref != self.session.nav.getCurrentlyPlayingServiceOrGroup():
            self.pending_ref = None
            return
        dialog = self.session.current_dialog
        if not self.session.in_exec or (dialog is not InfoBar.instance and dialog is not self.player):
            self.launch_timer.start(250, True)
            return
        self.pending_ref = None
        login = bouquet_login(ref.getPath())
        row = self.library.channels.get(login) or Channel(login, display=ref.getName())
        if self.player:
            self.player.bouquet_ref = ref
            self.player.play(row)
        else:
            self.library.start()
            self.player = self.session.openWithCallback(self.player_closed, Player, self.library, row, ref, self.previous)

    def player_closed(self):
        self.player = None
        self.library.leave()

    def close(self):
        self.timer.stop()
        self.launch_timer.stop()
        self.library.close()
        config.misc.standbyCounter.removeNotifier(self.enter_standby)
        if Standby.inStandby and self.leave_standby in Standby.inStandby.onClose:
            Standby.inStandby.onClose.remove(self.leave_standby)
        self.session.nav.event.remove(self.service_event)
        self.session.nav.playServiceExtensions.remove(self.play_service)
