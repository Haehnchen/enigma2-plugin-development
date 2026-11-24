from pathlib import Path
from time import monotonic, time

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Components.ServiceEventTracker import ServiceEventTracker
from enigma import ePoint, eServiceReference, eTimer, iPlayableService
from Screens.Screen import Screen
from Tools.LoadPixmap import LoadPixmap
from twisted.internet.threads import deferToThread

from . import _
from .catalog import LIVE, JoynError, clock, number, runtime
from .playback import Transport


PTS_PER_SECOND = 90_000


class Player(Screen):
    skin = (Path(__file__).parent / "skins/player.xml").read_text(encoding="utf-8")
    SEEK_STEP = 10
    SEEK_DELAY_MS = 850
    REPEAT_INTERVAL = 0.35
    INFO = ("panel", "accent", "logo", "badge", "context", "title", "status", "divider",
            "icon_ok", "key_ok", "icon_exit", "key_exit")
    TIMELINE = ("time_left", "time_center", "time_right", "progress", "seek_marker", "seek_caption")
    VOD_KEYS = ("icon_left", "key_left", "icon_right", "key_right", "icon_yellow", "key_yellow")

    def __init__(self, session, item, stream, catalog):
        super().__init__(session)
        self.item = item
        self.stream = stream
        self.duration = item.duration or max(0, number(stream.get("duration")) or 0)
        self.catalog = catalog

        self.nav = session.nav
        self.previous = self.nav.getCurrentlyPlayingServiceOrGroup()
        self.ref = None
        self.transport = None

        self.position = 0
        self.started = None
        self.decoder_started = False
        self.paused = False

        self.seek_start = self.seek_target = None
        self.seek_direction = 0
        self.pressed_at = 0
        self.next_repeat = 0
        self.seek_timer = eTimer()
        self.seek_timer.callback.append(self.commit_seek)

        self.closed = False
        self.info_visible = False
        self.epg_pending = False
        self.epg_retry_after = 0
        self.hide_timer = eTimer()
        self.hide_timer.callback.append(self.hide_info)
        self.info_timer = eTimer()
        self.info_timer.callback.append(self.update_info)

        for name in self.INFO + self.TIMELINE + self.VOD_KEYS:
            if name == "progress":
                self[name] = ProgressBar()
            elif name.startswith("icon_") or name == "logo":
                self[name] = Pixmap()
            else:
                self[name] = Label()
        self["key_ok"].setText(_("Info"))
        self["key_exit"].setText(_("Close"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "JoynSeekActions", "ColorActions", "InfobarEPGActions"],
            {"cancel": self.close, "ok": self.toggle_info, "InfoPressed": self.toggle_info,
             "EPGPressed": self.toggle_info, "IPressed": self.toggle_info,
             "seekLeft": lambda: self.skip(-1), "seekRight": lambda: self.skip(1),
             "repeatLeft": lambda: self.skip(-1, repeat=True),
             "repeatRight": lambda: self.skip(1, repeat=True),
             "releaseLeft": lambda: self.release_seek(-1),
             "releaseRight": lambda: self.release_seek(1),
             "yellow": self.pause},
            -2,
        )
        self.events = ServiceEventTracker(screen=self, eventmap={
            iPlayableService.evStart: self.started_playing,
            iPlayableService.evGstreamerPlayStarted: self.first_frame,
            iPlayableService.evEOF: self.end_of_stream,
        })
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.finish)

    def start(self):
        self["logo"].instance.setPixmap(LoadPixmap(str(Path(__file__).with_name("plugin.png"))))
        self.open_stream(0)

    def open_stream(self, position):
        self.cancel_seek()
        self.stop_service()
        self.position = position
        self.started = None
        self.decoder_started = False
        self.paused = False
        self.overlay(_("Opening stream…"))
        self.hide_timer.stop()
        transport = Transport(self.stream, position, on_demand=not self.item.live)
        self.transport = transport

        def ready(url):
            if getattr(self, "closed", True) or self.transport is not transport:
                deferToThread(transport.close)
                return
            ref = eServiceReference(4097, 0, url)
            ref.setData(0, 1)
            ref.setName(self.item.title)
            self.ref = ref
            if self.nav.playService(ref):
                self.failed(_("The receiver could not start playback."))
            else:
                self.started_playing()

        def failed(error):
            if not getattr(self, "closed", True) and self.transport is transport:
                value = error.value
                self.failed(str(value) if isinstance(value, JoynError) else _("The stream could not be started."))

        deferToThread(transport.open).addCallbacks(ready, failed)

    def started_playing(self):
        if self.ref is not None and self.started is None and not self.paused:
            self.started = monotonic()
            self.show_info()

    def first_frame(self):
        if self.ref is None or self.decoder_started:
            return
        # Exclude startup buffering from the relay's playback clock.
        self.decoder_started = True
        if self.started is None:
            self.started_playing()
        elif not self.paused:
            self.started = monotonic()

    def end_of_stream(self):
        if self.ref is not None:
            self.close()

    def failed(self, message):
        self.cancel_seek()
        self.stop_service()
        self.hide_timer.stop()
        self.overlay(message)

    def elapsed(self):
        return self.position + (int(monotonic() - self.started) if self.started is not None else 0)

    def native_seek(self):
        service = self.nav.getCurrentService()
        return service and service.seek()

    def video_times(self):
        """Read native timestamps when available. The relay starts a new TS on each seek."""
        position, duration = self.elapsed(), self.duration
        if self.transport.child is None:
            seek = self.native_seek()
            if seek:
                error, pts = seek.getPlayPosition()
                if not error and pts >= 0:
                    position = pts // PTS_PER_SECOND
                error, pts = seek.getLength()
                if not error and pts > 0:
                    duration = pts // PTS_PER_SECOND
        return position, duration

    def skip(self, direction, repeat=False):
        if self.item.live or self.ref is None:
            return
        if self.transport.child is None:
            seeker = self.native_seek()
            if not seeker or not seeker.isCurrentlySeekable():
                return

        now = monotonic()
        if repeat:
            if direction != self.seek_direction or now < self.next_repeat:
                return
        else:
            self.seek_timer.stop()
            self.seek_direction = direction
            self.pressed_at = now

        # Receivers send repeats at different rates. Limit how often the target moves.
        self.next_repeat = now + self.REPEAT_INTERVAL
        held = now - self.pressed_at
        if held >= 6:
            step = 120
        elif held >= 3:
            step = 60
        elif held >= 1:
            step = 30
        else:
            step = self.SEEK_STEP

        position, total = self.video_times()
        if self.seek_target is None:
            self.seek_start = min(position, total) if total else position
            self.seek_target = self.seek_start

        self.seek_target = max(0, self.seek_target + direction * step)
        if total:
            self.seek_target = min(self.seek_target, max(0, total - 1))

        self.show_seek_steps(direction, step)
        self.show_info()

    def show_seek_steps(self, direction, step):
        self["key_left"].setText(f"−{step if direction < 0 else self.SEEK_STEP} s")
        self["key_right"].setText(f"+{step if direction > 0 else self.SEEK_STEP} s")

    def release_seek(self, direction):
        if direction == self.seek_direction:
            self.seek_direction = 0
            self.seek_timer.start(self.SEEK_DELAY_MS, True)

    def cancel_seek(self):
        self.seek_timer.stop()
        self.seek_direction = 0
        self.show_seek_steps(0, self.SEEK_STEP)
        self.seek_start = self.seek_target = None

    def commit_seek(self):
        start, target = self.seek_start, self.seek_target
        self.cancel_seek()
        if self.ref is None or target is None or target == start:
            self.update_info()
            return

        if self.transport.child is not None:
            self.open_stream(target)
            return

        seeker = self.native_seek()
        if seeker and seeker.isCurrentlySeekable():
            seeker.seekTo(target * PTS_PER_SECOND)
        self.update_info()

    def pause(self):
        if self.item.live or self.ref is None:
            return
        service = self.nav.getCurrentService()
        pauseable = service and service.pause()
        if pauseable:
            if self.paused:
                if pauseable.unpause() == 0:
                    self.paused = False
                    self.started = monotonic()
            elif pauseable.pause() == 0:
                self.position = self.elapsed()
                self.started = None
                self.paused = True
        self.show_info()

    def visibility(self, fields, visible):
        for field in fields:
            self[field].setVisible(visible)

    def toggle_info(self):
        if self.ref is not None:
            if self.info_visible:
                self.hide_info()
            else:
                self.show_info()

    def show_info(self):
        if self.ref is not None:
            self.info_visible = True
            self.visibility(self.INFO, True)
            self.visibility(self.VOD_KEYS, not self.item.live)
            self.update_info()
            self.info_timer.start(1000)
            self.hide_timer.stop()
            if not self.paused:
                self.hide_timer.start(6500, True)

    def update_info(self):
        if not self.info_visible or self.ref is None:
            return
        if self.item.live:
            self.update_live_info()
        else:
            self.update_video_info()

    def update_live_info(self):
        timestamp = int(time())
        current, following = self.item.now_and_next(timestamp)
        self["badge"].setText(_("Live TV"))
        self["context"].setText(self.item.channel or self.item.title)
        title = self.item.programme_title(current)
        self["title"].setText(title or _("No programme information"))

        parts = []
        if following and following.title:
            parts.append(following.next_label())
        if current and current.subtitle and current.subtitle != current.title:
            parts.append(current.subtitle)
        self["status"].setText("   •   ".join(parts))

        if current:
            remaining = (current.end - timestamp + 59) // 60
            self.timeline(clock(current.start), _("{minutes} min left").format(minutes=remaining),
                          clock(current.end), timestamp - current.start, current.end - current.start)
        else:
            self.timeline("", "", "")

        if (current is None or following is None) and self.item.channel_id:
            self.refresh_schedule()

    def refresh_schedule(self):
        if self.epg_pending or monotonic() < self.epg_retry_after:
            return

        self.epg_pending = True
        channel_id = self.item.channel_id

        def finished(payload):
            if getattr(self, "closed", True):
                return
            self.epg_pending = False
            self.epg_retry_after = monotonic() + 90
            if payload is not None:
                match = next((item for item in self.catalog.items(payload) if item.channel_id == channel_id), None)
                if match and match.schedule:
                    self.item = match
                    self.update_info()

        deferToThread(self.catalog.request, LIVE).addCallbacks(finished, lambda error: finished(None))

    def update_video_info(self):
        item = self.item
        self["badge"].setText(_("Paused") if self.paused else _("Video"))
        self["context"].setText(item.video_metadata())
        self["title"].setText(item.title)
        self["status"].setText(item.description if item.description != item.title else "")
        self["key_yellow"].setText(_("Resume") if self.paused else _("Pause"))

        position, total = self.video_times()
        if total:
            position = min(position, total)
            self.timeline(runtime(position), "−" + runtime(total - position), runtime(total), position, total)
        else:
            self.timeline(runtime(position), "", "")
        self.show_seek_preview(total)

    def show_seek_preview(self, total):
        preview = self.seek_target is not None
        self["seek_caption"].setVisible(preview)
        self["seek_marker"].setVisible(preview and bool(total))
        if not preview:
            return
        self["time_center"].hide()
        offset = self.seek_target - self.seek_start
        sign = "−" if offset < 0 else "+" if offset > 0 else ""
        self["seek_caption"].setText(f"{sign}{runtime(abs(offset))}  →  {runtime(self.seek_target)}")
        if total:
            bar = self["progress"].instance
            marker = self["seek_marker"].instance
            width = bar.size().width() - marker.size().width()
            marker.move(ePoint(bar.position().x() + width * self.seek_target // total, marker.position().y()))

    def timeline(self, left, center, right, position=0, total=0):
        for field, caption in (("time_left", left), ("time_center", center), ("time_right", right)):
            self[field].setText(caption)
            self[field].setVisible(bool(caption))
        self["progress"].setVisible(total > 0)
        if total > 0:
            self["progress"].setValue(position * 100 // total)

    def overlay(self, message):
        self.info_visible = True
        self.info_timer.stop()
        self.visibility(self.INFO, True)
        self.visibility(self.TIMELINE + self.VOD_KEYS + ("icon_ok", "key_ok"), False)
        self["badge"].setText(_("Live TV") if self.item.live else _("Video"))
        self["context"].setText(self.item.channel or self.item.series or "Joyn")
        self["title"].setText(self.item.programme or self.item.title)
        self["status"].setText(message)

    def hide_info(self):
        self.info_visible = False
        self.hide_timer.stop()
        self.info_timer.stop()
        self.visibility(self.INFO + self.TIMELINE + self.VOD_KEYS, False)

    def stop_service(self):
        ref, self.ref = self.ref, None
        if ref is not None and ref == self.nav.getCurrentlyPlayingServiceOrGroup():
            self.nav.stopService()
        if self.transport:
            deferToThread(self.transport.close)
            self.transport = None

    def finish(self):
        self.closed = True
        self.cancel_seek()
        self.hide_timer.stop()
        self.info_timer.stop()
        current = self.nav.getCurrentlyPlayingServiceOrGroup()
        restore = current is None or current == self.ref
        self.stop_service()
        if restore and self.previous is not None:
            self.nav.playService(self.previous)
