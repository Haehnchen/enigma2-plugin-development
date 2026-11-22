from time import monotonic

from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from twisted.internet.threads import deferToThread

from .i18n import _
from .twitch import stream_qualities


class StreamQuality(MessageBox):
    def __init__(self, session, login, current):
        super().__init__(session, _("Loading available resolutions…"), type=self.TYPE_INFO,
                         windowTitle=_("Resolution"))
        self.login = login
        self.current = current
        self.lookup = None
        self.result = None
        self.fetched_at = 0

        self.onFirstExecBegin.append(self.load)
        self.onShown.append(self.show_result)
        self.onClose.append(self.cancel_lookup)

    def cancel(self):
        self.close(None)

    def select(self):
        if self.lookup is None:
            self.load()

    def load(self, selected=None):
        self["text"].setText(_("Loading available resolutions…"))
        self.lookup = deferToThread(stream_qualities, self.login)
        self.lookup.addCallbacks(
            lambda options: self.loaded(options, selected),
            lambda failure: self.loaded([], None),
        )

    def cancel_lookup(self):
        if self.lookup is not None:
            self.lookup.cancel()

    def loaded(self, options, selected):
        self.lookup = None
        self.result = (options, selected)
        self.fetched_at = monotonic()
        self.show_result()

    def show_result(self):
        """Present the lookup once the dialog has focus again, e.g. after Help."""
        if not self.execing or self.result is None:
            return

        options, selected = self.result
        self.result = None
        if not options:
            self["text"].setText(_("No resolutions available. Press OK to retry."))
            return

        entries = []
        index = 0
        for name, url, best in options:
            if name == selected:
                self.close((name, url, best))
                return
            if name == self.current or (self.current is None and best):
                index = len(entries)
            label = _("{quality} · Best available").format(quality=name) if best else name
            entries.append((label, (name, url, best)))

        self.session.openWithCallback(
            self.chosen, ChoiceBox, choiceList=entries, selection=index, buttonList=[],
            windowTitle=_("Resolution"), skinName="TwitchStreamQuality",
        )

    def chosen(self, entry=None):
        # Stream URLs can expire while the picker is open.
        if entry and monotonic() - self.fetched_at > 90:
            self.load(selected=entry[1][0])
        else:
            self.close(entry[1] if entry else None)
