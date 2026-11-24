from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlsplit

from . import _

PLUGIN = Path(__file__).resolve().parent
RUNTIME = PLUGIN / ".runtime"
PROFILE = Path(os.getenv("ENIGMA2_JOYN_PROFILE", "/etc/enigma2/joyn"))
LIVE = "?mode=channels&stream_type=LIVE"
MARKUP = re.compile(r"\[/?(?:B|I|COLOR(?: [^\]]+)?|UPPERCASE|LOWERCASE)\]", re.IGNORECASE)
CHANNEL_LABEL = re.compile(r"^\[B\](.+?)\[/B\](.*)$", re.IGNORECASE)
URL = re.compile(r"(?:https?|plugin)://\S+")
WATCHLIST_ACTIONS = {"add_fav", "drop_fav", "add_joyn_bookmark", "remove_joyn_bookmark"}


class JoynError(Exception):
    pass


def clean(value):
    return MARKUP.sub("", str(value or "")).replace("[CR]", " ").strip()


def redact(value):
    text = clean(value)
    return URL.sub("[URL]", text.splitlines()[0])[:190] if text else ""


def route(query):
    if query.startswith("?") or not query:
        return query
    url = urlsplit(query)
    if url.scheme == "plugin" and url.netloc == "plugin.video.joyn":
        return "?" + url.query
    raise JoynError(_("Unsupported Joyn link."))


def search_query(text):
    text = (text or "").strip()
    return "?" + urlencode({"mode": "search", "search_term": text}) if text else ""


def is_search_prompt(query):
    params = parse_qs(query.lstrip("?"))
    return params.get("mode") == ["search"] and "search_term" not in params


def number(value):
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return None


def clock(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def runtime(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


@dataclass(frozen=True)
class Programme:
    start: int
    end: int
    title: str
    subtitle: str = ""

    def next_label(self):
        return _("Next  {time} · {title}").format(time=clock(self.start), title=self.title)


def programme_schedule(events):
    schedule = []
    for event in events:
        start, end = number(event.get("start")), number(event.get("end"))
        if start is not None and end is not None and 0 < start < end < 10_000_000_000:
            schedule.append(Programme(start, end, clean(event.get("title")), clean(event.get("subtitle"))))
    return tuple(sorted(schedule, key=lambda event: event.start))


@dataclass(frozen=True)
class Item:
    query: str
    title: str
    description: str = ""
    folder: bool = False
    live: bool = False
    duration: int | None = None
    channel: str = ""
    programme: str = ""
    schedule: tuple[Programme, ...] = ()
    channel_id: str = ""
    series: str = ""
    season: int | None = None
    episode: int | None = None
    year: int | None = None
    mode: str = ""

    def now_and_next(self, timestamp):
        current = next((event for event in self.schedule if event.start <= timestamp < event.end), None)
        following = next((event for event in self.schedule if event.start > timestamp), None)
        return current, following

    def programme_title(self, current):
        if current:
            return current.title
        # The title in the channel label is only current when the listing is fetched.
        return "" if self.schedule else self.programme

    def video_metadata(self):
        parts = [self.series] if self.series and self.series != self.title else []
        if self.season is not None:
            parts.append(_("Season {number}").format(number=self.season))
        if self.episode is not None:
            parts.append(_("Episode {number}").format(number=self.episode))
        if self.year:
            parts.append(str(self.year))
        return "  ·  ".join(parts)

    @classmethod
    def from_provider(cls, data):
        query = route(data.get("query") or data.get("url", ""))
        args = parse_qs(query.lstrip("?"))
        info = data.get("info", {})
        duration = number(data.get("properties", {}).get("TotalTime")) or number(info.get("duration"))

        label = data.get("label") or data.get("title") or ""
        title = clean(label)
        live = args.get("stream_type") == ["LIVE"]
        channel = programme = ""
        if live:
            marked = CHANNEL_LABEL.match(label)
            if marked:
                channel = clean(marked[1])
                programme = clean(marked[2]).removeprefix("-").strip()
            else:
                channel, _, programme = title.partition(" - ")

        return cls(
            query=query, title=title, description=clean(data.get("plot")),
            folder=bool(data.get("is_folder")), live=live,
            duration=duration if duration and duration > 0 else None,
            channel=channel, programme=programme,
            schedule=programme_schedule(data.get("epg", [])) if live else (),
            channel_id=args.get("video_id", [""])[0] if live else "",
            series=clean(info.get("tvshowtitle")), season=number(info.get("season")),
            episode=number(info.get("episode")), year=number(info.get("year")),
            mode=args.get("mode", [""])[0],
        )


class Catalog:
    def __init__(self):
        self.lock = Lock()

    def request(self, query, responses=()):
        script = PLUGIN / "backend/joyn.py"
        addon = RUNTIME / "addons/plugin.video.joyn"
        if not addon.is_dir():
            raise JoynError(_("Joyn runtime missing. Build or reinstall the Joyn package."))
        command = [sys.executable, str(script), str(addon), str(PROFILE)]
        request = json.dumps({"query": route(query), "responses": responses})
        try:
            with self.lock:
                PROFILE.mkdir(mode=0o700, parents=True, exist_ok=True)
                result = subprocess.run(command, input=request, capture_output=True,
                                        text=True, timeout=50, check=False)
            payload = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise JoynError(_("Joyn could not be reached. Please try again.")) from error
        if result.returncode or payload.get("error"):
            detail = redact(payload.get("error"))
            raise JoynError(detail or _("Joyn could not complete the request."))
        return payload

    @staticmethod
    def items(payload, catalog_root=False):
        items = []
        for data in payload.get("items", []):
            try:
                item = Item.from_provider(data)
            except JoynError:
                continue
            if item.title and item.mode not in WATCHLIST_ACTIONS and not (catalog_root and item.live):
                items.append(item)
        return items
