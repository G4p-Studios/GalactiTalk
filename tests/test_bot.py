from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

from galactitalk.bot import COMMAND_HELP, Connection, tt


class RoutingTests(unittest.TestCase):
    def test_chatgpt_mention_is_literal_and_case_insensitive(self):
        pattern = Connection._compile_mention("GalactiTalk")
        self.assertEqual(pattern.match("@ChatGPT hello").group(1), "hello")
        self.assertEqual(pattern.match("@chatgpt: hello").group(1), "hello")
        self.assertIsNone(pattern.match("@[ChatGPT] hello"))
        self.assertIsNone(pattern.match("@GalactiTalk hello"))
        self.assertIsNone(pattern.match("hello @chatgpt"))

    def test_channel_chatgpt_mention_submits_text_directly(self):
        submitted = []
        connection = SimpleNamespace(
            my_id=99, parts={}, mention=Connection._compile_mention("Anything"),
            server=SimpleNamespace(name="one"),
            _submit_ai=lambda key, user, prompt, reply: submitted.append((key, user, prompt)),
            _send_channel=lambda *_: None,
        )
        message = SimpleNamespace(
            nFromUserID=7, nMsgType=tt.TextMsgType.MSGTYPE_CHANNEL,
            nChannelID=14, nToUserID=0, bMore=False,
            szMessage=tt.ttstr("@ChAtGpT explain this"),
        )
        Connection.onCmdUserTextMessage(connection, message)
        self.assertEqual(submitted, [("channel:one:14", 7, "explain this")])

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

    def test_star_completion_releases_stream_for_queue(self):
        started = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            path.write_bytes(b"audio")
            connection = SimpleNamespace(
                elapsed_ms=0, duration_ms=0, star_current=(path, 7, "voice"),
                _send_private=lambda *_: None,
                _start_waiting_stream=lambda: started.append(True),
            )
            info = SimpleNamespace(uElapsedMSec=1, uDurationMSec=1, nStatus=tt.MediaFileStatus.MFS_FINISHED)
            Connection.onStreamMediaFile(connection, info)
            self.assertFalse(path.exists())
            self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
