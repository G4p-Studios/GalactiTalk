from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

from galactitalk.bot import COMMAND_HELP, Connection, tt


class RoutingTests(unittest.TestCase):
    def test_mentions_match_old_chatgpt_forms(self):
        pattern = Connection._compile_mention("GalactiTalk")
        self.assertEqual(pattern.match("@[GalactiTalk] hello").group(1), "hello")
        self.assertEqual(pattern.match("@galactitalk: hello").group(1), "hello")
        self.assertIsNone(pattern.match("hello @GalactiTalk"))

    def test_bare_rate_routes_to_star(self):
        calls = []
        connection = SimpleNamespace(
            _is_banned=lambda _: False,
            _star_command=lambda user, command, arg: calls.append((user, command, arg)),
        )
        Connection._handle_private(connection, 3, "rate 1.25")
        self.assertEqual(calls, [(3, "rate", "1.25")])

    def test_slash_commands_are_rejected(self):
        replies = []
        connection = SimpleNamespace(_is_banned=lambda _: False, _send_private=lambda user, text: replies.append(text))
        Connection._handle_private(connection, 3, "/gpt hello")
        self.assertIn("do not start with /", replies[0])

    def test_plain_private_text_is_not_a_prompt(self):
        replies = []
        connection = SimpleNamespace(
            _is_banned=lambda _: False, _media_commands=lambda: set(),
            _send_private=lambda user, text: replies.append(text),
        )
        Connection._handle_private(connection, 3, "tell me a story")
        self.assertIn("Unknown command", replies[0])

    def test_every_command_has_detailed_help(self):
        expected = Connection._media_commands() | {
            "help", "gpt", "say", "clear", "clear-all", "status", "restart", "quit",
            "voices", "voice", "rate", "pitch", "preview", "current", "stop",
        }
        self.assertEqual(set(COMMAND_HELP), expected)
        self.assertNotIn("h", COMMAND_HELP)
        self.assertTrue(all(text.startswith("Usage:") for text in COMMAND_HELP.values()))

    def test_native_media_playback_has_per_stream_gain(self):
        connection = SimpleNamespace(volume=50)
        playback = Connection._playback(connection, False, 0)
        self.assertEqual(playback.audioPreprocessor.nPreprocessor, tt.AudioPreprocessorType.TEAMTALK_AUDIOPREPROCESSOR)
        self.assertEqual(playback.audioPreprocessor.ttpreprocessor.nGainLevel, tt.SoundLevel.SOUND_GAIN_DEFAULT)

    def test_star_completion_releases_stream_for_queue(self):
        started = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            path.write_bytes(b"audio")
            connection = SimpleNamespace(
                elapsed_ms=0, duration_ms=0, star_current=(path, 7, "voice"),
                state="playing", current_track=None, preparing=False,
                _send_private=lambda *_: None,
                _start_waiting_stream=lambda: started.append(True),
            )
            info = SimpleNamespace(uElapsedMSec=1, uDurationMSec=1, nStatus=tt.MediaFileStatus.MFS_FINISHED)
            Connection.onStreamMediaFile(connection, info)
            self.assertEqual(connection.state, "stopped")
            self.assertFalse(path.exists())
            self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
