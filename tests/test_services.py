from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from galactitalk.config import MediaSettings
from galactitalk.services import Track, YouTubeService


class YouTubeServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = YouTubeService(MediaSettings(search_results=300))

    def tearDown(self):
        self.service.close()

    def test_search_uses_original_lightweight_search_client(self):
        callback = Mock()
        with patch("galactitalk.services.VideosSearch") as search, \
                patch("galactitalk.services.YoutubeDL") as ytdl:
            search.return_value.result.return_value = {
                "result": [{"link": "https://youtube.test/watch?v=1", "title": "First"}]
            }
            self.service._search_done("query", callback)
        search.assert_called_once_with("query", limit=300, timeout=10)
        ytdl.assert_not_called()
        tracks = callback.call_args.args[1]
        self.assertEqual((tracks[0].source, tracks[0].name),
                         ("https://youtube.test/watch?v=1", "First"))

    def test_prepare_resolves_stream_without_downloading_media(self):
        ydl = Mock()
        ydl.extract_info.return_value = {
            "title": "Resolved", "url": "https://cdn.test/audio.m4a", "is_live": False,
        }
        manager = Mock()
        manager.__enter__ = Mock(return_value=ydl)
        manager.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, \
                patch("galactitalk.services.YoutubeDL", return_value=manager):
            source, live = self.service.prepare(
                Track("https://youtube.test/watch?v=1"), Path(directory))
        self.assertEqual(source, "https://cdn.test/audio.m4a")
        self.assertFalse(live)
        ydl.extract_info.assert_called_once_with("https://youtube.test/watch?v=1", download=False)

    def test_single_youtube_url_is_not_inspected_twice(self):
        callback = Mock()
        with patch("galactitalk.services.YoutubeDL") as ytdl:
            self.service._url_done("https://www.youtube.com/watch?v=one", callback)
        ytdl.assert_not_called()
        error, tracks = callback.call_args.args
        self.assertIsNone(error)
        self.assertEqual(tracks[0].source, "https://www.youtube.com/watch?v=one")

    def test_rejected_cookie_file_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cookiefile = Path(directory) / "cookies.txt"
            cookiefile.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            service = YouTubeService(MediaSettings(cookiefile_path=str(cookiefile)))
            ydl = Mock()
            ydl.extract_info.side_effect = RuntimeError("Sign in to confirm you're not a bot")
            manager = Mock()
            manager.__enter__ = Mock(return_value=ydl)
            manager.__exit__ = Mock(return_value=False)
            try:
                with patch("galactitalk.services.YoutubeDL", return_value=manager), \
                        self.assertRaisesRegex(RuntimeError, "rejected the configured cookies"):
                    service.prepare(Track("https://youtube.test/watch?v=1"), Path(directory))
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
