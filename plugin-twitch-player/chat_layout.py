from bisect import bisect_right
from collections import deque
import re
from typing import NamedTuple


FALLBACK_COLORS = (0xFF7F50, 0x9ACD32, 0x1E90FF, 0xFF69B4, 0xBA55D3,
                   0x00B5AD, 0xF2C94C, 0x7AA2FF, 0xFF8A65, 0x57D68D)
TWITCH_EMOTE = "https://static-cdn.jtvnw.net/emoticons/v2/{}/static/dark/1.0"
# Twemoji graphics by Twitter and contributors (https://github.com/jdecked/twemoji),
# licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
# ePicLoad rasterizes the vector directly at the view's requested size.
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@v17.0.2/assets/svg/{}.svg"
# Match the whole emoji, including skin tones, flags and joined characters.
PICTOGRAPH = r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u231A-\u231B\u23E9-\u23F3\u2B50\u2B55]"
EMOJI_PART = PICTOGRAPH + r"\uFE0F?[\U0001F3FB-\U0001F3FF]?"
EMOJI = re.compile(r"(?:[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\uFE0F?\u20E3|"
                   + EMOJI_PART + r"(?:\u200D" + EMOJI_PART + r")*(?:[\U000E0020-\U000E007E]+\U000E007F)?)")


class Span(NamedTuple):
    text: str
    color: int | None = None
    image: str = ""


def name_color(name, tag):
    if re.fullmatch(r"#[0-9a-fA-F]{6}", tag):
        rgb = int(tag[1:], 16)
        r, g, b = rgb >> 16, rgb >> 8 & 255, rgb & 255
        # Lift dark Twitch colors so the name is legible over the video.
        if 299 * r + 587 * g + 114 * b < 95000:
            return ((r + 255) // 2 << 16) | ((g + 255) // 2 << 8) | ((b + 255) // 2)
        return rgb
    value = 5381
    for byte in name.encode("utf-8"):
        value = (value * 33 + (byte if byte < 128 else byte - 256)) & 0xFFFFFFFF
    return FALLBACK_COLORS[value % len(FALLBACK_COLORS)]


def emoji_spans(text):
    cursor = 0
    for match in EMOJI.finditer(text):
        if cursor < match.start():
            yield Span(text[cursor:match.start()])
        emoji = match.group()
        asset = emoji if "\u200d" in emoji else emoji.replace("\ufe0f", "")
        key = "-".join(f"{ord(char):x}" for char in asset)
        yield Span(emoji, image=TWEMOJI.format(key))
        cursor = match.end()
    if cursor < len(text):
        yield Span(text[cursor:])


def title_line(text, width, measure, icon_size, *, ellipsis=False):
    """Position text and whole emoji on one line, preserving spaces between runs."""
    marker = measure("|")

    def text_width(value):
        return max(0, measure(value + "|") - marker)

    runs = []
    end = 0
    for span in emoji_spans(text):
        length = icon_size if span.image else text_width(span.text)
        runs.append((end, length, span))
        end += length
        if end > width:
            break
    if end <= width:
        return runs

    dots = min(width, text_width("…")) if ellipsis else 0
    limit = width - dots
    while runs and runs[-1][0] + runs[-1][1] > limit:
        x, length, span = runs.pop()
        if x < limit and not span.image:
            if ellipsis:
                count = bisect_right(range(len(span.text) + 1), limit - x,
                                     key=lambda n: text_width(span.text[:n])) - 1
                span = Span(span.text[:count])
                length = text_width(span.text)
            else:
                length = limit - x
            runs.append((x, length, span))
            break
    if dots:
        x = runs[-1][0] + runs[-1][1] if runs else 0
        runs.append((x, dots, Span("…")))
    return runs


def plain_spans(text):
    for span in emoji_spans(text):
        if span.image:
            yield span
        else:
            yield from (Span(word) for word in re.findall(r"\s+|\S+", span.text))


def message_spans(name, color, body, emotes):
    """Split the body at Twitch's inclusive emote offsets before processing Unicode emoji."""
    yield Span(name + ":", name_color(name, color))
    yield Span(" ")

    ranges = []
    for spec in emotes.split("/"):
        emote, _, positions = spec.partition(":")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", emote):
            continue
        for position in positions.split(","):
            if re.fullmatch(r"[0-9]{1,5}-[0-9]{1,5}", position):
                start, end = map(int, position.split("-"))
                if start <= end < len(body):
                    ranges.append((start, end + 1, emote))

    cursor = 0
    for start, end, emote in sorted(ranges):
        if start >= cursor:
            yield from plain_spans(body[cursor:start])
            yield Span(body[start:end], image=TWITCH_EMOTE.format(emote))
            cursor = end
    yield from plain_spans(body[cursor:])


def parse_message(line, login):
    tags = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        tags = dict(tag.partition("=")[::2] for tag in raw[1:].split(";"))

    prefix, _, tail = line.partition(" ")
    command, _, params = tail.partition(" ")
    target, _, body = params.partition(" :")
    if command == "PRIVMSG" and target.lower() == "#" + login and body:
        name = tags.get("display-name") or prefix.lstrip(":").partition("!")[0]
        if body.startswith("\x01ACTION ") and body.endswith("\x01"):
            body = body[8:-1]
        spans = message_spans(name.replace(r"\s", " "), tags.get("color", ""), body, tags.get("emotes", ""))
        return tuple(spans)


class ChatBuffer(deque):
    """Hold raw IRC frames until layout actually needs their text and emotes."""

    def newest_first(self, login):
        for index in range(len(self) - 1, -1, -1):
            message = self[index]
            if isinstance(message, bytes):
                message = parse_message(message.decode("utf-8", "replace"), login)
                self[index] = message
            if message:
                yield message


def wrap_message(spans, width, measure, icon_size, failed):
    lines = [[]]
    x = gap = 0
    for span in spans:
        if span.text.isspace():
            gap = measure(" ")
            continue

        image = span.image and span.image not in failed
        length = icon_size if image else measure(span.text)
        if x and x + gap + length > width:
            lines.append([])
            x = 0
        x += gap if x else 0
        gap = 0
        if image or length <= width:
            lines[-1].append((x, length, span))
            x += length
            continue

        # Long links and unbroken chat text can occupy more than one full line.
        piece = ""
        for char in span.text:
            if piece and measure(piece + char) > width:
                lines[-1].append((0, measure(piece), Span(piece, span.color)))
                lines.append([])
                piece = ""
            piece += char
        x = measure(piece)
        lines[-1].append((0, x, Span(piece, span.color)))
    return lines


def visible_lines(newest_first, capacity, width, measure, icon_size, failed):
    """Wrap recent messages until the viewport is full, then return lines in reading order."""
    lines = []
    for message in newest_first:
        lines = wrap_message(message, width, measure, icon_size, failed) + lines
        if len(lines) >= capacity:
            break
    return lines[-capacity:] if capacity else []
