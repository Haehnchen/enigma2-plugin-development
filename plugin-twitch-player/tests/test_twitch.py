from datetime import datetime, timezone, timedelta
from gettext import translation
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from .. import twitch


class PreferencesTests(unittest.TestCase):
    def test_first_launch_and_deliberately_empty_list(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            prefs = twitch.Preferences(path)
            self.assertEqual((prefs.channels, prefs.position), (["eslcs"], "top-left"))
            prefs.save([], "middle-left")
            reopened = twitch.Preferences(path)
            self.assertEqual((reopened.channels, reopened.position), ([], "middle-left"))
            self.assertEqual((path / "channels.json").stat().st_mode & 0o777, 0o600)

    def test_quick_chat_key_cycles_clockwise_and_persists_every_step(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            for position in ("top-center", "top-right", "middle-right", "bottom-right",
                             "bottom-center", "bottom-left", "middle-left", "top-left"):
                prefs = twitch.Preferences(path)
                prefs.cycle_chat_position()
                self.assertEqual(twitch.Preferences(path).position, position)
                self.assertEqual(prefs.channels, ["eslcs"])

    def test_only_channel_names_round_trip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "channels.json").write_text(json.dumps({"channels": [
                "  EsLCS ", "eslcs", "alice_123", "https://twitch.tv/example", "foo\nJOIN #bar", None,
            ]}))
            self.assertEqual(twitch.Preferences(path).channels, ["eslcs", "alice_123"])
        self.assertEqual(twitch.bouquet_reference("SkylineTVlive"),
                         "4097:0:1:0:0:0:0:0:0:0:streamlink%3a//https%3a//www.twitch.tv/skylinetvlive")
        with self.assertRaises(ValueError):
            twitch.bouquet_reference("a/b")

    def test_german_catalog_includes_device_login_and_chat(self):
        language = translation("TwitchPlayer", Path(twitch.__file__).parent / "locale", languages=["de"])
        self.assertEqual(language.gettext("Connect Twitch"), "Twitch verbinden")
        self.assertEqual(language.gettext("Bottom right"), "Unten rechts")


class DisplayTests(unittest.TestCase):
    def test_compact_counts_and_thresholds(self):
        examples = {0: "0", 99: "99", 210: "210", 999: "999", 1000: "1.0k", 1100: "1.1k",
                    9949: "9.9k", 9999: "10.0k", 10_000: "10.0k", 12_700: "12.7k",
                    24_200: "24.2k", 99_949: "99.9k", 99_950: "100k", 242_000: "242k",
                    999_499: "999k", 999_500: "1.0M", 1_200_000: "1.2M", 24_200_000: "24.2M"}
        for count, expected in examples.items():
            with self.subTest(count=count):
                self.assertEqual(twitch.format_viewers(count), expected)

    def test_online_time_uses_stream_start_and_handles_missing_metadata(self):
        now = datetime(2026, 11, 1, 19, 0, tzinfo=timezone.utc)
        for minutes, expected in ((-1, "0m"), (59, "59m"), (60, "1h 0m"), (306, "5h 6m"), (1500, "25h 0m")):
            start = (now - timedelta(minutes=minutes)).astimezone(timezone(timedelta(hours=2)))
            row = twitch.Channel("eslcs", viewers=1100, started_at=twitch.parse_start(start.isoformat()))
            self.assertEqual(row.live_stats(now), "1.1k · " + expected)
        for value in (None, "", "unknown", "2026-01-01T10:00"):
            self.assertIsNone(twitch.parse_start(value))
        self.assertEqual(twitch.Channel("eslcs", viewers=0).live_stats(now), "0")
        self.assertEqual(twitch.Channel("eslcs").live_stats(now), "")

    def test_lcd_duration_advances_from_cached_start_in_whole_minutes(self):
        start = datetime(2026, 11, 1, 12, 3, 17, tzinfo=timezone.utc)
        row = twitch.Channel("eslcs", viewers=0, started_at=start)
        self.assertEqual(row.live_duration(start + timedelta(seconds=3599)), "59m")
        self.assertEqual(row.live_duration(start + timedelta(hours=1)), "1h 0m")
        self.assertEqual(twitch.Channel("eslcs", viewers=0).live_duration(start), "")


class TwitchTests(unittest.TestCase):
    def test_resolution_choices_use_playable_video_variants_without_aliases(self):
        low = Mock()
        low.to_url.return_value = "https://example.test/low.m3u8"
        high = Mock()
        high.to_url.return_value = "https://example.test/high.m3u8"
        unsupported = Mock()
        unsupported.to_url.side_effect = TypeError
        streamlink = Mock()
        streamlink.streams.return_value = {"audio_only": Mock(), "160p30": low, "480p30": unsupported,
                                           "1080p60": high, "worst": low, "best": high}
        with patch.dict("sys.modules", {"streamlink": streamlink}):
            self.assertEqual(twitch.stream_qualities("EsLCS"), [
                ("1080p60", high.to_url.return_value, True), ("160p30", low.to_url.return_value, False)])
        streamlink.streams.assert_called_once_with("https://www.twitch.tv/eslcs")

    @patch.object(twitch, "request_json")
    def test_device_login_uses_public_client_and_follow_scope(self, request):
        twitch.device_code()
        self.assertEqual(request.call_args.kwargs["form"], {"client_id": twitch.CLIENT_ID, "scopes": twitch.SCOPE})
        twitch.device_token("device")
        form = request.call_args.kwargs["form"]
        self.assertEqual((form["device_code"], form["grant_type"]), ("device", twitch.DEVICE_GRANT))

    @patch.object(twitch, "request_json")
    def test_stream_metadata_batched_at_requested_size(self, request):
        request.side_effect = [{"data": {"users": [
            {"login": "a0", "displayName": "A0", "avatar": "small", "profileImageURL": "large",
             "stream": {"title": "Final", "viewersCount": 0, "createdAt": "2026-11-01T12:00:00Z",
             "game": {"name": "Chess", "boxArtURL": "portrait-{width}x{height}.jpg"}, "previewImageURL": "image"}},
            {"login": "a1", "displayName": "A1", "stream": None, "profileImageURL": "avatar"}, None]}},
            {"data": {"users": []}}]
        rows = twitch.fetch_channels([f"a{i}" for i in range(101)], (432, 243), 30)
        self.assertTrue(rows["a0"].live)
        self.assertEqual((rows["a0"].game, rows["a0"].image), ("Chess", "image"))
        self.assertEqual((rows["a0"].avatar, rows["a0"].profile_image), ("small", "large"))
        self.assertEqual(rows["a0"].cover, "portrait-{width}x{height}.jpg")
        self.assertEqual(rows["a0"].started_at, datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
        self.assertFalse(rows["a1"].cover)
        self.assertFalse(rows["a1"].live)
        self.assertEqual(rows["a1"].image, "")
        payloads = [call.kwargs["payload"] for call in request.call_args_list]
        self.assertEqual([len(p["variables"]["logins"]) for p in payloads], [100, 1])
        self.assertIn("width:432,height:243", payloads[0]["query"])
        self.assertIn("avatar:profileImageURL(width:50)", payloads[0]["query"])

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.account = twitch.Account(Path(temporary.name))
        self.original = {"access_token": "old", "refresh_token": "refresh"}
        self.rotated = {"access_token": "new", "refresh_token": "rotated"}
        self.identity = {"user_id": "123", "client_id": twitch.CLIENT_ID, "scopes": [twitch.SCOPE], "expires_in": 600}
        self.account.connect(self.original)

    @patch.object(twitch, "request_json")
    def test_refresh_persisted_and_all_follow_pages_returned(self, request):
        request.side_effect = [twitch.TwitchError(401, "expired"), self.rotated, self.identity,
                               {"data": [{"broadcaster_login": "a"}], "pagination": {"cursor": "x+"}},
                               {"data": [{"broadcaster_login": "b"}], "pagination": {}}]
        self.assertEqual(self.account.followed(), ["a", "b"])
        last = request.call_args
        self.assertEqual(parse_qs(urlsplit(last.args[0]).query)["after"], ["x+"])
        self.assertEqual(last.kwargs["headers"]["Authorization"], "Bearer new")
        self.assertEqual(twitch.read_json(self.account.path), self.rotated)
        self.assertEqual(self.account.path.stat().st_mode & 0o777, 0o600)

    @patch.object(twitch, "request_json")
    def test_revoked_account_is_disconnected_and_transient_errors_preserve_credentials(self, request):
        request.side_effect = twitch.TwitchError(503, "offline")
        with self.assertRaises(twitch.TwitchError):
            self.account.followed()
        self.assertEqual(self.account.tokens, self.original)
        request.side_effect = [twitch.TwitchError(401, "expired"), twitch.TwitchError(400, "invalid refresh token")]
        with self.assertRaises(twitch.TwitchError):
            self.account.followed()
        self.assertFalse(self.account.path.exists())

    @patch.object(twitch, "request_json")
    def test_late_refresh_cannot_restore_disconnected_account(self, request):
        def respond(url, **kwargs):
            if url.endswith("validate"):
                raise twitch.TwitchError(401, "expired")
            self.account.disconnect()
            return self.rotated
        request.side_effect = respond
        self.assertEqual(self.account.followed(), [])
        self.assertFalse(self.account.path.exists())
