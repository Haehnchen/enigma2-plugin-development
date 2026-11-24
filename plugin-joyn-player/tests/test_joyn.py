import importlib
from pathlib import Path
import socket
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

# The source directory uses a hyphen. The installed extension is named JoynPlayer.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
catalog = importlib.import_module("plugin-joyn-player.catalog")
playback = importlib.import_module("plugin-joyn-player.playback")


class CatalogTests(unittest.TestCase):
    def test_live_folder_is_separate_from_catalog(self):
        data = {"items": [
            {"query": catalog.LIVE, "label": "Live TV", "is_folder": True},
            {"query": "?mode=categories", "label": "[B]Series[/B]", "is_folder": True},
            {"url": "plugin://another.addon/?mode=channels", "label": "Unsupported"},
        ]}
        self.assertEqual([item.title for item in catalog.Catalog.items(data, catalog_root=True)], ["Series"])
        self.assertTrue(catalog.Catalog.items(data)[0].live)

    def test_search_term_is_a_route_parameter(self):
        term = "Joko & Klaas / Überraschung"
        self.assertEqual(parse_qs(catalog.search_query(f" {term} ")[1:]),
                         {"mode": ["search"], "search_term": [term]})
        self.assertFalse(catalog.search_query(" "))
        self.assertTrue(catalog.is_search_prompt("?mode=search&stream_type=VOD"))
        self.assertFalse(catalog.is_search_prompt(catalog.search_query(term)))

    def test_live_row_separates_the_current_programme(self):
        item = catalog.Item.from_provider({
            "query": "?mode=play_video&stream_type=LIVE",
            "label": "[B]Das Erste HD[/B] - Wer weiß denn sowas? - Spezial",
        })
        self.assertEqual(item.channel, "Das Erste HD")
        self.assertEqual(item.programme, "Wer weiß denn sowas? - Spezial")

    def test_episode_route_and_duration(self):
        item = catalog.Item.from_provider({"url": "plugin://plugin.video.joyn/?mode=play_video&stream_type=VOD",
                                         "label": "Episode", "properties": {"TotalTime": "120.5"},
                                         "info": {"tvshowtitle": "Series", "season": 2, "episode": 4}})
        self.assertEqual(item.duration, 120)
        self.assertEqual((item.series, item.season, item.episode), ("Series", 2, 4))
        self.assertFalse(item.live)
        self.assertFalse(item.folder)

    def test_epg_recognises_the_boundary_and_ignores_invalid_times(self):
        item = catalog.Item.from_provider({
            "query": "?mode=play_video&stream_type=LIVE&video_id=channel",
            "label": "[B]Example[/B] - Now",
            "epg": [{"start": 200, "end": 300, "title": "Next"},
                    {"start": 100, "end": 200, "title": "Now", "subtitle": "Episode"},
                    {"start": None, "end": 100, "title": "Incomplete"}],
        })
        self.assertEqual(item.channel_id, "channel")
        self.assertEqual(tuple(event.title for event in item.now_and_next(199)), ("Now", "Next"))
        current, following = item.now_and_next(200)
        self.assertEqual(current.title, "Next")
        self.assertIsNone(following)
        self.assertEqual(item.now_and_next(300), (None, None))

    def test_watchlist_actions_are_filtered_by_route(self):
        data = {"items": [
            {"query": "?mode=add_fav", "label": "Custom translation"},
            {"query": "?mode=drop_fav", "label": "Remove from watchlist"},
            {"query": "?mode=play_video", "label": "Episode"},
        ]}
        self.assertEqual([item.title for item in catalog.Catalog.items(data)], ["Episode"])


class PlaybackTests(unittest.TestCase):
    def test_helper_and_http_relay_close_together(self):
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "inputstream.adaptive").mkdir()
            cdm = runtime / "cdm"
            cdm.touch()
            binary = runtime / "dash2ts"
            binary.write_text(f"""#!{sys.executable}
import socket, sys, time
port = int(sys.argv[sys.argv.index('-p') + 1])
assert '-f' in sys.argv and sys.argv[sys.argv.index('-s') + 1] == '32'
with socket.create_connection(('127.0.0.1', port)) as out:
    out.sendall((bytes([0x47]) + bytes(187)) * 3)
    time.sleep(12)
""")
            binary.chmod(stat.S_IRWXU)
            stream = {"url": "https://stream.example/manifest.mpd", "drm": "com.widevine.alpha",
                      "properties": {"inputstream.adaptive.license_key": "https://license.example"}}
            relay = playback.Transport(stream, position=32, on_demand=True)
            with patch.object(playback, "RUNTIME", runtime), patch.object(playback, "ensure_widevine", return_value=cdm):
                try:
                    url = relay.open()
                    port = relay.server.server_port
                    with urlopen(Request(url, method="HEAD"), timeout=2) as response:
                        self.assertEqual(response.headers.get_content_type(), "video/mp2t")
                    with self.assertRaises(HTTPError) as error:
                        urlopen(f"http://127.0.0.1:{port}/wrong-url", timeout=2)
                    with error.exception:
                        self.assertEqual(error.exception.code, 404)
                    with urlopen(url, timeout=2) as response:
                        self.assertEqual(response.read(188 * 3), (bytes([0x47]) + bytes(187)) * 3)
                finally:
                    relay.close()
                relay.close()
                self.assertIsNotNone(relay.child.poll())
                self.assertFalse(Path(relay.temp.name).exists())
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=1)

    def test_clear_stream_can_play_without_helper(self):
        relay = playback.Transport({"url": "https://example.invalid/stream.m3u8"})
        self.assertEqual(relay.open(), "https://example.invalid/stream.m3u8")
        self.assertIsNone(relay.child)
        relay.close()
