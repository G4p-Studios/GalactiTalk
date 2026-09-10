from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


_SOUND_SYSTEM_LOCK = threading.RLock()
_ACTIVE_PLAYERS: set["PulseAudioMediaPlayer"] = set()
_ROUTE_GUARD_MODULE_ID: int | None = None
_ROUTE_GUARD_SINK = f"galactitalk_route_guard_{os.getpid()}"


class PulseAudioMediaPlayer:
    """Route one MPV process into one temporary PulseAudio monitor source."""

    def __init__(self, teamtalk, tt_module, name: str,
                 on_exit: Callable[[int, int, str], None],
                 on_metadata: Callable[[int, str, bool], None] | None = None) -> None:
        self.teamtalk = teamtalk
        self.tt = tt_module
        self.name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:32] or "server"
        self.on_exit = on_exit
        self.on_metadata = on_metadata or (
            lambda _token, _title, _has_stream_metadata: None)
        self.process: subprocess.Popen | None = None
        self.module_id: int | None = None
        self.sink_name = ""
        self.socket_path: Path | None = None
        self.input_open = False
        self.voice_enabled = False
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

    @staticmethod
    def _load_null_sink(name: str, priority: int) -> int:
        result = subprocess.run(
            ["pactl", "load-module", "module-null-sink", f"sink_name={name}",
             "format=s16le", "rate=48000", "channels=2",
             "channel_map=front-left,front-right",
             (f"sink_properties=device.description={name} node.virtual=true "
              f"priority.session={priority}")],
            text=True, capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(
                f"could not create the temporary PulseAudio route: {result.stderr.strip()}")
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise RuntimeError(
                f"PulseAudio returned an invalid module ID for {name!r}") from exc

    @classmethod
    def _ensure_route_guard(cls) -> None:
        global _ROUTE_GUARD_MODULE_ID
        if _ROUTE_GUARD_MODULE_ID is None:
            # On a headless session the first null sink becomes the system
            # default. Keep unrelated playback on this silent guard instead of
            # letting it enter a bot's monitor source and be retransmitted.
            _ROUTE_GUARD_MODULE_ID = cls._load_null_sink(
                _ROUTE_GUARD_SINK, priority=1)

    @staticmethod
    def _release_route_guard_if_unused() -> None:
        global _ROUTE_GUARD_MODULE_ID
        if _ACTIVE_PLAYERS or _ROUTE_GUARD_MODULE_ID is None:
            return
        module_id, _ROUTE_GUARD_MODULE_ID = _ROUTE_GUARD_MODULE_ID, None
        subprocess.run(["pactl", "unload-module", str(module_id)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start(self, source: Path | str, volume: int, speed: float,
              maximum_volume: int, token: int) -> None:
        self.stop()
        self.ensure_server()
        self.token = token
        unique = f"{os.getpid()}_{token}_{time.monotonic_ns() & 0xfffffff:x}"
        self.sink_name = f"galactitalk_{self.name}_{unique}"
        try:
            with _SOUND_SYSTEM_LOCK:
                self._ensure_route_guard()
                self.module_id = self._load_null_sink(
                    self.sink_name, priority=0)
                _ACTIVE_PLAYERS.add(self)
                self._rescan_and_reopen_routes()

            ipc_dir = Path(tempfile.gettempdir())
            self.socket_path = ipc_dir / f"gt-{os.getpid()}-{token}-{unique[-7:]}.sock"
            self.socket_path.unlink(missing_ok=True)
            command = [
                "mpv", "--no-config", "--no-video", "--really-quiet", "--idle=no",
                f"--audio-device=pulse/{self.sink_name}",
                "--audio-samplerate=48000", "--audio-format=s16",
                "--audio-channels=stereo",
                f"--input-ipc-server={self.socket_path}",
                f"--volume={volume}", f"--volume-max={max(100, maximum_volume)}",
                f"--speed={speed:g}", "--", str(source),
            ]
            environment = os.environ.copy()
            environment["PULSE_SINK"] = self.sink_name
            executable_dir = str(Path(sys.executable).parent)
            environment["PATH"] = executable_dir + os.pathsep + environment.get("PATH", "")
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                            text=True, env=environment)
            self.voice_enabled = True
            self.teamtalk.enableVoiceTransmission(True)
            process = self.process
            threading.Thread(target=self._watch, args=(process, token),
                             name=f"mpv:{self.name}", daemon=True).start()
        except Exception:
            self.stop()
            raise

    def _close_input_for_rescan(self) -> None:
        if not self.input_open:
            return
        try:
            self.teamtalk.enableVoiceTransmission(False)
        except Exception:
            pass
        try:
            self.tt._CloseSoundInputDevice(self.teamtalk._tt)
        finally:
            self.input_open = False

    def _open_input(self) -> None:
        monitor = f"{self.sink_name}.monitor"
        device_id = self._find_monitor_device(monitor)
        if device_id is None:
            raise RuntimeError(f"TeamTalk did not discover temporary audio source {monitor}")
        if not self.teamtalk.initSoundInputDevice(device_id):
            raise RuntimeError(f"TeamTalk could not open temporary audio source {monitor}")
        self.input_open = True

    def _rescan_and_reopen_routes(self) -> None:
        routes = [player for player in _ACTIVE_PLAYERS
                  if player.module_id is not None and player.sink_name]
        previously_open = [player for player in routes if player.input_open]
        for player in previously_open:
            player._close_input_for_rescan()

        restarted = False
        for _ in range(10):
            if self.tt._RestartSoundSystem():
                restarted = True
                break
            time.sleep(0.1)
        if not restarted:
            # A failed restart leaves TeamTalk's previous device list intact, so
            # restore already-playing routes before reporting the new failure.
            for player in previously_open:
                try:
                    player._open_input()
                    player.teamtalk.enableVoiceTransmission(player.voice_enabled)
                except Exception as exc:
                    print(f"Could not restore audio route {player.sink_name}: {exc}")
            raise RuntimeError("TeamTalk could not rescan the newly created audio route after closing all active bot inputs")

        failures: list[str] = []
        for player in routes:
            try:
                player._open_input()
                player.teamtalk.enableVoiceTransmission(player.voice_enabled)
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("; ".join(failures))

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
        last_title = ""
        while process.poll() is None:
            metadata = self._request(["get_property", "metadata"])
            media_title = self._request(["get_property", "media-title"])
            metadata_title = self._parse_metadata(metadata)
            title = metadata_title or html.unescape(str(media_title or ""))
            if title and title != last_title:
                last_title = title
                self.on_metadata(token, title, bool(metadata_title))
            time.sleep(0.25)
        _, errors = process.communicate()
        error_lines = [line.strip() for line in (errors or "").splitlines() if line.strip()]
        self.on_exit(token, process.returncode, "\n".join(error_lines[-12:]))

    @staticmethod
    def _parse_metadata(metadata) -> str:
        stream_name = title = artist = ""
        if isinstance(metadata, dict):
            for key, raw_value in metadata.items():
                if raw_value is None:
                    continue
                value = html.unescape(str(raw_value))
                name = str(key)
                if name == "icy-name":
                    stream_name = value
                if "title" in name:
                    title = value
                if "artist" in name:
                    artist = value
        return " - ".join(value for value in (artist, title, stream_name) if value)

    @classmethod
    def _metadata_title(cls, metadata, media_title) -> str:
        return cls._parse_metadata(metadata) or html.unescape(str(media_title or ""))

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
        self.voice_enabled = not paused
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
        self.voice_enabled = False
        with _SOUND_SYSTEM_LOCK:
            if self.input_open:
                try:
                    self._close_input_for_rescan()
                except Exception:
                    pass
            module_id, self.module_id = self.module_id, None
            if module_id is not None:
                subprocess.run(["pactl", "unload-module", str(module_id)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _ACTIVE_PLAYERS.discard(self)
            self._release_route_guard_if_unused()
        if self.socket_path:
            self.socket_path.unlink(missing_ok=True)
            self.socket_path = None
        self.sink_name = ""
