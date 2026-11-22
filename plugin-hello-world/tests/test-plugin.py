"""Exercise the real Enigma2 plugin browser in the test container."""

from traceback import print_exc

from enigma import eTimer, quitMainloop

from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox
from Screens.PluginBrowser import PluginBrowser


class HelloWorldTest:
    def __init__(self, session):
        self.session = session
        self.steps = iter((self.open_browser, self.launch_plugin, self.check_dialog, self.check_return))
        self.timer = eTimer()
        self.timer.callback.append(self.advance)
        self.timer.start(100, True)

    def advance(self):
        step = next(self.steps, None)
        if step is None:
            print("[HelloWorldTest] PASS: registered entry, custom PNG icon, dialog text and return.", flush=True)
            quitMainloop(0)
            return

        try:
            step()
        except Exception:
            print(f"[HelloWorldTest] FAIL: {step.__name__}", flush=True)
            print_exc()
            quitMainloop(1)
        else:
            self.timer.start(100, True)

    def open_browser(self):
        self.browser = self.session.open(PluginBrowser)
        assert self.browser.shown, "The plugin browser did not open"

    def launch_plugin(self):
        for index, (descriptor, name, _, icon) in enumerate(self.browser.pluginList):
            if name == "Hello World":
                break
        else:
            raise AssertionError("Hello World is missing from the plugin browser")

        assert descriptor.iconString == "plugin.png", "The descriptor does not declare the custom icon"
        assert descriptor.icon is not None, "Enigma2 could not load plugin.png"
        assert icon is not None and (icon.size().width(), icon.size().height()) == (100, 40), (
            "The plugin browser row does not contain the expected 100 × 40 icon"
        )
        self.browser.currentList.setCurrentIndex(index)
        self.browser.keySelect()

    def check_dialog(self):
        dialog = self.session.current_dialog
        assert isinstance(dialog, MessageBox) and dialog.shown, "Hello World did not open a message box"
        assert dialog["text"].getText() == "Hello World!", "Unexpected message text"
        dialog.cancel()

    def check_return(self):
        assert self.session.current_dialog is self.browser and self.browser.shown, (
            "Closing Hello World did not return to the plugin browser"
        )


def start(reason, session=None, **kwargs):
    if reason == PluginDescriptor.REASON_START:
        session.hello_world_test = HelloWorldTest(session)


def Plugins(**kwargs):
    return PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=start)
