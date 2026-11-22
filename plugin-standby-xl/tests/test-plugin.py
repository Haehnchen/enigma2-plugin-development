"""Enter real standby and capture the LCD framebuffer in a disposable receiver."""

from datetime import datetime
from os import environ
from traceback import print_exc
from unittest.mock import patch

from enigma import eLCD, eTimer, quitMainloop
from Components.config import config
from Components.PluginComponent import plugins
from Components.Renderer.Label import Label
from Plugins.Plugin import PluginDescriptor
from Screens.SkinSelection import SkinSelection
from Screens.Standby import Standby, StandbySummary
import skin


class StandbyTest:
    def __init__(self, session):
        self.session = session
        self.steps = iter((self.open_settings, self.close_settings, self.enter_standby, self.capture))
        self.timer = eTimer()
        self.timer.callback.append(self.advance)
        self.timer.start(500, True)

    def advance(self):
        step = next(self.steps, None)
        if step is None:
            print("[StandbyXLTest] PASS", environ["STANDBY_TEST_SKIN"], flush=True)
            quitMainloop(0)
            return
        try:
            step()
        except Exception:
            print_exc()
            quitMainloop(1)
        else:
            self.timer.start(300, True)

    def open_settings(self):
        assert skin.currentDisplaySkin == environ["STANDBY_TEST_SKIN"]
        entries = plugins.getPlugins([PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU])
        assert all(item.name != "Standby XL" for item in entries)
        self.session.open(SkinSelection)

    def close_settings(self):
        dialog = self.session.current_dialog
        assert any(len(item) > 1 and item[1] is config.skin.lcdSkin for item in dialog["config"].list)
        assert "Standby XL/skin_display.xml" in config.skin.lcdSkin.choices
        dialog.close()

    def enter_standby(self):
        timestamp = datetime(2026, 11, 22, 20, 48).timestamp()
        # Only freeze the Enigma2 clock source for the two screenshots.
        self.clock = patch("Components.Sources.Clock.gettime", return_value=timestamp)
        self.clock.start()
        self.session.open(Standby)

    def capture(self):
        summary = self.session.summary
        assert isinstance(summary, StandbySummary) and summary.shown
        assert summary.desktop is self.session.summaryDesktop
        labels = [r.text for r in summary.renderer if isinstance(r, Label)]
        assert labels == ["20:48", "22.11.2026"], labels
        eLCD.getInstance().dumpLCD(True)
        self.clock.stop()
        self.session.current_dialog.Power()


def start(reason, session=None, **kwargs):
    if reason == PluginDescriptor.REASON_START:
        session.standby_test = StandbyTest(session)


def Plugins(**kwargs):
    return PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=start)
