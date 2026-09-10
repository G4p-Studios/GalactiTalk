from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Callable


_SOUND_SYSTEM_LOCK = threading.RLock()


class PulseAudioMediaPlayer:
    """Route one MPV process into one temporary PulseAudio monitor source."""

    def __init__(self, teamtalk, tt_module, name: str,
                 on_exit: Callable[[int, int], None]) -> None:
        self.teamtalk = teamtalk
        self.tt = tt_module
        self.name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:32] or "server"
        self.on_exit = on_exit
        self.process: subprocess.Popen | None = None
        self.module_id: int | None = None
        self.sink_name = ""
        self.socket_path: Path | None = None
        self.input_open = False
        self.token = 0

    @staticmethod
    def ensure_server() -> None:
        probe = subprocess.run(["pactl", "info"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "start", "pipewire-pulse.service"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["pactl", "info"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return
        if shutil.which("pulseaudio"):
            subprocess.run(["pulseaudio", "--start"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        if subprocess.run(["pactl", "info"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError("PulseAudio/PipeWire-Pulse is installed but no user audio server is available")

    def start(self, source: Path | str, volume: int, speed: float,
              maximum_volume: int, token: int) -> None:
        self.stop()
        self.ensure_server()
        self.token = token
        unique = f"{os.getpid()}_{token}_{time.monotonic_ns() & 0xfffffff:x}"
        self.sink_name = f"galactitalk_{self.name}_{unique}"
        try:
            with _SOUND_SYSTEM_LOCK:
                result = subprocess.run(
                    ["pactl", "load-module", "module-null-sink", f"sink_name={self.sink_name}",
                     f"sink_properties=device.description={self.sink_name}"],
                    text=True, capture_output=True,
                )
                if result.returncode:
                    raise RuntimeError(f"could not create the temporary PulseAudio route: {result.stderr.strip()}")
                self.module_id = int(result.stdout.strip())
                monitor = f"{self.sink_name}.monitor"
                if not self.tt._RestartSoundSystem():
                    raise RuntimeError("TeamTalk could not rescan the newly created audio route")
                device_id = self._find_monitor_device(monitor)
                if device_id is None:
                    raise RuntimeError(f"TeamTalk did not discover temporary audio source {monitor}")
                if not self.teamtalk.initSoundInputDevice(device_id):
                    raise RuntimeError(f"TeamTalk could not open temporary audio source {monitor}")
                self.input_open = True

            ipc_dir = Path(tempfile.gettempdir())
            self.socket_path = ipc_dir / f"gt-{os.getpid()}-{token}-{unique[-7:]}.sock"
            self.socket_path.unlink(missing_ok=True)
            command = [
                "mpv", "--no-config", "--no-video", "--really-quiet", "--idle=no",
                f"--audio-device=pulse/{self.sink_name}",
                f"--input-ipc-server={self.socket_path}",
                f"--volume={volume}", f"--volume-max={max(100, maximum_volume)}",
                f"--speed={speed:g}", "--", str(source),
            ]
            environment = os.environ.copy()
            environment["PULSE_SINK"] = self.sink_name
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            env=environment)
            self.teamtalk.enableVoiceTransmission(True)
            process = self.process
            threading.Thread(target=self._watch, args=(process, token),
                             name=f"mpv:{self.name}", daemon=True).start()
        except Exception:
            self.stop()
            raise

    def _find_monitor_device(self, monitor: str) -> int | None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            for device in self.teamtalk.getSoundDevices():
                name = str(self.tt.ttstr(device.szDeviceName))
                identifier = str(self.tt.ttstr(device.szDeviceID))
                if monitor in {name, identifier} and device.nMaxInputChannels > 0:
                    return int(device.nDeviceID)
            time.sleep(0.05)
        return None

    def _watch(self, process: subprocess.Popen, token: int) -> None:
        returncode = process.wait()
        self.on_exit(token, returncode)

    def _request(self, command: list, *, attempts: int = 1):
        path = self.socket_path
        if not path:
            return None
        payload = (json.dumps({"command": command}) + "\n").encode()
        for attempt in range(attempts):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(1)
                    client.connect(str(path))
                    client.sendall(payload)
                    data = b""
                    while b"\n" not in data:
                        part = client.recv(65536)
                        if not part:
                            break
                        data += part
                response = json.loads(data.splitlines()[0]) if data else {}
                return response.get("data") if response.get("error") == "success" else None
            except (OSError, ValueError, IndexError):
                if attempt + 1 < attempts:
                    time.sleep(0.05)
        return None

    def pause(self, paused: bool) -> bool:
        if self._request(["set_property", "pause", paused], attempts=5) is None:
            # Successful MPV set-property replies have a null data value, so verify state.
            state = self._request(["get_property", "pause"], attempts=2)
            if state is not paused:
                return False
        self.teamtalk.enableVoiceTransmission(not paused)
        return True

    def seek(self, seconds: float) -> bool:
        self._request(["seek", seconds, "relative", "exact"], attempts=3)
        return self.process is not None and self.process.poll() is None

    def set_volume(self, volume: int) -> bool:
        self._request(["set_property", "volume", volume], attempts=3)
        return self.process is not None and self.process.poll() is None

    def set_speed(self, speed: float) -> bool:
        self._request(["set_property", "speed", speed], attempts=3)
        return self.process is not None and self.process.poll() is None

    def position_ms(self) -> int:
        position = self._request(["get_property", "time-pos"])
        try:
            return max(0, int(float(position) * 1000))
        except (TypeError, ValueError):
            return 0

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
        try:
            self.teamtalk.enableVoiceTransmission(False)
        except Exception:
            pass
        with _SOUND_SYSTEM_LOCK:
            if self.input_open:
                try:
                    close = getattr(self.teamtalk, "closeSoundInputDevice", None)
                    if close:
                        close()
                    else:
                        self.tt._CloseSoundInputDevice(self.teamtalk._tt)
                except Exception:
                    pass
                self.input_open = False
            module_id, self.module_id = self.module_id, None
            if module_id is not None:
                subprocess.run(["pactl", "unload-module", str(module_id)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.socket_path:
            self.socket_path.unlink(missing_ok=True)
            self.socket_path = None
        self.sink_name = ""
