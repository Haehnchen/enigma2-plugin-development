"""Exercise bouquet ownership, EPG transitions and standby timers in a disposable OpenATV session."""

from dataclasses import replace
from time import monotonic, time
from traceback import print_exc

from enigma import eDVBDB, eEPGCache, eServiceCenter, eServiceReference, eTimer, quitMainloop
from Plugins.Plugin import PluginDescriptor
from Plugins.Extensions.TwitchPlayer.twitch import Channel
from Screens import Standby
from Screens.InfoBar import InfoBar


def entries(filename):
    root = eServiceReference(f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{filename}" ORDER BY bouquet')
    return eServiceCenter.getInstance().list(root).getContent("R", True)


def bouquet_paths():
    return [ref.getPath() for ref in entries("bouquets.tv")]


class BouquetTest:
    def __init__(self, session):
        self.session = session
        self.steps = self.run()
        self.timer = eTimer()
        self.timer.callback.append(self.advance)
        self.timer.start(800, True)

    def advance(self):
        try:
            delay = next(self.steps)
        except StopIteration:
            print("[TwitchBouquetTest] PASS", flush=True)
            quitMainloop(0)
        except Exception:
            print_exc()
            quitMainloop(1)
        else:
            self.timer.start(delay, True)

    def run(self):
        runtime = self.session.twitch_bouquet
        library = runtime.library
        prefs = library.prefs
        assert not prefs.bouquet_enabled and not runtime.timer.isActive() and not library.pending
        assert (prefs.interval, prefs.standby_interval) == (10, 60)

        db = eDVBDB.getInstance()
        db.addOrUpdateBouquet("Twitch Live", "userbouquet.somebody_else.tv", [], False)
        originals = bouquet_paths()
        alice = Channel("alice", viewers=8649, followers=900, game="Dota 2", title="First title", user_id=123)
        bob = Channel("bob", followers=100, user_id=456)
        rows = {"bob": bob, "alice": alice}
        library.fetch = lambda *args: ([], list(rows), dict(rows), "")
        prefs.save(list(rows), prefs.position, bouquet_enabled=True, interval=1, standby_interval=2)
        runtime.configure()
        while library.pending:
            yield 20
        filename = runtime.filename
        owned = bouquet_paths()[-1]
        assert filename in owned and bouquet_paths() == originals + [owned]
        online, first, offline, second = entries(filename)
        assert online.flags & offline.flags & eServiceReference.isMarker
        assert [ref.getName() for ref in (online, first, offline, second)] == ["Online", "alice", "Offline", "bob"]
        epg = eEPGCache.getInstance()
        event = epg.lookupEventTime(first, int(time()))
        assert event.getEventName() == "8.6k · Dota 2 · First title" and "8649" in event.getShortDescription()

        db.addOrUpdateBouquet("Later", "userbouquet.later.tv", [], False)
        later = bouquet_paths()[-1]
        rows["alice"] = replace(alice, viewers=None)
        # The active interval is already due, but the standby interval still has two seconds left.
        runtime.last_attempt = monotonic() - 118
        last = runtime.last_attempt
        standby = self.session.open(Standby.Standby)
        yield 300
        assert self.session.isStandby and runtime.interval == 2 and runtime.last_attempt == last
        while runtime.last_attempt == last or library.pending:
            yield 50
        assert [ref.getName() for ref in entries(filename)] == ["Online", "Offline", "alice", "bob"]
        assert epg.lookupEventTime(first, int(time())) is None
        assert bouquet_paths() == originals + [owned, later]

        rows["alice"] = replace(alice, viewers=1_200_000, game="", title="Back live")
        runtime.last_attempt = monotonic() - 65
        standby.Power()
        while self.session.isStandby or runtime.last_attempt < monotonic() - 60 or library.pending:
            yield 50
        assert runtime.interval == 1 and epg.lookupEventTime(first, int(time())).getEventName() == "1.2M · Back live"

        # Removing the currently browsed bouquet must not recreate an orphan in the native database.
        selection = InfoBar.instance.servicelist
        selection.enterUserbouquet(eServiceReference(f"1:7:1:0:0:0:0:0:0:0:{owned}"))
        prefs.save(list(rows), prefs.position, bouquet_enabled=False)
        runtime.configure()
        assert not runtime.timer.isActive() and bouquet_paths() == originals + [later]
        prefs.save(list(rows), prefs.position, bouquet_enabled=True)
        runtime.configure()
        while library.pending:
            yield 20
        assert runtime.filename == filename and bouquet_paths() == originals + [later, owned]


def start(reason, session, **kwargs):
    if reason == PluginDescriptor.REASON_START:
        session.bouquet_test = BouquetTest(session)


def Plugins(**kwargs):
    return PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=start)
