from collections import OrderedDict
from functools import lru_cache, partial
from os import getenv
from pathlib import Path
from time import monotonic

from enigma import ePicLoad, eTimer
from Tools.Directories import SCOPE_CONFIG, resolveFilename
from twisted.internet.defer import Deferred, DeferredSemaphore, inlineCallbacks
from twisted.internet.threads import deferToThread

from .i18n import _
from .twitch import Account, Channel, Preferences, bouquet_sections, download_preview, fetch_channels


class Previews:
    """Load visible images in priority order, with bounded downloads and native decoders."""

    def __init__(self, capacity=24, max_jobs=3):
        self.wanted = {}
        self.jobs = {}
        self.cache = OrderedDict()
        self.capacity = capacity
        self.max_jobs = max_jobs
        self.decode_slots = DeferredSemaphore(3)

    def show(self, images):
        wanted = {}
        for widget, url, revision in images:
            if not url:
                widget.hide()
                continue
            size = widget.getSize()
            url = url.replace("{width}", str(size[0])).replace("{height}", str(size[1]))
            key = (url, size, revision)
            paint = partial(self.paint, widget)
            wanted.setdefault(key, []).append(paint)
            paint(self.cached(key))
        self.request(wanted)

    def request(self, wanted):
        """Set the image callbacks for the current screen or grid page."""
        self.wanted = wanted
        for key in wanted:
            if key in self.cache:
                self.cache.move_to_end(key)

        # Visible images must stay cached, even in a chat line full of different emotes.
        if len(self.cache) > self.capacity:
            for key in list(self.cache):
                if len(self.cache) <= self.capacity:
                    break
                if key not in wanted:
                    del self.cache[key]
        self.pump()

    def cached(self, key):
        pixmap = self.cache.get(key)
        if pixmap is None and key[2]:
            # Keep this channel's previous frame visible during a refresh.
            pixmap = next((frame for previous, frame in reversed(self.cache.items())
                           if previous[:2] == key[:2] and frame is not None), None)
        return pixmap

    @staticmethod
    def paint(widget, pixmap):
        if pixmap is not None and pixmap is not widget.pixmap:
            widget.setPixmap(pixmap)
        widget.setVisible(pixmap is not None)

    def pump(self):
        for key in self.wanted:
            if len(self.jobs) >= self.max_jobs:
                break
            if key not in self.jobs and key not in self.cache:
                self.jobs[key] = None
                download = deferToThread(download_preview, key[0])
                download.addCallback(partial(self.decode_slots.run, self.decode, key))
                download.addCallbacks(partial(self.done, key), lambda failure, key=key: self.done(key, None))

    @inlineCallbacks
    def decode(self, key, filename):
        """Keep the downloaded file until ePicLoad signals that decoding has finished."""
        path = Path(filename)
        decoder = None
        try:
            if key not in self.wanted:
                return

            decoder = self.jobs[key] = ePicLoad()
            width, height = key[1]
            decoder.setPara((width, height, 1, 1, False, 1, "#FF000000"))  # Transparent padding.
            ready = Deferred()
            decoder.PictureData.get().append(ready.callback)
            if not decoder.startDecode(filename):
                yield ready
                return decoder.getData()
        finally:
            if decoder is not None:
                decoder.PictureData.get().clear()
            path.unlink(missing_ok=True)

    def done(self, key, pixmap):
        del self.jobs[key]
        if key in self.wanted:
            self.cache[key] = pixmap
            for callback in self.wanted[key]:
                callback(pixmap)
        self.pump()


@lru_cache(maxsize=1)
def receiver_account(directory):
    """Share the token refresh lock with requests from a recently closed browser."""
    return Account(directory)


class Library:
    def __init__(self, publish):
        directory = Path(getenv("ENIGMA2_TWITCH_CONFIG") or resolveFilename(SCOPE_CONFIG, "twitch-player"))
        self.prefs = Preferences(directory)
        self.account = receiver_account(directory)
        self.followed = []
        self.channels = {name: Channel(name) for name in self.prefs.channels}
        self.status = ""

        self.previews = Previews(max_jobs=8)
        self.size = (288, 162)
        self.avatar_width = 20
        self.revision = 0

        self.listeners = []
        self.publish = publish
        self.foreground = 0
        self.timer = eTimer()
        self.timer.callback.append(self.refresh)
        self.generation = 0
        self.pending = False
        self.closed = False

    def start(self, size=None, avatar_width=None):
        if size is not None:
            self.size = size
            self.avatar_width = avatar_width
        self.foreground += 1
        self.timer.start(90000)
        self.refresh()

    def leave(self):
        self.foreground -= 1
        if not self.foreground:
            self.timer.stop()
            self.previews.show([])

    def close(self):
        self.closed = True
        self.timer.stop()
        self.listeners.clear()
        self.previews.show([])

    def notify(self):
        for listener in tuple(self.listeners):
            listener()

    def changed(self, account=False):
        self.generation += 1
        if account:
            self.followed = []
        names = dict.fromkeys(self.prefs.channels + self.followed)
        self.channels = {name: self.channels.get(name, Channel(name)) for name in names}
        self.notify()
        self.refresh()

    def refresh(self):
        if self.pending or self.closed:
            return

        self.pending = True
        for key in list(self.previews.cache):
            if self.previews.cache[key] is None:
                del self.previews.cache[key]
        self.status = _("Updating channels…")
        self.notify()

        generation = self.generation
        lookup = deferToThread(self.fetch, list(self.prefs.channels), self.followed, self.size)
        lookup.addCallbacks(partial(self.finished, generation), lambda failure: self.finished(generation, None))

    def fetch(self, custom, previous_follows, size):
        status = ""
        try:
            follows = self.account.followed()
        except Exception:
            if self.account.tokens:
                follows = previous_follows
                status = _("Followed channels unavailable")
            else:
                follows = []
                status = _("Please connect Twitch again")

        names = list(dict.fromkeys(custom + follows))
        try:
            errors = []
            info = fetch_channels(names, size, self.avatar_width, errors=errors)
            if errors:
                status = _("Channel details unavailable")
        except Exception:
            info = None
            status = _("Channel details unavailable")
        return follows, names, info, status

    def finished(self, generation, result):
        self.pending = False
        if self.closed:
            return
        # Account or channel settings changed while the worker was fetching this list.
        if generation != self.generation:
            self.refresh()
            return

        if result is None:
            self.status = _("Channel details unavailable")
        else:
            self.followed, names, info, self.status = result
            if info is not None:
                self.revision = int(monotonic() // 90) + 1
            else:
                info = self.channels

            rows = [info.get(name, Channel(name)) for name in names]
            online, offline = bouquet_sections(rows)
            self.channels = {row.login: row for row in online + offline}
            if not self.status:
                self.publish(online, offline)
        self.notify()
