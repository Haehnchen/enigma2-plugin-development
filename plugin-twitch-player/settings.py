from pathlib import Path
from time import monotonic

from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.config import ConfigElement, ConfigSelection, getConfigListEntry
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Sources.StaticText import StaticText
from enigma import eTimer
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.Setup import Setup
from Screens.VirtualKeyBoard import VirtualKeyboard
from twisted.internet.threads import deferToThread

from .i18n import _
from .twitch import CHAT_POSITIONS, channel_name, device_code, device_token


class DeviceLogin(Screen):
    skin = (Path(__file__).with_name("skins") / "device-login.xml").read_text(encoding="utf-8")

    def __init__(self, session):
        super().__init__(session)
        self.skinName = "TwitchDeviceLogin"
        self.setTitle(_("Connect Twitch"))
        self["prompt"] = Label(_("Requesting device code…"))
        self["code"] = Label()
        self["status"] = Label(_("Cancel"))
        self["actions"] = ActionMap(["OkCancelActions"], {"cancel": self.close}, -1)

        self.timer = eTimer()
        self.timer.callback.append(self.poll)
        self.onFirstExecBegin.append(self.start)
        self.onClose.append(self.timer.stop)

    def start(self):
        deferToThread(device_code).addCallbacks(self.show_code, self.failed)

    def show_code(self, code):
        if not getattr(self, "execing", False):
            return

        self.code = code["device_code"]
        self.deadline = monotonic() + code["expires_in"]
        self.interval = max(1, code.get("interval", 5))

        self["prompt"].setText(_("Open {url} on your phone or computer").format(url=code["verification_uri"]))
        self["code"].setText(code["user_code"])
        self.schedule()

    def schedule(self):
        """Wake at the next poll interval or when the device code expires."""
        remaining = self.deadline - monotonic()
        self.timer.start(max(1, int(1000 * min(self.interval, remaining))), True)

    def poll(self):
        if monotonic() >= self.deadline:
            self.error(_("Device code expired. Open Connect Twitch again."))
        else:
            deferToThread(device_token, self.code).addCallbacks(self.authorized, self.poll_failed)

    def authorized(self, tokens):
        if getattr(self, "execing", False):
            self.close(tokens)

    def poll_failed(self, failure):
        if not getattr(self, "execing", False):
            return

        message = failure.getErrorMessage()
        if message == "slow_down":
            self.interval += 5
        elif message != "authorization_pending":
            self.failed(failure)
            return
        self.schedule()

    def failed(self, failure):
        if getattr(self, "execing", False):
            self.error(_("Twitch login failed. Open Connect Twitch to retry."))

    def error(self, message):
        self["prompt"].setText(message)
        self["code"].setText("")
        self["status"].setText(_("Close"))


class CustomChannels(Screen):
    skin = (Path(__file__).with_name("skins") / "custom-channels.xml").read_text(encoding="utf-8")

    def __init__(self, session, library):
        super().__init__(session)
        self.skinName = "TwitchCustomChannels"
        self.library = library
        self.setTitle(_("Custom channels"))
        self["channels"] = MenuList(list(library.prefs.channels))
        self["empty"] = Label(_("No custom channels. Press Green to add one."))
        self["key_red"] = Label(_("Remove"))
        self["key_green"] = Label(_("Add"))
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.close, "green": self.add, "red": self.remove,
        }, -1)
        self.onLayoutFinish.append(self.update)

    def update(self):
        self["channels"].setList(self.library.prefs.channels)
        self["empty"].setVisible(not self.library.prefs.channels)

    def add(self):
        self.session.openWithCallback(self.added, VirtualKeyboard, title=_("Twitch channel name"))

    def added(self, text=None):
        if text is None:
            return
        name = channel_name(text)
        if not name:
            self.message(_("Enter a channel name using letters, digits or underscores."))
        elif name in self.library.prefs.channels:
            self.message(_("This channel is already on your list."))
        else:
            self.save(self.library.prefs.channels + [name])

    def remove(self):
        name = self["channels"].getCurrent()
        if name:
            self.session.openWithCallback(
                lambda yes: self.removed(name, yes), MessageBox, _("Remove {name}?").format(name=name),
                type=MessageBox.TYPE_YESNO, default=False,
            )

    def removed(self, name, yes):
        if yes:
            self.save([n for n in self.library.prefs.channels if n != name])

    def save(self, channels):
        try:
            self.library.prefs.save(channels, self.library.prefs.position)
        except OSError:
            self.message(_("Could not save Twitch settings."))
            return
        self.library.changed()
        self.update()

    def message(self, text):
        self.session.open(MessageBox, text, type=MessageBox.TYPE_INFO)


class Settings(Screen, ConfigListScreen):
    skin = Setup.skin

    def __init__(self, session, library):
        Screen.__init__(self, session, mandatoryWidgets=["config", "description", "footnote"], enableHelp=True)
        self.skinName = ["TwitchSettings", "Setup"]
        self.setImage("Setup", "setup")
        self.library = library
        self.setTitle(_("Twitch settings"))

        self.account = ConfigElement()
        self.channel_count = ConfigElement()
        self.position = ConfigSelection([(key, _(label)) for key, label in CHAT_POSITIONS.items()],
                                        default=library.prefs.position)
        self.account_entry = getConfigListEntry(_("Twitch account"), self.account,
                                               _("Press OK to manage your Twitch connection."))
        self.channels_entry = getConfigListEntry(_("Custom channels"), self.channel_count,
                                                _("Press OK to add or remove channels."))
        position_entry = getConfigListEntry(_("Chat position"), self.position,
                                            _("Use Left/Right to move the chat."))
        ConfigListScreen.__init__(
            self, [self.account_entry, self.channels_entry, position_entry],
            session=session, on_change=self.position_changed,
        )

        self["description"] = Label()
        self["footnote"] = Label()
        self["key_red"] = StaticText(_("Back"))
        self["settingsActions"] = ActionMap(["OkCancelActions", "ColorActions"],
                                           {"cancel": self.close, "red": self.close}, -1)

        self["config"].onSelectionChanged.append(self.show_description)
        self.library.listeners.append(self.update)
        self.onClose.append(lambda: self.library.listeners.remove(self.update))
        self.update()
        self.onLayoutFinish.append(self.show_description)

    def show_description(self):
        self["description"].setText(self.getCurrentDescription())

    def update(self):
        self.account.value = _("Connected") if self.library.account.tokens else _("Disconnected")
        self.channel_count.value = str(len(self.library.prefs.channels))
        self["config"].invalidate(self.account_entry)
        self["config"].invalidate(self.channels_entry)

    def handleInputHelpers(self):
        super().handleInputHelpers()
        self["menuConfigActions"].setEnabled(False)
        self["key_menu"].setText("")
        self["configActions"].setEnabledAction("select", self.getCurrentItem() is not self.position)

    def keySelect(self):
        item = self.getCurrentItem()
        if item is self.account:
            self.account_action()
        elif item is self.channel_count:
            self.session.open(CustomChannels, self.library)

    def position_changed(self):
        prefs = self.library.prefs
        if self.position.value != prefs.position:
            try:
                prefs.save(prefs.channels, self.position.value)
            except OSError:
                self.position.value = prefs.position
                self.message(_("Could not save Twitch settings."))

    def account_action(self):
        if self.library.account.tokens:
            self.session.openWithCallback(self.disconnect, MessageBox, _("Disconnect this Twitch account?"),
                                          type=MessageBox.TYPE_YESNO, default=False)
        else:
            self.session.openWithCallback(self.connected, DeviceLogin)

    def connected(self, tokens=None):
        if tokens:
            try:
                self.library.account.connect(tokens)
            except (KeyError, OSError):
                self.message(_("Could not save the Twitch login."))
                return
            self.library.changed(account=True)
            self.update()

    def disconnect(self, yes):
        if yes:
            try:
                self.library.account.disconnect()
            except OSError:
                self.message(_("Could not save Twitch settings."))
                return
            self.library.changed(account=True)
            self.update()

    def message(self, text):
        self.session.open(MessageBox, text, type=MessageBox.TYPE_INFO)
