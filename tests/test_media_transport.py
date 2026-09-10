from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from galactitalk.media_transport import PulseAudioMediaPlayer


class MediaTransportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
