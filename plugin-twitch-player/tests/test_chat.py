import unittest
from unittest.mock import patch

from .. import chat_layout
from ..chat_layout import ChatBuffer, Span, name_color, parse_message, visible_lines


def text_of(line):
    return "".join(span.text for _, _, span in line)


class ChatTests(unittest.TestCase):
    def test_stream_title_keeps_text_and_inline_emoji_in_order(self):
        title = "☀️ GEILER MANN GEILER TAG ☀️"
        sun = Span("☀️", image=chat_layout.TWEMOJI.format("2600"))
        self.assertEqual(list(chat_layout.emoji_spans(title)), [sun, Span(" GEILER MANN GEILER TAG "), sun])
        self.assertEqual(list(chat_layout.emoji_spans("A normal title")), [Span("A normal title")])

    def test_grid_title_ellipsis_preserves_whole_emoji_and_fits_the_card(self):
        def fitted(text, width):
            line = chat_layout.title_line(text, width, len, 2, ellipsis=True)
            self.assertTrue(all(x + size <= width for x, size, _ in line))
            return text_of(line)

        self.assertEqual(fitted("🦩🌴 Köln", 9), "🦩🌴 Köln")
        self.assertEqual(fitted("Fünf 🌴", 6), "Fünf …")
        self.assertEqual(fitted("A👩🏽‍💻BCDEF", 6), "A👩🏽‍💻BC…")
        self.assertEqual(fitted("🇩🇪🌴🦩", 4), "🇩🇪…")

    def test_irc_colors_emote_offsets_and_unicode(self):
        irc = (r"@display-name=Alice\sB;color=#FF69B4;emotes=25:9-13,2-6/bad:5-50 "
               ":alice!abc PRIVMSG #demo :😄 Kappa  Kappa 👩🏽‍💻🇩🇪")
        spans = parse_message(irc, "demo")
        self.assertEqual(spans[0], Span("Alice B:", 0xFF69B4))
        self.assertEqual([s.text for s in spans if s.image], ["😄", "Kappa", "Kappa", "👩🏽‍💻", "🇩🇪"])
        self.assertEqual([s.image for s in spans if s.text == "Kappa"],
                         ["https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/1.0"] * 2)
        self.assertTrue(any(s.image.endswith("/svg/1f469-1f3fd-200d-1f4bb.svg") for s in spans))
        self.assertIsNone(parse_message(irc, "another"))
        self.assertEqual(name_color("Alice", ""), name_color("Alice", "garbage"))

    def test_action_and_malformed_emote_ranges_keep_the_text(self):
        irc = "@emotes=25:99-101/26:1-0 :alice!host PRIVMSG #demo :\x01ACTION hello world\x01"
        spans = parse_message(irc, "demo")
        self.assertEqual("".join(s.text for s in spans), "alice: hello world")
        self.assertFalse(any(s.image for s in spans))

    def test_scroll_keeps_latest_wrapped_lines_and_the_end_of_a_long_message(self):
        messages = [(Span("old"),), (Span("AB"), Span(" "), Span("CD")), (Span("1234567890"),)]
        lines = visible_lines(reversed(messages), 3, 4, len, 2, set())
        self.assertEqual([text_of(line) for line in lines], ["1234", "5678", "90"])
        messages.append((Span("new"),))
        self.assertEqual([text_of(line) for line in visible_lines(reversed(messages), 3, 4, len, 2, set())],
                         ["5678", "90", "new"])

    def test_burst_only_parses_the_visible_tail_and_reuses_it_on_repaint(self):
        messages = ChatBuffer(maxlen=8)
        with patch.object(chat_layout, "parse_message", wraps=parse_message) as parse:
            for i in range(10_000):
                messages.append(f":u!h PRIVMSG #demo :{i}".encode())
            self.assertEqual(len(messages), 8)
            self.assertEqual(parse.call_count, 0)
            for _ in range(2):
                lines = visible_lines(messages.newest_first("demo"), 8, 25, len, 2, set())
                self.assertEqual([line[-1][2].text for line in lines], [str(i) for i in range(9992, 10_000)])
                self.assertEqual(parse.call_count, 8)
            for _ in range(8):
                messages.append(b":u!h PRIVMSG #demo :a very long message")
            visible_lines(messages.newest_first("demo"), 2, 8, len, 2, set())
            self.assertEqual(parse.call_count, 9)

    def test_missing_emote_falls_back_to_its_entire_name(self):
        messages = [(Span("ok"), Span(" "), Span("Kappa", image="k"))]
        ready = visible_lines(reversed(messages), 3, 5, len, 2, set())
        self.assertEqual([(x, w) for x, w, _ in ready[0]], [(0, 2), (3, 2)])
        fallback = visible_lines(reversed(messages), 3, 5, len, 2, {"k"})
        self.assertEqual([text_of(line) for line in fallback], ["ok", "Kappa"])
