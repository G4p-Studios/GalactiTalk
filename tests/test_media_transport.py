from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from galactitalk.media_transport import PulseAudioMediaPlayer


class MediaTransportTests(unittest.TestCase):
    def test_metadata_title_matches_original_ttmediabot_order(self):
        title = PulseAudioMediaPlayer._metadata_title(
            {"icy-name": "Station &amp; Co", "icy-title": "Song", "icy-artist": "Artist"},
            "Fallback",
        )
        self.assertEqual(title, "Artist - Song - Station & Co")
        self.assertEqual(PulseAudioMediaPlayer._metadata_title({}, "Fallback &amp; More"),
                         "Fallback & More")

    def test_playback_creates_and_removes_an_isolated_route(self):
        device = SimpleNamespace(szDeviceName="temporary.monitor", szDeviceID="", nMaxInputChannels=2,
                                 nDeviceID=17)
        client = SimpleNamespace(
            getSoundDevices=lambda: [device],
            initSoundInputDevice=Mock(return_value=True),
            enableVoiceTransmission=Mock(),
            _tt="handle",
        )
        closed = []
        tt_module = SimpleNamespace(ttstr=str, _RestartSoundSystem=Mock(return_value=True),
                                    _CloseSoundInputDevice=lambda handle: closed.append(handle))
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0

        def run(command, **kwargs):
            if command[:3] == ["pactl", "load-module", "module-null-sink"]:
                sink = next(item.split("=", 1)[1] for item in command if item.startswith("sink_name="))
                device.szDeviceName = sink + ".monitor"
                return SimpleNamespace(returncode=0, stdout="42\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("galactitalk.media_transport.subprocess.run", side_effect=run) as runner, \
                patch("galactitalk.media_transport.subprocess.Popen", return_value=process) as popen, \
                patch("galactitalk.media_transport.threading.Thread") as thread:
            player = PulseAudioMediaPlayer(client, tt_module, "my server", lambda *_: None)
            player.start(Path("song.opus"), 55, 1.25, 150, 8)
            command = popen.call_args.args[0]
            self.assertTrue(any(item.startswith("--audio-device=pulse/galactitalk_my_server_") for item in command))
            self.assertIn("--volume=55", command)
            self.assertIn("--speed=1.25", command)
            client.initSoundInputDevice.assert_called_once_with(17)
            tt_module._RestartSoundSystem.assert_called_once_with()
            client.enableVoiceTransmission.assert_called_with(True)
            thread.return_value.start.assert_called_once()

            player.stop()
            process.terminate.assert_called_once()
            self.assertEqual(closed, ["handle"])
            unloads = [call.args[0] for call in runner.call_args_list
                       if call.args[0][:2] == ["pactl", "unload-module"]]
            self.assertEqual(unloads, [["pactl", "unload-module", "42"]])
            self.assertEqual(player.sink_name, "")

    def test_starting_a_second_route_closes_rescans_and_restores_first(self):
        devices = []
        clients = []
        closed = []

        def client(handle):
            value = SimpleNamespace(
                getSoundDevices=lambda: devices,
                initSoundInputDevice=Mock(return_value=True),
                enableVoiceTransmission=Mock(),
                _tt=handle,
            )
            clients.append(value)
            return value

        tt_module = SimpleNamespace(
            ttstr=str,
            _RestartSoundSystem=Mock(return_value=True),
            _CloseSoundInputDevice=lambda handle: closed.append(handle),
        )
        module_ids = iter((41, 42))

        def run(command, **kwargs):
            if command[:3] == ["pactl", "load-module", "module-null-sink"]:
                sink = next(item.split("=", 1)[1] for item in command if item.startswith("sink_name="))
                devices.append(SimpleNamespace(szDeviceName=sink + ".monitor", szDeviceID="",
                                               nMaxInputChannels=2, nDeviceID=len(devices) + 20))
                return SimpleNamespace(returncode=0, stdout=f"{next(module_ids)}\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        processes = [Mock(), Mock()]
        for process in processes:
            process.poll.return_value = None
        with patch("galactitalk.media_transport.subprocess.run", side_effect=run), \
                patch("galactitalk.media_transport.subprocess.Popen", side_effect=processes), \
                patch("galactitalk.media_transport.threading.Thread"):
            first = PulseAudioMediaPlayer(client("first"), tt_module, "first", lambda *_: None)
            second = PulseAudioMediaPlayer(client("second"), tt_module, "second", lambda *_: None)
            first.start(Path("one.opus"), 50, 1, 100, 1)
            second.start(Path("two.opus"), 50, 1, 100, 2)

            self.assertEqual(tt_module._RestartSoundSystem.call_count, 2)
            self.assertIn("first", closed)
            self.assertEqual(clients[0].initSoundInputDevice.call_count, 2)
            clients[0].enableVoiceTransmission.assert_called_with(True)
            self.assertTrue(first.input_open)
            self.assertTrue(second.input_open)

            second.stop()
            first.stop()


if __name__ == "__main__":
    unittest.main()
