"""Run the upstream Joyn add-on and return only the data consumed by Enigma2."""

from collections import deque
from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import sys
import time
from types import ModuleType, SimpleNamespace
from urllib.parse import parse_qs
import xml.etree.ElementTree as ET


ADDON_URL = "plugin://plugin.video.joyn/"
ADAPTIVE = "inputstream.adaptive."


def noop(*args, **kwargs):
    pass


def register(name, **attributes):
    module = ModuleType(name)
    vars(module).update(attributes)
    sys.modules[name] = module


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def addon_strings(path):
    if not path.is_file():
        return {}
    strings = {}
    for entry in path.read_text(encoding="utf-8").split("\n\n"):
        context = re.search(r'^msgctxt "#(\d+)"$', entry, re.M)
        if context:
            message = re.search(r'^msgstr (".*")$', entry, re.M)
            original = re.search(r'^msgid (".*")$', entry, re.M)
            translated = json.loads(message[1]) if message else ""
            strings[int(context[1])] = translated or (json.loads(original[1]) if original else "")
    return strings


class Addon:
    def __init__(self, folder, profile):
        manifest = ET.parse(folder / "addon.xml").getroot()
        assets = manifest.find("./extension[@point='xbmc.addon.metadata']/assets")
        self.info = {**manifest.attrib, "path": str(folder), "profile": str(profile)}
        if assets is not None:
            for key in ("fanart", "icon"):
                value = assets.findtext(key)
                if value:
                    self.info[key] = str(folder / value)

        self.settings_path = profile / "settings.json"
        settings = ET.parse(folder / "resources/settings.xml")
        self.settings = {
            entry.attrib["id"]: entry.findtext("default", entry.get("default", ""))
            for entry in settings.iter("setting") if entry.get("id")
        }
        self.saved = read_json(self.settings_path)
        self.strings = addon_strings(folder / "resources/language/resource.language.en_gb/strings.po")

    def getAddonInfo(self, key):
        return self.info.get(key, "")

    def getSetting(self, key):
        value = str(self.saved.get(key, self.settings.get(key, "")))
        return value.lower() if value.lower() in ("true", "false") else value

    def setSetting(self, id, value):
        self.saved[id] = str(value)
        self.settings_path.write_text(json.dumps(self.saved), encoding="utf-8")

    def getLocalizedString(self, number):
        return self.strings.get(int(number), str(number))

    openSettings = noop


class ListItem:
    def __init__(self, label="", **kwargs):
        self.label = label
        self.path = ""
        self.info = {}
        self.properties = {}

    def setPath(self, path):
        self.path = path or ""

    def setProperty(self, key, value):
        self.properties[key] = str(value)

    setArt = setMimeType = setContentLookup = addContextMenuItems = noop


class PendingDialog(BaseException):
    """Unwind the add-on so Enigma2 can collect the answer and replay the request."""


class Request:
    def __init__(self, query, responses):
        self.query = query
        self.responses = deque(responses)
        self.payload = {"items": [], "notifications": [], "dialogs": [], "container_updates": []}
        self.duration = None

    def prompt(self, flag, details):
        if self.responses and self.responses[0][0] == flag:
            return self.responses.popleft()[1]
        self.payload["dialogs"].append(details)
        raise PendingDialog

    def inputs(self, heading, defaultt="", type=0, option=0):
        return self.prompt("--input", {"kind": "input", "heading": heading, "default": defaultt,
                                      "input_type": type, "hidden": bool(option)})

    def notify(self, heading, message, *args):
        self.payload["notifications"].append({"heading": heading, "message": message})

    def confirm(self, heading, message, nolabel="No", yeslabel="Yes", **kwargs):
        answer = self.prompt("--select", {"kind": "select", "heading": f"{heading}: {message}",
                                         "options": [nolabel, yeslabel]})
        return answer == "1"

    def add_items(self, handle, items, *args):
        self.payload["items"].extend(
            {"url": url, "label": item.label, "plot": item.info.get("plot", ""),
             "info": {key: item.info[key] for key in ("duration", "tvshowtitle", "season", "episode", "year")
                      if key in item.info},
             "epg": item.info.get("joyn_epg", []), "properties": item.properties,
             "is_folder": folder}
            for url, item, folder in items
        )

    def resolve(self, handle, succeeded, item):
        self.payload["resolved"] = {"succeeded": bool(succeeded), "stream": {
            "url": item.path.partition("|")[0], "properties": item.properties,
            "drm": item.properties.get(ADAPTIVE + "license_type", ""),
            "duration": self.duration,
        }}

    def builtin(self, command):
        prefix, _, argument = command.partition("(")
        if prefix not in ("Container.Update", "RunPlugin"):
            return
        target = argument.removesuffix(")").removesuffix(",replace")
        if target.startswith(ADDON_URL):
            self.payload["container_updates"].append(target)

    def info_label(self, name):
        return {
            "System.BuildVersion": "21.0",
            "Container.CurrentItem": "0",
            "Container.FolderPath": ADDON_URL + self.query,
        }.get(name, "")

    def run(self, folder, profile):
        addon = Addon(folder, profile)
        sys.path.insert(0, str(folder))
        sys.argv = [ADDON_URL, "1", self.query]
        self.bind_addon(addon)

        try:
            from resources.lib import plugin

            plugin.xbmc_helper().set_videoinfo = set_video_info
            epg_metadata = plugin.lib_joyn.get_epg_metadata

            # Preserve event boundaries before Kodi formats the EPG as display text.
            def with_schedule(channel):
                metadata = epg_metadata(channel)
                metadata["infoLabels"]["joyn_epg"] = [
                    epg_event(event) for event in channel.get("epgEvents") or []
                ]
                return metadata

            plugin.lib_joyn.get_epg_metadata = staticmethod(with_schedule)
            if parse_qs(self.query.lstrip("?")).get("mode") == ["play_movie"]:
                self.capture_movie_duration()
            plugin.run(ADDON_URL, 1, self.query, addon)
        except PendingDialog:
            pass
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        return self.payload

    def capture_movie_duration(self):
        from resources.lib.submodules import libjoyn_video

        get_video_client_data = libjoyn_video.get_video_client_data

        def with_duration(asset_id, stream_type, asset_data=None):
            if asset_data:
                self.duration = (asset_data.get("video") or {}).get("duration")
            return get_video_client_data(asset_id, stream_type, asset_data or {})

        libjoyn_video.get_video_client_data = with_duration

    def bind_addon(self, addon):
        register(
            "xbmc", getCondVisibility=lambda condition: False, getInfoLabel=self.info_label,
            sleep=lambda ms: time.sleep(ms / 1000), getSkinDir=lambda: "",
            executeJSONRPC=rpc, executebuiltin=self.builtin, log=noop,
            LOGDEBUG=0, LOGINFO=1, LOGERROR=4,
        )

        def get_addon(addon_id=None):
            if addon_id is None:
                return addon
            return SimpleNamespace(getAddonInfo=lambda key: "3.0.0" if key == "version" else "")

        register("xbmcaddon", Addon=get_addon)
        register(
            "xbmcgui", ListItem=ListItem, INPUT_ALPHANUM=0, INPUT_NUMERIC=1,
            ALPHANUM_HIDE_INPUT=2, NOTIFICATION_ERROR="error", getCurrentWindowId=lambda: 10000,
            Window=lambda *args: SimpleNamespace(setProperty=noop),
            Dialog=lambda: SimpleNamespace(
                input=self.inputs, notification=self.notify, ok=self.notify, yesno=self.confirm,
            ),
        )

        sort_methods = ("UNSORTED", "LABEL", "DATE", "EPISODE", "DURATION", "TITLE", "DATEADDED")
        register(
            "xbmcplugin", addDirectoryItems=self.add_items, setResolvedUrl=self.resolve,
            setContent=noop, setPluginCategory=noop, endOfDirectory=noop, addSortMethod=noop,
            **{f"SORT_METHOD_{name}": i for i, name in enumerate(sort_methods)},
        )
        register(
            "xbmcvfs", exists=lambda path: Path(path).exists(), translatePath=str,
            mkdirs=lambda path: Path(path).mkdir(parents=True, exist_ok=True),
            listdir=list_directory, delete=delete_file,
        )

        # Transport checks the installed adaptive runtime and Widevine when opening the stream.
        register("inputstreamhelper", Helper=lambda *args, **kwargs:
                 SimpleNamespace(check_inputstream=lambda: True))


def set_video_info(item, info):
    item.info = info
    return item


def epg_event(event):
    programme = event.get("program") or {}
    return {"start": event.get("startDate"), "end": event.get("endDate"),
            "title": event.get("title") or programme.get("title"),
            "subtitle": event.get("secondaryTitle") or programme.get("secondaryTitle")}


def rpc(query):
    request = json.loads(query)
    result = {"value": False} if request.get("method") == "Settings.GetSettingValue" else {
        "addon": {"enabled": True}}
    return json.dumps({"jsonrpc": "2.0", "result": result, "id": request.get("id")})


def list_directory(path):
    candidates = list(Path(path).iterdir()) if Path(path).is_dir() else []
    return ([p.name.encode() for p in candidates if p.is_dir()],
            [p.name for p in candidates if p.is_file()])


def delete_file(path):
    Path(path).unlink(missing_ok=True)
    return True


def main():
    folder, profile = (Path(value).resolve() for value in sys.argv[1:])
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            payload = Request(request["query"], request["responses"]).run(folder, profile)
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
