"""Twitch metadata, device authorization and local receiver settings."""

from dataclasses import dataclass
from datetime import datetime, timezone
from gzip import GzipFile
from json import dump, dumps, load, loads
from os import fdopen, replace, unlink
from pathlib import Path
import re
from tempfile import mkstemp
from threading import Lock
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4
from zlib import crc32


# Public application IDs used by twitch-player-2 for device login and public previews.
CLIENT_ID = "8l1fzyh4jhs1cxhtqs6p4swmxuejh6"
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
AUTH = "https://id.twitch.tv/oauth2/"
SCOPE = "user:read:follows"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
LOGIN = re.compile(r"[a-z0-9_]{1,25}", re.ASCII)
PLAYER_SCHEME = "twitch-player://"
DEFAULT_INTERVAL = 10
DEFAULT_STANDBY_INTERVAL = 60
INTERVAL_LIMITS = (1, 1440)
CHAT_POSITIONS = {
    "top-left": "Top left", "top-center": "Top center", "top-right": "Top right",
    "middle-right": "Middle right", "bottom-right": "Bottom right", "bottom-center": "Bottom center",
    "bottom-left": "Bottom left", "middle-left": "Middle left",
}
CHAT_CLOCKWISE = tuple(CHAT_POSITIONS)


def channel_name(value):
    if isinstance(value, str):
        name = value.strip().lower()
        if LOGIN.fullmatch(name):
            return name
    return ""


def channel_url(login):
    name = channel_name(login)
    if not name:
        raise ValueError("Invalid channel name")
    return "https://www.twitch.tv/" + name


def bouquet_reference(login):
    """Encode the Twitch URL in the service-reference format used by StreamlinkWrapper."""
    return "4097:0:1:0:0:0:0:0:0:0:" + ("streamlink://" + channel_url(login)).replace(":", "%3a")


def bouquet_login(path):
    return channel_name(path[len(PLAYER_SCHEME):]) if path.startswith(PLAYER_SCHEME) else ""


def player_reference(row):
    """Keep the EPG triplet stable across refreshes, reordering and channel renames."""
    user_id = row.user_id
    if not 0 <= user_id < 1 << 48 or not channel_name(row.login):
        raise ValueError("Invalid Twitch channel identity")
    onid = 0x7faa ^ (user_id >> 32)
    if not user_id:
        # A saved login can refer to an account Twitch no longer returns.
        user_id = crc32(row.login.encode("ascii"))
        onid = 0xfffe
    sid = user_id & 0xffff
    tsid = (user_id >> 16) & 0xffff
    return f"4097:0:1:{sid:X}:{tsid:X}:{onid:X}:0:0:0:0:twitch-player%3a//{row.login}"


def bouquet_sections(channels):
    online = sorted((row for row in channels if row.live), key=lambda row: row.viewers, reverse=True)
    offline = sorted((row for row in channels if not row.live),
                     key=lambda row: row.followers if row.followers is not None else -1, reverse=True)
    return online, offline


def stream_qualities(login):
    """Return playable video variants, highest quality first, with the best variant marked."""
    import streamlink

    streams = streamlink.streams(channel_url(login))
    best = streams.get("best")
    choices = []

    # Streamlink orders concrete stream names by increasing quality, then appends its aliases.
    for name, stream in reversed(streams.items()):
        if name in ("best", "worst", "best-unfiltered", "worst-unfiltered", "audio_only", "audio"):
            continue
        try:
            url = stream.to_url()
        except TypeError:  # Some stream types cannot be passed to the receiver as a URL.
            continue
        if url:
            choices.append((name, url, stream is best))
    return choices


def read_json(path):
    try:
        with path.open(encoding="utf-8") as file:
            data = load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    """Replace the settings file atomically using a private temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = mkstemp(dir=path.parent)
    try:
        with fdopen(fd, "w", encoding="utf-8") as file:
            dump(data, file)
        replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def interval_minutes(value, default):
    return value if type(value) is int and INTERVAL_LIMITS[0] <= value <= INTERVAL_LIMITS[1] else default


class Preferences:
    def __init__(self, directory):
        self.path = directory / "channels.json"
        saved = read_json(self.path)
        names = saved.get("channels")
        if not isinstance(names, list):
            names = ["eslcs"]
        self.channels = list(dict.fromkeys(filter(None, map(channel_name, names))))

        position = saved.get("chat_position")
        self.position = position if position in CHAT_CLOCKWISE else "top-left"
        self.interval = interval_minutes(saved.get("bouquet_interval"), DEFAULT_INTERVAL)
        self.standby_interval = interval_minutes(saved.get("bouquet_standby_interval"), DEFAULT_STANDBY_INTERVAL)
        owner = saved.get("bouquet_id")
        self.bouquet_id = owner if isinstance(owner, str) and re.fullmatch("[0-9a-f]{32}", owner) else ""
        self.bouquet_enabled = bool(self.bouquet_id) and saved.get("bouquet_enabled") is True

    def save(self, channels, position, *, interval=None, standby_interval=None, bouquet_enabled=None):
        minutes = self.interval if interval is None else interval
        standby = self.standby_interval if standby_interval is None else standby_interval
        enabled = self.bouquet_enabled if bouquet_enabled is None else bouquet_enabled
        owner = self.bouquet_id or (uuid4().hex if enabled else "")
        write_json(self.path, {"channels": channels, "chat_position": position, "bouquet_interval": minutes,
                               "bouquet_standby_interval": standby, "bouquet_enabled": enabled, "bouquet_id": owner})
        self.channels = list(channels)
        self.position = position
        self.interval = minutes
        self.standby_interval = standby
        self.bouquet_enabled = enabled
        self.bouquet_id = owner

    def cycle_chat_position(self):
        index = (CHAT_CLOCKWISE.index(self.position) + 1) % len(CHAT_CLOCKWISE)
        self.save(self.channels, CHAT_CLOCKWISE[index])


class TwitchError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request_json(url, *, headers=None, form=None, payload=None):
    body = None
    headers = dict(headers or {})
    if form is not None:
        body = urlencode(form).encode()
    elif payload is not None:
        body = dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    try:
        with urlopen(Request(url, body, headers), timeout=10) as response:
            return loads(response.read())
    except HTTPError as error:
        with error:
            try:
                message = loads(error.read(8192)).get("message", "")
            except (ValueError, AttributeError):
                message = ""
        raise TwitchError(error.code, message or "HTTP %d" % error.code) from None


def device_code():
    return request_json(AUTH + "device", form={"client_id": CLIENT_ID, "scopes": SCOPE})


def device_token(code):
    return request_json(AUTH + "token", form={
        "client_id": CLIENT_ID, "device_code": code, "scope": SCOPE, "grant_type": DEVICE_GRANT,
    })


class Account:
    """Write rotated credentials before using them. Ignore replies from a disconnected account."""

    def __init__(self, directory):
        self.path = directory / "account.json"
        self.lock = Lock()
        saved = read_json(self.path)
        self.tokens = saved if all(isinstance(saved.get(key), str) and saved[key]
                                   for key in ("access_token", "refresh_token")) else {}
        self.fetch_lock = Lock()
        self.identity = {}
        self.valid_until = 0

    def connect(self, tokens):
        with self.lock:
            self._save(tokens)

    def _save(self, tokens):
        pair = {key: tokens[key] for key in ("access_token", "refresh_token")}
        write_json(self.path, pair)
        self.tokens = pair
        self.identity = {}
        self.valid_until = 0

    def disconnect(self):
        with self.lock:
            self._clear()

    def _clear(self):
        self.path.unlink(missing_ok=True)
        self.tokens = {}
        self.identity = {}
        self.valid_until = 0

    def followed(self):
        # Refresh tokens from a public device client are single-use.
        with self.fetch_lock:
            return self._fetch()

    def _fetch(self):
        with self.lock:
            tokens = self.tokens
        if not tokens:
            return []

        try:
            return self._followed(tokens)
        except TwitchError as error:
            if error.status != 401:
                raise

        try:
            refreshed = request_json(AUTH + "token", form={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            })
        except TwitchError as error:
            if error.status in (400, 401, 403):
                with self.lock:
                    if self.tokens is tokens:
                        self._clear()
            raise

        # A disconnect or a new login can finish before the token request returns.
        with self.lock:
            if self.tokens is not tokens:
                return []
            self._save(refreshed)
            tokens = self.tokens
        return self._followed(tokens)

    def _followed(self, tokens):
        headers = {"Authorization": "Bearer " + tokens["access_token"], "Client-ID": CLIENT_ID}
        with self.lock:
            identity = self.identity if self.tokens is tokens and monotonic() < self.valid_until else {}
        if not identity:
            identity = request_json(AUTH + "validate", headers=headers)
            if identity.get("client_id") != CLIENT_ID or SCOPE not in (identity.get("scopes") or []):
                raise TwitchError(401, "Invalid account or scope")
            with self.lock:
                if self.tokens is not tokens:
                    return []
                self.identity = identity
                self.valid_until = monotonic() + min(3500, identity.get("expires_in", 0))

        params = {"first": 100, "user_id": identity["user_id"]}
        names = []
        while True:
            page = request_json("https://api.twitch.tv/helix/channels/followed?" + urlencode(params), headers=headers)
            names.extend(row["broadcaster_login"] for row in page["data"])
            cursor = page.get("pagination", {}).get("cursor")
            if not cursor or self.tokens is not tokens:
                return names if self.tokens is tokens else []
            params["after"] = cursor


def format_count(count, *, keep_decimal=False):
    if count < 1000:
        return str(count)
    # Switch units when rounding would produce 1000k.
    scale, suffix = (1000, "k") if count < 999_500 else (1_000_000, "M")
    tenths = (count * 10 + scale // 2) // scale
    if keep_decimal or tenths < 1000:
        return f"{tenths // 10}.{tenths % 10}{suffix}"
    return f"{(count + scale // 2) // scale}{suffix}"


def parse_start(value):
    if not isinstance(value, str):
        return None
    try:
        start = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return start if start.tzinfo is not None else None


@dataclass(frozen=True)
class Channel:
    login: str
    display: str = ""
    title: str = ""
    game: str = ""
    viewers: int | None = None
    image: str = ""
    avatar: str = ""
    profile_image: str = ""
    started_at: datetime | None = None
    cover: str = ""
    followers: int | None = None
    user_id: int = 0

    @property
    def live(self):
        return self.viewers is not None

    def live_duration(self, now=None):
        if not self.live or self.started_at is None:
            return ""

        elapsed = (now or datetime.now(timezone.utc)) - self.started_at
        hours, minutes = divmod(max(0, int(elapsed.total_seconds())) // 60, 60)
        return f"{hours}h {minutes}m" if hours else f"{minutes}m"

    def live_stats(self, now=None):
        if not self.live:
            return ""
        count = format_count(self.viewers)
        duration = self.live_duration(now)
        return f"{count} · {duration}" if duration else count

    def follower_count(self):
        return "" if self.followers is None else format_count(self.followers, keep_decimal=True)


def fetch_channels(names, size, avatar_width, *, errors=None):
    """Fetch channel metadata in batches with previews sized for the active skin."""
    width, height = size
    # Twitch accepts preset profile image sizes, independent of the receiver's OSD.
    avatar_size = next((n for n in (28, 50, 70) if n >= avatar_width), 150)
    query = (
        "query($logins:[String!]!){users(logins:$logins){id login displayName profileImageURL(width:150) "
        f"avatar:profileImageURL(width:{avatar_size}) "
        "followers{totalCount} "
        "stream{title viewersCount createdAt game{name boxArtURL} "
        f"previewImageURL(width:{width},height:{height})" + "}}}"
    )

    result = {}
    for offset in range(0, len(names), 100):
        data = request_json(
            "https://gql.twitch.tv/gql",
            headers={"Client-ID": WEB_CLIENT_ID},
            payload={"query": query, "variables": {"logins": names[offset:offset + 100]}},
        )
        users = (data.get("data") or {}).get("users")
        if not isinstance(users, list) or (data.get("errors") and not any(users)):
            raise TwitchError(200, "Twitch metadata unavailable")
        if errors is not None:
            errors.extend(data.get("errors") or [])

        for user in users:
            if not user:
                continue
            stream = user.get("stream") or {}
            game = stream.get("game") or {}
            login = user["login"].lower()
            result[login] = Channel(
                user_id=int(user.get("id") or 0),
                login=login, display=user["displayName"], title=stream.get("title", ""),
                game=game.get("name", ""), viewers=stream.get("viewersCount"),
                image=stream.get("previewImageURL") or "", avatar=user.get("avatar") or "",
                profile_image=user.get("profileImageURL") or "",
                started_at=parse_start(stream.get("createdAt")),
                cover=game.get("boxArtURL") or "",
                followers=(user.get("followers") or {}).get("totalCount"),
            )
    return result


def download_preview(url):
    """Fetch an image for ePicLoad, accepting compressed SVGs from the emoji CDN."""
    limit = 2 * 1024 * 1024
    svg = url.endswith(".svg")
    request = Request(url, headers={"Accept-Encoding": "gzip"}) if svg else url
    with urlopen(request, timeout=7) as response:
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            with GzipFile(fileobj=response) as source:
                data = source.read(limit + 1)
        else:
            data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("Unexpected thumbnail size")

    fd, path = mkstemp(prefix="twitch-preview-", suffix=".svg" if svg else "")
    try:
        with fdopen(fd, "wb") as file:
            file.write(data)
    except BaseException:
        unlink(path)
        raise
    return path
