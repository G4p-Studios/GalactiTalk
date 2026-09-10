from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

from galactitalk.bot import Connection, tt


class RoutingTests(unittest.TestCase):
    def test_mentions_match_old_chatgpt_forms(self):
        pattern = Connection._compile_mention("GalactiTalk")
        self.assertEqual(pattern.match("@[GalactiTalk] hello").group(1), "hello")
        self.assertEqual(pattern.match("@galactitalk: hello").group(1), "hello")
        self.assertIsNone(pattern.match("hello @GalactiTalk"))

    def test_rate_slash_routes_to_star(self):
        calls = []
        connection = SimpleNamespace(
            _is_banned=lambda _: False,
            _star_command=lambda user, command, arg: calls.append((user, command, arg)),
        )
        Connection._handle_private(connection, 3, "/rate 1.25")
        self.assertEqual(calls, [(3, "rate", "1.25")])

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
