from __future__ import annotations

import math
import os
from pathlib import Path
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
from typing import Callable

from teamtalk_sdk import load_teamtalk_sdk

from .config import AppConfig, ServerSettings, write_config
from .services import MediaCache, OpenAIService, StarPool, Track, YouTubeService


tt = load_teamtalk_sdk()
DONE_MEDIA = {tt.MediaFileStatus.MFS_FINISHED, tt.MediaFileStatus.MFS_ERROR, tt.MediaFileStatus.MFS_ABORTED}
MODES = {"st": "single track", "rt": "repeat track", "tl": "track list", "rtl": "repeat track list", "rnd": "random"}
GENDERS = {"m": 0, "f": 256, "n": 4096}


class RuntimeControl:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.restart = False

    def request_stop(self, restart: bool = False) -> None:
        self.restart = restart
        self.stop_event.set()


class Connection(tt.TeamTalk, threading.Thread):
    """One independent TeamTalk connection and native SDK media route."""

    def __init__(self, server: ServerSettings, config: AppConfig, config_path: Path,
                 ai: OpenAIService | None, star: StarPool, youtube: YouTubeService,
                 cache: MediaCache, control: RuntimeControl):
        tt.TeamTalk.__init__(self)
        threading.Thread.__init__(self, name=f"teamtalk:{server.name}", daemon=True)
        self.server, self.config, self.config_path = server, config, config_path
        self.ai, self.star, self.youtube, self.cache, self.control = ai, star, youtube, cache, control
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
        self.star_queue: queue.Queue[tuple[Path, int, str]] = queue.Queue()
        self.star_current: tuple[Path, int, str] | None = None
        self.locked = False

    @staticmethod
    def _compile_mention(nickname: str) -> re.Pattern[str]:
        nick = re.escape(nickname.strip())
        return re.compile(rf"^\s*@(?:\[{nick}\]|{nick})(?=$|[\s,:-])[\s,:-]*(.*)$", re.I | re.S)

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
        self.doChangeStatus(GENDERS.get(self.server.gender, 4096), tt.ttstr(self.server.status))
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
            return
        self._handle_private(message.nFromUserID, content)

    def onStreamMediaFile(self, info):
        self.elapsed_ms, self.duration_ms = int(info.uElapsedMSec), int(info.uDurationMSec)
        status = info.nStatus
        if status == tt.MediaFileStatus.MFS_PAUSED:
            self.state = "paused"
            return
        if status in {tt.MediaFileStatus.MFS_STARTED, tt.MediaFileStatus.MFS_PLAYING}:
            self.state = "playing"
            return
        if status not in DONE_MEDIA:
            return
        if self.star_current:
            path, user_id, _ = self.star_current
            self.star_current = None
            path.unlink(missing_ok=True)
            if status == tt.MediaFileStatus.MFS_ERROR:
                self._send_private(user_id, "STAR audio streaming failed.")
            self._start_waiting_stream()
            return
        if status == tt.MediaFileStatus.MFS_ABORTED and self.preparing:
            # An old stream was stopped so a newly selected track could be prepared.
            return
        was_playing = self.current_track is not None
        self.state = "stopped"
        if was_playing and status == tt.MediaFileStatus.MFS_FINISHED:
            self._advance_after_finish()
        elif status in {tt.MediaFileStatus.MFS_ERROR, tt.MediaFileStatus.MFS_ABORTED}:
            self.current_track = None
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
        return bool(user.uUserType & tt.UserType.USERTYPE_ADMIN) or self._username(user_id).casefold() in {x.casefold() for x in self.server.admins}

    def _is_banned(self, user_id: int) -> bool:
        return self._username(user_id).casefold() in {x.casefold() for x in self.server.banned_users}

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
        if self._is_banned(user_id):
            self._send_private(user_id, "You are banned from using this bot.")
            return
        command, _, rest = text.partition(" ")
        slash = command.casefold()
        if slash in {"/ai", "/chatgpt"}:
            if rest:
                self._submit_ai(f"pm:{self.server.name}:{self._user_key(user_id)}", user_id, rest,
                                lambda answer: self._send_private(user_id, answer))
            else:
                self._send_private(user_id, "Usage: /ai your prompt")
            return
        if slash in {"/say", "/speak"}:
            self._speak(user_id, rest)
            return
        if slash in {"/rate", "/pitch"}:
            self._star_command(user_id, slash[1:], rest)
            return
        if slash in {"/voices", "/voice", "/select", "/preview", "/current", "/who", "/stop"}:
            self._star_command(user_id, slash[1:], rest)
            return
        ai_commands = {"/clear", "/reset", "/clear-all", "/status", "/help", "/quit", "/exit", "/shutdown", "/restart"}
        if slash in ai_commands:
            self._ai_command(user_id, slash)
            return
        prefix = self.config.bot.command_prefix
        if text.startswith(prefix):
            name, _, arg = text[len(prefix):].strip().partition(" ")
            self._star_command(user_id, name.casefold(), arg.strip())
            return
        # Old media commands are intentionally still bare PM commands.
        if command.casefold() in self._media_commands():
            self._media_command(user_id, text, self._is_admin(user_id))
            return
        if self.server.private_message_mode == "star":
            self._speak(user_id, text)
        else:
            self._submit_ai(f"pm:{self.server.name}:{self._user_key(user_id)}", user_id, text,
                            lambda answer: self._send_private(user_id, answer))

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
        if command in {"/clear", "/reset"}:
            if self.ai:
                self.ai.reset(key)
            self._send_private(user_id, "Your private conversation context was cleared.")
        elif command == "/clear-all":
            if not self._is_admin(user_id):
                self._send_private(user_id, "This command is restricted to TeamTalk administrators.")
            elif self.ai:
                self.ai.reset_all(); self._send_private(user_id, "All private and channel contexts were cleared.")
        elif command == "/status":
            model = self.ai.settings.model if self.ai else "unavailable"
            histories = self.ai.history_count() if self.ai else 0
            self._send_private(user_id, f"Model: {model}. Contexts: {histories}. Queue: {self.pending_ai}. Media: {self.state}.")
        elif command == "/help":
            self._send_private(user_id, "GalactiTalk PM commands:\n/ai PROMPT - ask ChatGPT\n/say TEXT - synthesize with STAR\n"
                               "/clear, /status - ChatGPT context controls\n!voices, !voice, !preview, !current - STAR controls\n"
                               "h - all YouTube media commands\nAdministrators: /clear-all, /restart, /quit")
        elif command in {"/quit", "/exit", "/shutdown", "/restart"}:
            if not self._is_admin(user_id):
                self._send_private(user_id, "This command is restricted to TeamTalk administrators.")
            else:
                self._send_private(user_id, "Restarting." if command == "/restart" else "Shutting down.")
                self.control.request_stop(command == "/restart")

    def _star_command(self, user_id: int, command: str, arg: str) -> None:
        key = self._user_key(user_id)
        voices = self.star.all_voices()
        if command in {"help", "h", "?"}:
            self._send_private(user_id, "STAR commands: !voices [filter], !voice NAME|NUMBER, /rate NUMBER|none, "
                               "/pitch NUMBER|none, !preview [voice], !current, !stop")
        elif command in {"voices", "v"}:
            matches = [v for v in voices if arg.casefold() in v.casefold()]
            self.voice_results[key] = matches
            limit = self.config.star.max_voice_results
            self._send_private(user_id, "\n".join(f"{i+1}. {v}" for i, v in enumerate(matches[:limit])) or "No matching voices are available.")
        elif command in {"voice", "select"}:
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
                self._send_private(user_id, "No matching voice. Use !voices first.")
        elif command in {"current", "who"}:
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
                    self._send_private(user_id, f"Usage: !{command} number|none")
        elif command == "preview":
            voice = arg or self._voice(user_id)
            self._request_star(user_id, voice, self.config.star.voice_preview_text.format(voice=voice))
        elif command == "status":
            self._send_private(user_id, f"STAR connections: {self.star.connected_count()}/{len(self.star.connections)}. Voices: {len(voices)}.")
        elif command == "stop":
            self._clear_star(); self._send_private(user_id, "STAR audio queue cleared.")
        elif command in {"restart", "quit", "exit", "shutdown"}:
            self._ai_command(user_id, "/restart" if command == "restart" else "/quit")
        else:
            self._send_private(user_id, "Unknown STAR command. Send !help for commands.")

    def _voice(self, user_id: int) -> str:
        return self.user_voices.get(self._user_key(user_id)) or self.config.star.default_voice

    def _speak(self, user_id: int, text: str) -> None:
        voice = self._voice(user_id)
        if not text:
            self._send_private(user_id, "Usage: /say text")
        elif not voice:
            self._send_private(user_id, "Choose a STAR voice with !voice NAME.")
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
        return {"h", "a", "p", "u", "sv", "s", "b", "n", "c", "sb", "sf", "v", "sp", "f", "m", "gl", "dl", "r",
                "cg", "cl", "cn", "cs", "cc", "cm", "jc", "bc", "l", "ua", "ub", "eh", "sc", "va", "rs", "q", "gcid"}

    def _media_command(self, user_id: int, text: str, admin: bool, silent: bool = False) -> None:
        command, _, arg = text.strip().partition(" ")
        command = command.casefold()
        reply = lambda value: None if silent else self._send_private(user_id, value)
        if not admin and (not self._same_channel(user_id) or self.locked):
            reply("You must be in the bot's channel, and the bot must be unlocked."); return
        if not admin and command in {x.casefold() for x in self.server.blocked_media_commands}:
            reply("This media command is blocked."); return
        admin_commands = {"cg", "cl", "cn", "cs", "cc", "cm", "jc", "bc", "l", "ua", "ub", "eh", "sc", "va", "rs", "q", "gcid"}
        if command in admin_commands and not admin:
            reply("This command is restricted to administrators."); return
        if command == "h":
            reply("Media commands:\np QUERY - search/play YouTube (or pause/resume)\nu URL - play URL, playlist, or admin local path\n"
                  "s stop; b previous; n next; c NUMBER select; sb/sf [SECONDS] seek; v [0-MAX] volume; sp [0.25-4] speed\n"
                  "m [st|rt|tl|rtl|rnd] mode; f [+|-NUMBER|NUMBER] favorites; r [NUMBER] recents; gl link; dl upload\n"
                  "Admin: bc, cg, cl, cn, cs, cc, cm, jc, l, ua, ub, eh, sc, va, rs, q, gcid")
        elif command == "a": reply("GalactiTalk: TeamTalk media, STAR speech, and ChatGPT in one multi-server client.")
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
                target = max(0, self.elapsed_ms + int(amount*1000)*(1 if command == "sf" else -1))
                self._update_playback(offset=target)
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
                    if self.current_track: self._play_index(self.track_index)
                    reply(f"Playback speed set to {value:g}.")
        elif command == "m":
            if not arg: reply(f"Current mode: {self.mode} ({MODES[self.mode]}). Available: " + ", ".join(f"{k}={v}" for k,v in MODES.items()))
            elif arg.casefold() in MODES: self.mode = arg.casefold(); reply(f"Current mode: {MODES[self.mode]}.")
            else: reply("Unknown mode.")
        elif command == "f": self._favorites(user_id, arg, reply)
        elif command == "r": self._recents(arg, reply)
        elif command == "gl": reply(self.current_track.source if self.current_track else "Nothing is playing.")
        elif command == "dl": self._upload_current(user_id, reply)
        elif command == "sv": reply("Current service: yt\nAvailable: yt (YouTube and supported yt-dlp sites)")
        elif command == "bc": self._edit_list(self.server.blocked_media_commands, arg, reply)
        elif command == "ua": self._edit_list(self.server.admins, arg, reply)
        elif command == "ub": self._edit_list(self.server.banned_users, arg, reply)
        elif command == "cg":
            if arg.casefold() not in GENDERS: reply("Gender must be n, m, or f.")
            else: self.server.gender = arg.casefold(); self.doChangeStatus(GENDERS[self.server.gender], tt.ttstr(self.server.status))
        elif command == "cl": reply("Current language: en. GalactiTalk's built-in messages are English.")
        elif command == "cn":
            if arg: self.server.nickname = arg; self.mention = self._compile_mention(arg); self.doChangeNickname(tt.ttstr(arg))
            else: reply("Usage: cn NICKNAME")
        elif command == "cs": self.server.status = arg; self.doChangeStatus(GENDERS.get(self.server.gender,4096), tt.ttstr(arg))
        elif command == "cc": self.cache.recents.clear(); self.cache.favorites.clear(); self.cache.save(); reply("Cache cleared.")
        elif command == "cm": self.config.media.send_channel_messages = not self.config.media.send_channel_messages; reply(f"Channel messages {'enabled' if self.config.media.send_channel_messages else 'disabled'}.")
        elif command == "jc":
            channel, sep, password = arg.partition("|")
            self._join(channel.strip() or self.server.channel, password.strip() if sep else "")
        elif command == "l": self.locked = not self.locked; reply("Locked." if self.locked else "Unlocked.")
        elif command == "eh": reply("Event handling is built in and enabled for every connection.")
        elif command == "sc": write_config(self.config_path, self.config); reply("Configuration saved.")
        elif command == "va": reply("Native media streaming is enabled; no sound-device voice route is required.")
        elif command == "gcid": reply(str(self.channel_id))
        elif command in {"rs", "q"}: self.control.request_stop(command == "rs")

    def _media_results(self, user_id: int, error: str | None, tracks: list[Track] | None, request: str) -> None:
        if error or not tracks:
            self._send_private(user_id, f"Nothing found: {error or request}"); return
        self.tracks = tracks
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
        track, speed = self.current_track, self.speed
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
                if speed != 1 and not live and isinstance(source, Path):
                    source = self._speed_file(source, Path(temp.name), speed)
                self.actions.put(lambda: self._prepared(token, temp, source))
            except Exception as exc:
                temp.cleanup()
                error = str(exc)
                self.actions.put(lambda error=error: self._prepare_failed(token, error))
        threading.Thread(target=prepare, name=f"prepare:{self.server.name}", daemon=True).start()

    @staticmethod
    def _speed_file(source: Path, directory: Path, speed: float) -> Path:
        output = directory / "speed.opus"
        factors, remaining = [], speed
        while remaining > 2: factors.append(2.0); remaining /= 2
        while remaining < .5: factors.append(.5); remaining /= .5
        factors.append(remaining)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source), "-vn",
                        "-filter:a", ",".join(f"atempo={x:g}" for x in factors), str(output)], check=True)
        return output

    def _prepared(self, token: int, temp, source: Path | str) -> None:
        if token != self.play_token:
            temp.cleanup(); return
        self.preparing, self.current_temp, self.current_source = False, temp, source
        playback = self._playback(False, 0)
        start_ex = getattr(tt, "_StartStreamingMediaFileToChannelEx", None)
        ok = start_ex(self._tt, tt.ttstr(str(source)), playback, None) if start_ex else self.startStreamingMediaFileToChannel(tt.ttstr(str(source)), None)
        if not ok:
            self._prepare_failed(token, "TeamTalk rejected the media source")
            return
        self.state = "playing"
        if self.current_track:
            self.cache.recents.append(Track(**vars(self.current_track)))
            self.cache.recents = self.cache.recents[-100:]
            self.cache.save()

    def _prepare_failed(self, token: int, error: str) -> None:
        if token != self.play_token: return
        self.preparing, self.state, self.current_track = False, "stopped", None
        print(f"{self.server.name}: media preparation failed: {error}")

    def _playback(self, paused: bool, offset: int):
        playback = tt.MediaFilePlayback()
        playback.uOffsetMSec = offset
        playback.bPaused = paused
        playback.audioPreprocessor.nPreprocessor = tt.AudioPreprocessorType.TEAMTALK_AUDIOPREPROCESSOR
        maximum = getattr(tt.SoundLevel, "SOUND_GAIN_MAX", 32000)
        default = getattr(tt.SoundLevel, "SOUND_GAIN_DEFAULT", 1000)
        playback.audioPreprocessor.ttpreprocessor.nGainLevel = int(default * self.volume / 50)
        playback.audioPreprocessor.ttpreprocessor.nGainLevel = min(maximum, playback.audioPreprocessor.ttpreprocessor.nGainLevel)
        return playback

    def _update_playback(self, paused: bool | None = None, offset: int | None = None) -> None:
        if not self.current_track: return
        paused = self.state == "paused" if paused is None else paused
        offset = getattr(tt, "TT_MEDIAPLAYBACK_OFFSET_IGNORE", 0xFFFFFFFF) if offset is None else offset
        update = getattr(tt, "_UpdateStreamingMediaFileToChannel", None)
        if update and update(self._tt, self._playback(paused, offset), None):
            self.state = "paused" if paused else "playing"

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
        self.stopStreamingMediaFileToChannel()
        if self.current_temp: self.current_temp.cleanup(); self.current_temp = None
        self._start_waiting_stream()

    def _stop_all(self) -> None:
        try: self.stopStreamingMediaFileToChannel()
        except Exception: pass
        if self.current_temp: self.current_temp.cleanup(); self.current_temp = None
        self._clear_star()

    def _favorites(self, user_id: int, arg: str, reply: Callable[[str], None]) -> None:
        username = self._username(user_id)
        if not username: reply("Favorites are unavailable to guest users."); return
        items = self.cache.favorites.setdefault(username, [])
        if arg.startswith("+"):
            if not self.current_track: reply("Nothing is playing."); return
            items.append(Track(**vars(self.current_track))); self.cache.save(); reply("Added.")
        elif arg.startswith("-"):
            try:
                index = int(arg[1:])-1 if arg[1:] else items.index(self.current_track)
                items.pop(index); self.cache.save(); reply("Deleted.")
            except (ValueError, IndexError): reply("Track is not in favorites.")
        elif arg:
            try: self.tracks = items; self._play_index(int(arg)-1)
            except (ValueError, IndexError): reply("Track number is out of range.")
        else: reply("\n".join(f"{i+1}: {t.name or t.source}" for i,t in enumerate(items)) or "The list is empty.")

    def _recents(self, arg: str, reply: Callable[[str], None]) -> None:
        items = list(reversed(self.cache.recents))
        if arg:
            try: self.tracks = items; self._play_index(int(arg)-1)
            except (ValueError, IndexError): reply("Track number is out of range.")
        else: reply("\n".join(f"{i+1}: {t.name or t.source}" for i,t in enumerate(items)) or "The list is empty.")

    def _upload_current(self, user_id: int, reply: Callable[[str], None]) -> None:
        if not self.current_track: reply("Nothing is playing."); return
        user_account = self.getMyUserAccount()
        if not user_account.uUserRights & tt.UserRight.USERRIGHT_UPLOAD_FILES:
            reply("The bot account cannot upload files."); return
        track = Track(**vars(self.current_track))
        def prepare_upload():
            temp = tempfile.TemporaryDirectory(prefix="galactitalk-upload-")
            try:
                source, live = self.youtube.prepare(track, Path(temp.name))
                if live or not isinstance(source, Path): raise RuntimeError("Live streams cannot be uploaded")
                persistent = Path(temp.name) / source.name
                self.actions.put(lambda: (self.doSendFile(self.channel_id, tt.ttstr(str(persistent))), reply("Uploading...")))
                # Kept until TeamTalk has opened the file; cleanup is delayed.
                threading.Timer(30, temp.cleanup).start()
            except Exception as exc:
                temp.cleanup()
                error = str(exc)
                self.actions.put(lambda error=error: reply(f"Upload failed: {error}"))
        threading.Thread(target=prepare_upload, daemon=True).start()

    @staticmethod
    def _edit_list(values: list[str], arg: str, reply: Callable[[str], None]) -> None:
        if not arg: reply(", ".join(values) or "The list is empty.")
        elif arg.startswith("+") and arg[1:]:
            if arg[1:] not in values: values.append(arg[1:])
            reply("Added.")
        elif arg.startswith("-") and arg[1:]:
            try: values.remove(arg[1:]); reply("Deleted.")
            except ValueError: reply("That value is not in the list.")
        else: reply("Use +VALUE or -VALUE.")

    def _join(self, channel: str, password: str) -> None:
        if str(channel).isdigit(): channel_id = int(channel)
        else: channel_id = self.getRootChannelID() if channel == "/" else self.getChannelIDFromPath(tt.ttstr(channel))
        if channel_id > 0: self.doJoinChannelByID(channel_id, tt.ttstr(password))
        else: print(f"{self.server.name}: channel not found: {channel}")
