from __future__ import annotations

import math
from pathlib import Path
import queue
import random
import re
import tempfile
import threading
import traceback
from typing import Callable
from urllib.parse import urlparse

from teamtalk_sdk import load_teamtalk_sdk

from .config import AppConfig, ServerSettings
from .media_transport import PulseAudioMediaPlayer
from .services import OpenAIService, StarPool, Track, YouTubeService


tt = load_teamtalk_sdk()
DONE_MEDIA = {tt.MediaFileStatus.MFS_FINISHED, tt.MediaFileStatus.MFS_ERROR, tt.MediaFileStatus.MFS_ABORTED}
MODES = {"st": "single track", "rt": "repeat track", "tl": "track list", "rtl": "repeat track list", "rnd": "random"}
GENDERS = {"m": 0, "f": 256, "n": 4096}

COMMAND_HELP = {
    "help": "Usage: help [command]. Shows the unified command list, or detailed usage and behavior for one command. Slash-prefixed forms and the old h alias are not accepted.",
    "gpt": "Usage: gpt PROMPT. Sends PROMPT to ChatGPT using your private conversation context. In a channel, use @chatgpt PROMPT (case-insensitive).",
    "say": "Usage: say TEXT. Synthesizes TEXT with your selected STAR voice and streams it into the bot's channel. You must be in that channel.",
    "clear": "Usage: clear. Clears only your private ChatGPT conversation context on this server.",
    "clear-all": "Usage: clear-all. Administrator-only. Clears every private and channel ChatGPT context across the process.",
    "status": "Usage: status. Shows ChatGPT model/context/queue state, STAR connectivity and voice count, and media playback state.",
    "restart": "Usage: restart. Administrator-only. Restarts the complete GalactiTalk process and all server connections.",
    "quit": "Usage: quit. Administrator-only. Stops the complete GalactiTalk process.",
    "voices": "Usage: voices [filter]. Lists available STAR voices, optionally restricted to names containing filter, and saves the numbered result list for voice NUMBER.",
    "voice": "Usage: voice NAME|NUMBER. Selects your STAR voice by exact/unique partial name or by number from your last voices result.",
    "rate": "Usage: rate NUMBER|none. Sets or clears the rate parameter attached to your STAR synthesis requests.",
    "pitch": "Usage: pitch NUMBER|none. Sets or clears the pitch parameter attached to your STAR synthesis requests.",
    "preview": "Usage: preview [voice]. Synthesizes the configured preview sentence using the named voice or your selected/default voice.",
    "current": "Usage: current. Shows your selected/default STAR voice and your current rate and pitch parameters.",
    "stop": "Usage: stop. Stops the current STAR stream and clears queued STAR audio for this server connection. Media playback uses the separate s command.",
    "a": "Usage: a. Shows a short description of GalactiTalk.",
    "p": "Usage: p QUERY, or p with no argument. With QUERY, searches YouTube and plays the first result. With no argument, pauses or resumes media playback.",
    "u": "Usage: u URL|PATH. Loads a supported URL or playlist. Administrators may also provide a local file or directory path.",
    "s": "Usage: s. Stops media playback and clears the current media track.",
    "b": "Usage: b. Plays the previous item in the current media list when the selected playback mode permits it.",
    "n": "Usage: n. Plays the next item in the current media list when the selected playback mode permits it.",
    "c": "Usage: c NUMBER. Plays a numbered item from the current media result or playlist; negative indexes count from the end.",
    "sb": "Usage: sb [SECONDS]. Seeks media backward by SECONDS, or by the configured default seek step when omitted.",
    "sf": "Usage: sf [SECONDS]. Seeks media forward by SECONDS, or by the configured default seek step when omitted.",
    "v": "Usage: v [VOLUME]. Shows the media volume when omitted, or sets per-stream gain between 0 and the configured maximum.",
    "sp": "Usage: sp [SPEED]. Shows playback speed when omitted, or changes the active MPV playback speed from 0.25 through 4.",
    "m": "Usage: m [MODE]. Shows or selects media mode: st single, rt repeat track, tl list, rtl repeating list, or rnd random.",
    "gl": "Usage: gl. Returns the original link or path for the current media track.",
    "dl": "Usage: dl. Downloads the current non-live track and uploads it to the TeamTalk channel when the bot account has upload rights.",
    "sv": "Usage: sv. Shows the active searchable media service. GalactiTalk intentionally provides only yt (YouTube/yt-dlp).",
}

HELP_OVERVIEW = (
    "GalactiTalk commands (no leading slash):\n"
    "Core: help, gpt, say, clear, clear-all, status, restart, quit\n"
    "STAR: voices, voice, rate, pitch, preview, current, stop\n"
    "Media: a, p, u, s, b, n, c, sb, sf, v, sp, m, gl, dl, sv\n"
    "Send help COMMAND for detailed usage. Plain text that does not begin with a command is ignored."
)


class RuntimeControl:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.restart = False

    def request_stop(self, restart: bool = False) -> None:
        self.restart = restart
        self.stop_event.set()


class Connection(tt.TeamTalk, threading.Thread):
    """One independent TeamTalk connection with an isolated media route."""

    def __init__(self, server: ServerSettings, config: AppConfig,
                 ai: OpenAIService | None, star: StarPool, youtube: YouTubeService,
                 control: RuntimeControl):
        tt.TeamTalk.__init__(self)
        threading.Thread.__init__(self, name=f"teamtalk:{server.name}", daemon=True)
        self.server, self.config = server, config
        self.ai, self.star, self.youtube, self.control = ai, star, youtube, control
        self.stop_event = threading.Event()
        self.actions: queue.Queue[Callable[[], None]] = queue.Queue()
        self.parts: dict[tuple[int, int, int, int], list] = {}
        self.reconnect_now = False
        self.my_id = 0
        self.channel_id = 0
        self.pending_ai = 0
        self.mention = self._compile_mention(server.nickname)
        self.user_voices: dict[str, str] = {}
        self.user_rates: dict[str, str] = {}
        self.user_pitches: dict[str, str] = {}
        self.voice_results: dict[str, list[str]] = {}
        self.tracks: list[Track] = []
        self.track_index = -1
        self.mode = config.media.default_mode
        self.volume = config.media.default_volume
        self.speed = 1.0
        self.state = "stopped"
        self.current_track: Track | None = None
        self.current_source: Path | str | None = None
        self.current_temp: tempfile.TemporaryDirectory | None = None
        self.elapsed_ms = 0
        self.duration_ms = 0
        self.preparing = False
        self.play_token = 0
        self.media_requester = 0
        self.star_queue: queue.Queue[tuple[Path, int, str]] = queue.Queue()
        self.star_current: tuple[Path, int, str] | None = None
        self.media_player = PulseAudioMediaPlayer(
            self, tt, server.name,
            lambda token, code, error: self.actions.put(
                lambda token=token, code=code, error=error: self._media_exited(token, code, error)),
            lambda token, title: self.actions.put(
                lambda token=token, title=title: self._media_metadata(token, title)),
        )

    @staticmethod
    def _compile_mention(nickname: str) -> re.Pattern[str]:
        del nickname  # Kept in the signature for compatibility with migrated callers.
        return re.compile(r"^\s*@chatgpt(?=$|[\s,:-])[\s,:-]*(.*)$", re.I | re.S)

    def stop(self) -> None:
        self.stop_event.set()
        self.actions.put(self._stop_all)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._reset_connection()
                print(f"Connecting {self.server.name}: {self.server.host}:{self.server.tcp_port}")
                if self.connect(tt.ttstr(self.server.host), self.server.tcp_port, self.server.udp_port,
                                bEncrypted=self.server.encrypted):
                    while not self.stop_event.is_set() and not self.reconnect_now:
                        self._drain_actions()
                        self.runEventLoop(100)
                    self.disconnect()
            except Exception:
                traceback.print_exc()
            if not self.stop_event.is_set():
                self.stop_event.wait(self.config.teamtalk.reconnect_seconds)
        self._stop_all()

    def _reset_connection(self) -> None:
        self.reconnect_now, self.my_id, self.channel_id = False, 0, 0
        self.parts.clear()

    def _drain_actions(self) -> None:
        while True:
            try:
                self.actions.get_nowait()()
            except queue.Empty:
                return

    def onConnectSuccess(self):
        self.doLogin(tt.ttstr(self.server.nickname), tt.ttstr(self.server.username),
                     tt.ttstr(self.server.password), tt.ttstr(self.config.teamtalk.client_name))

    def onConnectFailed(self):
        self.reconnect_now = True

    def onConnectCryptError(self, error):
        print(f"{self.server.name}: encryption error {tt.ttstr(error.szErrorMsg)}")
        self.reconnect_now = True

    def onConnectionLost(self):
        print(f"{self.server.name}: connection lost")
        self.reconnect_now = True

    def onCmdError(self, command_id, error):
        print(f"{self.server.name}: command {command_id} error {error.nErrorNo}: {tt.ttstr(error.szErrorMsg)}")

    def onCmdMyselfLoggedIn(self, user_id, account):
        self.my_id = user_id
        print(f"{self.server.name}: logged in as user {user_id}")
        self._update_status()
        self._join(self.server.channel, self.server.channel_password)

    def onCmdMyselfLoggedOut(self):
        self.my_id, self.channel_id = 0, 0

    def onCmdUserJoinedChannel(self, user):
        if user.nUserID == self.my_id:
            self.channel_id = user.nChannelID
            for command in self.server.start_commands:
                self._media_command(0, command, True, silent=True)

    def onCmdUserLeftChannel(self, channel_id, user):
        if user.nUserID == self.my_id:
            self.channel_id = 0

    def onCmdMyselfKickedFromChannel(self, channel_id, user):
        self.channel_id = 0

    def onCmdUserUpdate(self, user):
        if user.nUserID == self.my_id:
            self.channel_id = user.nChannelID

    def onCmdUserTextMessage(self, message):
        if message.nFromUserID == self.my_id:
            return
        if message.nMsgType not in {tt.TextMsgType.MSGTYPE_USER, tt.TextMsgType.MSGTYPE_CHANNEL}:
            return
        key = message.nFromUserID, message.nMsgType, message.nChannelID, message.nToUserID
        if message.bMore:
            self.parts.setdefault(key, []).append(message)
            return
        if key in self.parts:
            parts = self.parts.pop(key) + [message]
            content = str(tt.rebuildTextMessage(parts))
        else:
            content = str(tt.ttstr(message.szMessage))
        content = content.strip()
        if message.nMsgType == tt.TextMsgType.MSGTYPE_CHANNEL:
            match = self.mention.match(content)
            if match and match.group(1).strip():
                self._submit_ai(f"channel:{self.server.name}:{message.nChannelID}", message.nFromUserID,
                                match.group(1).strip(), lambda answer: self._send_channel(message.nChannelID, answer))
            elif match:
                self._send_channel(message.nChannelID, "Use: @chatgpt PROMPT. Send the bot a private message containing help for all commands.")
            return
        self._handle_private(message.nFromUserID, content)

    def onStreamMediaFile(self, info):
        status = info.nStatus
        if status not in DONE_MEDIA:
            return
        if self.star_current:
            path, user_id, _ = self.star_current
            self.star_current = None
            path.unlink(missing_ok=True)
            if status == tt.MediaFileStatus.MFS_ERROR:
                self._send_private(user_id, "STAR audio streaming failed.")
            self._start_waiting_stream()

    def onCmdFileNew(self, remote_file):
        delay = self.config.media.delete_uploaded_files_after
        if delay > 0:
            threading.Timer(delay, lambda: self.actions.put(
                lambda: self.doDeleteFile(remote_file.nChannelID, remote_file.nFileID))).start()

    def _user(self, user_id: int):
        return self.getUser(user_id)

    def _username(self, user_id: int) -> str:
        user = self._user(user_id)
        return str(tt.ttstr(user.szUsername) or "").strip()

    def _user_key(self, user_id: int) -> str:
        user = self._user(user_id)
        return (str(tt.ttstr(user.szUsername) or "").strip() or
                str(tt.ttstr(user.szNickname) or "").strip() or str(user_id)).casefold()

    def _display(self, user_id: int) -> str:
        user = self._user(user_id)
        return str(tt.ttstr(user.szNickname) or tt.ttstr(user.szUsername) or user_id)

    def _is_admin(self, user_id: int) -> bool:
        if user_id == 0:
            return True
        user = self._user(user_id)
        return bool(user.uUserType & tt.UserType.USERTYPE_ADMIN)

    def _same_channel(self, user_id: int) -> bool:
        return bool(self.channel_id and self._user(user_id).nChannelID == self.channel_id)

    def _send_private(self, user_id: int, text: str) -> None:
        if not user_id or not text:
            return
        if threading.current_thread() is not self:
            self.actions.put(lambda: self._send_private(user_id, text))
            return
        for message in tt.buildTextMessage(text, tt.TextMsgType.MSGTYPE_USER,
                                           nToUserID=user_id, nFromUserID=self.my_id):
            self.doTextMessage(message)

    def _send_channel(self, channel_id: int, text: str) -> None:
        if not text:
            return
        if threading.current_thread() is not self:
            self.actions.put(lambda: self._send_channel(channel_id, text))
            return
        for message in tt.buildTextMessage(text, tt.TextMsgType.MSGTYPE_CHANNEL,
                                           nChannelID=channel_id, nFromUserID=self.my_id):
            self.doTextMessage(message)

    def _handle_private(self, user_id: int, text: str) -> None:
        if not text:
            return
        command, _, rest = text.partition(" ")
        command = command.casefold()
        rest = rest.strip()
        if command.startswith("/"):
            self._send_private(user_id, "Commands do not start with /. Send help for the unified command list.")
            return
        if command == "help":
            self._send_private(user_id, self._help(rest))
            return
        if command == "gpt":
            if rest:
                self._submit_ai(f"pm:{self.server.name}:{self._user_key(user_id)}", user_id, rest,
                                lambda answer: self._send_private(user_id, answer))
            else:
                self._send_private(user_id, COMMAND_HELP["gpt"])
            return
        if command == "say":
            self._speak(user_id, rest)
            return
        if command in {"voices", "voice", "rate", "pitch", "preview", "current", "stop"}:
            self._star_command(user_id, command, rest)
            return
        if command in {"clear", "clear-all", "status", "quit", "restart"}:
            self._ai_command(user_id, command)
            return
        if command in self._media_commands():
            self._media_command(user_id, text, self._is_admin(user_id))
            return
        self._send_private(user_id, f"Unknown command: {command}. Send help for the unified command list.")

    @staticmethod
    def _help(topic: str = "") -> str:
        if not topic:
            return HELP_OVERVIEW
        command = topic.split()[0].casefold()
        return COMMAND_HELP.get(command, f"No help exists for {command}. Send help for the unified command list.")

    def _submit_ai(self, key: str, user_id: int, text: str, reply: Callable[[str], None]) -> None:
        if not self.ai:
            reply("ChatGPT is unavailable because no OpenAI API key is configured.")
            return
        if len(text) > self.config.bot.max_prompt_chars:
            reply(f"That prompt is too long (maximum {self.config.bot.max_prompt_chars} characters).")
            return
        if self.pending_ai >= self.config.bot.max_pending_per_server:
            reply("The ChatGPT request queue is full; try again shortly.")
            return
        self.pending_ai += 1
        def done(error, answer):
            def finish():
                self.pending_ai = max(0, self.pending_ai - 1)
                reply(answer or "I couldn't complete that request. Please try again later.")
            self.actions.put(finish)
        self.ai.submit(key, self._display(user_id), text, done)

    def _ai_command(self, user_id: int, command: str) -> None:
        key = f"pm:{self.server.name}:{self._user_key(user_id)}"
        if command == "clear":
            if self.ai:
                self.ai.reset(key)
            self._send_private(user_id, "Your private conversation context was cleared.")
        elif command == "clear-all":
            if not self._is_admin(user_id):
                self._send_private(user_id, "This command is restricted to TeamTalk administrators.")
            elif self.ai:
                self.ai.reset_all(); self._send_private(user_id, "All private and channel contexts were cleared.")
        elif command == "status":
            model = self.ai.settings.model if self.ai else "unavailable"
            histories = self.ai.history_count() if self.ai else 0
            self._send_private(user_id, f"Model: {model}. Contexts: {histories}. GPT queue: {self.pending_ai}. "
                               f"STAR: {self.star.connected_count()}/{len(self.star.connections)} connected, "
                               f"{len(self.star.all_voices())} voices. Media: {self.state}.")
        elif command in {"quit", "restart"}:
            if not self._is_admin(user_id):
                self._send_private(user_id, "This command is restricted to TeamTalk administrators.")
            else:
                self._send_private(user_id, "Restarting." if command == "restart" else "Shutting down.")
                self.control.request_stop(command == "restart")

    def _star_command(self, user_id: int, command: str, arg: str) -> None:
        key = self._user_key(user_id)
        voices = self.star.all_voices()
        if command == "voices":
            matches = [v for v in voices if arg.casefold() in v.casefold()]
            self.voice_results[key] = matches
            limit = self.config.star.max_voice_results
            self._send_private(user_id, "\n".join(f"{i+1}. {v}" for i, v in enumerate(matches[:limit])) or "No matching voices are available.")
        elif command == "voice":
            selected = None
            if arg.isdigit():
                results = self.voice_results.get(key, [])
                index = int(arg)-1
                selected = results[index] if 0 <= index < len(results) else None
            else:
                exact = [v for v in voices if v.casefold() == arg.casefold()]
                partial = [v for v in voices if arg.casefold() in v.casefold()]
                selected = exact[0] if exact else (partial[0] if len(partial) == 1 else None)
                if len(partial) > 1 and not exact:
                    self.voice_results[key] = partial
                    self._send_private(user_id, "Multiple voices matched:\n" + "\n".join(f"{i+1}. {v}" for i, v in enumerate(partial[:self.config.star.max_voice_results])))
                    return
            if selected:
                self.user_voices[key] = selected; self._send_private(user_id, f"Voice set to {selected}.")
            else:
                self._send_private(user_id, "No matching voice. Send voices first. For details, send help voice.")
        elif command == "current":
            self._send_private(user_id, f"Current voice: {self._voice(user_id) or 'none'}. Rate: {self.user_rates.get(key, 'none')}. Pitch: {self.user_pitches.get(key, 'none')}.")
        elif command in {"rate", "pitch"}:
            values = self.user_rates if command == "rate" else self.user_pitches
            if arg.casefold() == "none":
                values.pop(key, None); self._send_private(user_id, f"{command.title()} cleared.")
            else:
                try:
                    value = float(arg)
                    if not math.isfinite(value): raise ValueError
                    values[key] = arg; self._send_private(user_id, f"{command.title()} set to {arg}.")
                except ValueError:
                    self._send_private(user_id, COMMAND_HELP[command])
        elif command == "preview":
            voice = arg or self._voice(user_id)
            self._request_star(user_id, voice, self.config.star.voice_preview_text.format(voice=voice))
        elif command == "stop":
            self._clear_star(); self._send_private(user_id, "STAR audio queue cleared.")

    def _voice(self, user_id: int) -> str:
        return self.user_voices.get(self._user_key(user_id)) or self.config.star.default_voice

    def _speak(self, user_id: int, text: str) -> None:
        voice = self._voice(user_id)
        if not text:
            self._send_private(user_id, COMMAND_HELP["say"])
        elif not voice:
            self._send_private(user_id, "Choose a STAR voice with voice NAME. Send help voice for details.")
        elif not self._same_channel(user_id):
            self._send_private(user_id, "You must be in the bot's channel to synthesize speech.")
        else:
            self._request_star(user_id, voice, f"{self._display(user_id)} said, {text}")

    def _request_star(self, user_id: int, voice: str, text: str) -> None:
        if not voice:
            self._send_private(user_id, "Choose a STAR voice first."); return
        key = self._user_key(user_id)
        params = []
        if key in self.user_rates: params.append(f"r={self.user_rates[key]}")
        if key in self.user_pitches: params.append(f"p={self.user_pitches[key]}")
        requested_voice = voice + (f"<{' '.join(params)}>" if params else "")
        def done(error, extension, audio):
            if error or not audio:
                self._send_private(user_id, f"Synthesis failed: {error or 'empty audio'}"); return
            suffix = "".join(c for c in (extension or "wav") if c.isalnum()) or "wav"
            directory = Path(self.config.star.temp_dir).expanduser() if self.config.star.temp_dir else None
            if directory: directory.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(prefix="galactitalk-star-", suffix=f".{suffix}", dir=directory, delete=False)
            with handle:
                handle.write(audio)
            self.actions.put(lambda: self._enqueue_star(Path(handle.name), user_id, voice))
        if not self.star.request(requested_voice, text, done):
            self._send_private(user_id, "No connected STAR coagulator is available.")

    def _enqueue_star(self, path: Path, user_id: int, voice: str) -> None:
        self.star_queue.put((path, user_id, voice))
        self._start_waiting_stream()

    def _start_waiting_stream(self) -> None:
        if self.state in {"playing", "paused"} or self.preparing or self.star_current:
            return
        try:
            self.star_current = self.star_queue.get_nowait()
        except queue.Empty:
            return
        path, user_id, _ = self.star_current
        if not self.startStreamingMediaFileToChannel(tt.ttstr(str(path)), None):
            self.star_current = None; path.unlink(missing_ok=True)
            self._send_private(user_id, "Could not start STAR audio stream.")

    def _clear_star(self) -> None:
        if self.star_current:
            self.stopStreamingMediaFileToChannel()
        while True:
            try: self.star_queue.get_nowait()[0].unlink(missing_ok=True)
            except queue.Empty: break

    @staticmethod
    def _media_commands() -> set[str]:
        return {"a", "p", "u", "sv", "s", "b", "n", "c", "sb", "sf", "v", "sp", "m", "gl", "dl"}

    def _media_command(self, user_id: int, text: str, admin: bool, silent: bool = False) -> None:
        command, _, arg = text.strip().partition(" ")
        command = command.casefold()
        reply = lambda value: None if silent else self._send_private(user_id, value)
        if not admin and not self._same_channel(user_id):
            reply("You must be in the bot's channel."); return
        if command == "a": reply("GalactiTalk: TeamTalk media, STAR speech, and ChatGPT in one multi-server client.")
        elif command == "p":
            if arg:
                reply("Searching...")
                self.youtube.submit_search(arg, lambda error, tracks: self.actions.put(lambda: self._media_results(user_id, error, tracks, arg)))
            elif self.state == "playing": self._update_playback(paused=True)
            elif self.state == "paused": self._update_playback(paused=False)
            else: reply("Nothing is playing.")
        elif command == "u":
            if not arg: reply("Usage: u URL")
            elif Path(arg).expanduser().exists() and not admin: reply("Local paths are restricted to administrators.")
            elif not Path(arg).expanduser().exists() and urlparse(arg).scheme.casefold() not in {"http", "https", "rtmp", "rtsp"}:
                reply("Use an http, https, rtmp, or rtsp URL." if not admin else "The local path cannot be found.")
            else:
                reply("Loading URL...")
                self.youtube.submit_url(arg, lambda error, tracks: self.actions.put(lambda: self._media_results(user_id, error, tracks, arg)))
        elif command == "s":
            if self.current_track: self._stop_media(); reply("Playback stopped.")
            else: reply("Nothing is playing.")
        elif command in {"b", "n"}: self._step(-1 if command == "b" else 1, reply)
        elif command == "c":
            try: self._play_index(int(arg)-1); reply(f"Playing {arg}.")
            except (ValueError, IndexError): reply("Track number is out of range.")
        elif command in {"sb", "sf"}:
            if not self.current_track: reply("Nothing is playing.")
            else:
                try: amount = float(arg) if arg else self.config.media.seek_step
                except ValueError: reply("Seek step must be a number."); return
                self._update_playback(relative_seconds=amount * (1 if command == "sf" else -1))
        elif command == "v":
            if not arg: reply(str(self.volume))
            else:
                try: value = int(arg)
                except ValueError: reply("Volume must be a whole number."); return
                if not 0 <= value <= self.config.media.max_volume: reply(f"Volume must be 0-{self.config.media.max_volume}.")
                else: self.volume = value; self._update_playback()
        elif command == "sp":
            if not arg: reply(f"Current rate: {self.speed:g}")
            else:
                try: value = float(arg)
                except ValueError: reply("Speed must be 0.25-4."); return
                if not 0.25 <= value <= 4: reply("Speed must be 0.25-4.")
                else:
                    self.speed = value
                    if self.current_track: self.media_player.set_speed(value)
                    reply(f"Playback speed set to {value:g}.")
        elif command == "m":
            if not arg: reply(f"Current mode: {self.mode} ({MODES[self.mode]}). Available: " + ", ".join(f"{k}={v}" for k,v in MODES.items()))
            elif arg.casefold() in MODES: self.mode = arg.casefold(); reply(f"Current mode: {MODES[self.mode]}.")
            else: reply("Unknown mode.")
        elif command == "gl": reply(self.current_track.source if self.current_track else "Nothing is playing.")
        elif command == "dl": self._upload_current(user_id, reply)
        elif command == "sv": reply("Current service: yt\nAvailable: yt (YouTube and supported yt-dlp sites)")

    def _media_results(self, user_id: int, error: str | None, tracks: list[Track] | None, request: str) -> None:
        if error or not tracks:
            self._send_private(user_id, f"Nothing found: {error or request}"); return
        self.tracks = tracks
        self.media_requester = user_id
        if self.config.media.send_channel_messages and self.channel_id:
            self._send_channel(self.channel_id, f"{self._display(user_id)} requested {request}")
        self._play_index(0)
        self._send_private(user_id, f"Playing {tracks[0].name or tracks[0].source}")

    def _play_index(self, index: int) -> None:
        if index < 0: index += len(self.tracks)
        if not 0 <= index < len(self.tracks): raise IndexError(index)
        self.play_token += 1
        token = self.play_token
        self.track_index, self.current_track, self.preparing = index, self.tracks[index], True
        self.state = "preparing"
        track = self.current_track
        self.media_player.stop()
        if self.star_current:
            self.stopStreamingMediaFileToChannel()
        old_temp, self.current_temp = self.current_temp, None
        if old_temp: old_temp.cleanup()
        def prepare():
            temp_root = Path(self.config.media.temp_dir).expanduser() if self.config.media.temp_dir else None
            if temp_root:
                temp_root.mkdir(parents=True, exist_ok=True)
            temp = tempfile.TemporaryDirectory(prefix="galactitalk-media-", dir=temp_root)
            try:
                source, live = self.youtube.prepare(track, Path(temp.name))
                self.actions.put(lambda: self._prepared(token, temp, source))
            except Exception as exc:
                temp.cleanup()
                error = str(exc)
                self.actions.put(lambda error=error: self._prepare_failed(token, error))
        threading.Thread(target=prepare, name=f"prepare:{self.server.name}", daemon=True).start()

    def _prepared(self, token: int, temp, source: Path | str) -> None:
        if token != self.play_token:
            temp.cleanup(); return
        self.preparing, self.current_temp, self.current_source = False, temp, source
        try:
            self.media_player.start(source, self.volume, self.speed,
                                    self.config.media.max_volume, token)
        except Exception as exc:
            self._prepare_failed(token, str(exc))
            return
        self.state = "playing"
        self._update_status()

    def _prepare_failed(self, token: int, error: str) -> None:
        if token != self.play_token: return
        self.media_player.stop()
        if self.current_temp:
            self.current_temp.cleanup()
            self.current_temp = None
        self.preparing, self.state, self.current_track = False, "stopped", None
        self._update_status()
        print(f"{self.server.name}: media preparation failed: {error}")
        if self.media_requester:
            self._send_private(self.media_requester, f"Media could not be played: {error}")
        self._start_waiting_stream()

    def _update_playback(self, paused: bool | None = None,
                         relative_seconds: float | None = None) -> None:
        if not self.current_track: return
        if relative_seconds is not None:
            self.media_player.seek(relative_seconds)
        elif paused is not None:
            if self.media_player.pause(paused):
                self.state = "paused" if paused else "playing"
                self._update_status()
        else:
            self.media_player.set_volume(self.volume)

    def _media_exited(self, token: int, returncode: int, error: str = "") -> None:
        if token != self.play_token:
            return
        self.media_player.stop()
        if self.current_temp:
            self.current_temp.cleanup()
            self.current_temp = None
        self.current_source = None
        self.elapsed_ms = self.duration_ms = 0
        self.state = "stopped"
        if returncode == 0 and self.current_track is not None:
            self._advance_after_finish()
            if self.state == "stopped":
                self._update_status()
        else:
            if returncode:
                detail = f": {error}" if error else ""
                print(f"{self.server.name}: MPV media playback failed with exit status {returncode}{detail}")
                if self.media_requester:
                    self._send_private(self.media_requester, "Media playback failed. The failure was recorded in the GalactiTalk log.")
            self.current_track = None
            self.media_requester = 0
            self._update_status()
            self._start_waiting_stream()

    def _media_metadata(self, token: int, title: str) -> None:
        if token != self.play_token or not self.current_track or self.state != "playing":
            return
        if self.current_track.kind not in {"direct", "local"}:
            return
        if title != self.current_track.name:
            self.current_track.name = title
            self._update_status()

    def _update_status(self) -> None:
        text = self.server.status
        if self.current_track and self.state in {"playing", "paused"}:
            prefix = "Paused" if self.state == "paused" else "Playing"
            text = f"{prefix}: {self.current_track.name or self.current_track.source}"
        self.doChangeStatus(GENDERS.get(self.server.gender, 4096), tt.ttstr(text[:256]))

    def _step(self, delta: int, reply: Callable[[str], None]) -> None:
        if not self.tracks: reply("Nothing is playing."); return
        index = self.track_index + delta
        if self.mode == "rtl": index %= len(self.tracks)
        if not 0 <= index < len(self.tracks): reply("No next track." if delta > 0 else "No previous track."); return
        self._play_index(index); reply(f"Playing {self.tracks[index].name or self.tracks[index].source}")

    def _advance_after_finish(self) -> None:
        if self.mode == "st": self.current_track = None; self._start_waiting_stream(); return
        if self.mode == "rt": self._play_index(self.track_index); return
        if self.mode == "rnd" and self.tracks: self._play_index(random.randrange(len(self.tracks))); return
        next_index = self.track_index + 1
        if next_index >= len(self.tracks):
            if self.mode == "rtl": next_index = 0
            else: self.current_track = None; self._start_waiting_stream(); return
        self._play_index(next_index)

    def _stop_media(self) -> None:
        self.play_token += 1; self.preparing = False; self.current_track = None; self.state = "stopped"
        self.media_requester = 0
        self.media_player.stop()
        if self.current_temp: self.current_temp.cleanup(); self.current_temp = None
        self.current_source = None
        self._update_status()
        self._start_waiting_stream()

    def _stop_all(self) -> None:
        self.play_token += 1
        self.media_player.stop()
        self.current_track = None
        self.state = "stopped"
        self._update_status()
        try: self.stopStreamingMediaFileToChannel()
        except Exception: pass
        if self.current_temp: self.current_temp.cleanup(); self.current_temp = None
        self._clear_star()

    def _upload_current(self, user_id: int, reply: Callable[[str], None]) -> None:
        if not self.current_track: reply("Nothing is playing."); return
        user_account = self.getMyUserAccount()
        if not user_account.uUserRights & tt.UserRight.USERRIGHT_UPLOAD_FILES:
            reply("The bot account cannot upload files."); return
        track = Track(**vars(self.current_track))
        def prepare_upload():
            temp = tempfile.TemporaryDirectory(prefix="galactitalk-upload-")
            try:
                source, live = self.youtube.download(track, Path(temp.name))
                if live: raise RuntimeError("Live streams cannot be uploaded")
                persistent = Path(temp.name) / source.name
                self.actions.put(lambda: (self.doSendFile(self.channel_id, tt.ttstr(str(persistent))), reply("Uploading...")))
                # Kept until TeamTalk has opened the file; cleanup is delayed.
                threading.Timer(30, temp.cleanup).start()
            except Exception as exc:
                temp.cleanup()
                error = str(exc)
                self.actions.put(lambda error=error: reply(f"Upload failed: {error}"))
        threading.Thread(target=prepare_upload, daemon=True).start()

    def _join(self, channel: str, password: str) -> None:
        if str(channel).isdigit(): channel_id = int(channel)
        else: channel_id = self.getRootChannelID() if channel == "/" else self.getChannelIDFromPath(tt.ttstr(channel))
        if channel_id > 0: self.doJoinChannelByID(channel_id, tt.ttstr(password))
        else: print(f"{self.server.name}: channel not found: {channel}")
