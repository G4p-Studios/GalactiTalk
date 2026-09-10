from __future__ import annotations

import inspect
import itertools
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlparse

import httpx
from openai import OpenAI
import websockets.exceptions
import websockets.sync.client
from yt_dlp import YoutubeDL
from youtubesearchpython import VideosSearch as _VideosSearch
from youtubesearchpython.core.constants import userAgent

from .config import MediaSettings, OpenAISettings, StarSettings


USER_REVISION = 4


class VideosSearch(_VideosSearch):
    """Original TTMediaBot search client, updated for current HTTPX."""

    def syncPostRequest(self) -> httpx.Response:
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        with httpx.Client(transport=transport, timeout=self.timeout) as client:
            return client.post(self.url, headers={"User-Agent": userAgent}, json=self.data)


class OpenAIService:
    def __init__(self, settings: OpenAISettings):
        self.settings = settings
        key = settings.api_key or os.environ.get(settings.api_key_env, "")
        if not key:
            raise ValueError(f"No OpenAI API key. Set {settings.api_key_env} or configure openai.api_key.")
        args: dict[str, object] = {"api_key": key, "timeout": settings.timeout_seconds}
        if settings.base_url:
            args["base_url"] = settings.base_url
        self.client = OpenAI(**args)
        self.executor = ThreadPoolExecutor(settings.max_workers, thread_name_prefix="openai")
        self.histories: dict[str, list[dict[str, str]]] = {}
        self.epochs: dict[str, int] = {}
        self.global_epoch = 0
        self.conversation_locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    def submit(self, key: str, display_name: str, text: str, callback: Callable[[str | None, str | None], None]) -> None:
        with self.lock:
            token = self.global_epoch, self.epochs.get(key, 0)
        self.executor.submit(self._generate, key, display_name, text, callback, token)

    def _generate(self, key: str, display_name: str, text: str, callback: Callable, token: tuple[int, int]) -> None:
        try:
            with self.lock:
                conversation_lock = self.conversation_locks.setdefault(key, threading.Lock())
            with conversation_lock:
                with self.lock:
                    history = list(self.histories.get(key, []))
                message = {"role": "user", "content": f"The user {json.dumps(display_name, ensure_ascii=False)} said: {json.dumps(text, ensure_ascii=False)}"}
                request: dict[str, object] = {
                    "model": self.settings.model, "instructions": self.settings.instructions,
                    "input": history + [message], "store": False,
                    "max_output_tokens": self.settings.max_output_tokens,
                }
                if self.settings.reasoning_effort:
                    request["reasoning"] = {"effort": self.settings.reasoning_effort}
                answer = (self.client.responses.create(**request).output_text or "").strip()
                if not answer:
                    raise RuntimeError("The model returned no text.")
                updated = history + [message, {"role": "assistant", "content": answer}]
                with self.lock:
                    if token == (self.global_epoch, self.epochs.get(key, 0)):
                        limit = self.settings.max_history_messages
                        if limit:
                            self.histories[key] = updated[-limit:]
                        else:
                            self.histories.pop(key, None)
            callback(None, answer)
        except Exception as exc:
            print(f"OpenAI request failed: {type(exc).__name__}: {exc}")
            callback(str(exc), None)

    def reset(self, key: str) -> None:
        with self.lock:
            self.histories.pop(key, None)
            self.epochs[key] = self.epochs.get(key, 0) + 1

    def reset_all(self) -> None:
        with self.lock:
            self.histories.clear()
            self.global_epoch += 1

    def history_count(self) -> int:
        with self.lock:
            return len(self.histories)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


def _connect_star(uri: str):
    params = inspect.signature(websockets.sync.client.connect).parameters
    kwargs = {"max_size": None} if "max_size" in params else {}
    if "max_queue" in params:
        kwargs["max_queue"] = 4096
    return websockets.sync.client.connect(uri, **kwargs)


def _voices(raw) -> list[str]:
    result = []
    for voice in raw or []:
        value = voice.get("name") or voice.get("label") or voice.get("voice") if isinstance(voice, dict) else str(voice)
        if value:
            result.append(value)
    return result


def _request_id(value) -> str:
    text = str(value)
    parts = text.split("_")
    return parts[1] if len(parts) >= 3 else text


class StarConnection(threading.Thread):
    def __init__(self, name: str, host: str, timeout: float):
        super().__init__(name=f"star:{name}", daemon=True)
        self.display_name, self.host, self.timeout = name, host, timeout
        self.stop_event = threading.Event()
        self.outbox: queue.Queue[dict] = queue.Queue()
        self.pending: dict[str, tuple[float, Callable]] = {}
        self.voices: list[str] = []
        self.connected = False
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    def request(self, voice: str, text: str, callback: Callable) -> bool:
        if not self.connected:
            return False
        request_id = str(next(self.ids))
        self.pending[request_id] = time.time(), callback
        self.outbox.put({"user": USER_REVISION, "request": f"{voice}: {text}", "id": request_id})
        return True

    def run(self) -> None:
        last_error = ""
        while not self.stop_event.is_set():
            try:
                with _connect_star(self.host) as ws:
                    self.connected = True
                    ws.send(json.dumps({"user": USER_REVISION}))
                    print(f"Connected STAR coagulator {self.display_name}: {self.host}")
                    while not self.stop_event.is_set():
                        while True:
                            try:
                                ws.send(json.dumps(self.outbox.get_nowait()))
                            except queue.Empty:
                                break
                        now = time.time()
                        for request_id in [key for key, (created, _) in self.pending.items() if now-created > self.timeout]:
                            _, callback = self.pending.pop(request_id)
                            callback(f"Synthesis request {request_id} timed out", None, None)
                        try:
                            message = ws.recv(0.25)
                        except TimeoutError:
                            continue
                        self._handle(message)
            except websockets.exceptions.ConnectionClosedOK:
                pass
            except Exception as exc:
                if str(exc) != last_error:
                    print(f"STAR {self.display_name} reconnecting: {exc}")
                last_error = str(exc)
            finally:
                self.connected = False
                with self.lock:
                    self.voices = []
                pending, self.pending = self.pending, {}
                for _, callback in pending.values():
                    callback("STAR connection closed", None, None)
            self.stop_event.wait(3)

    def _handle(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            if len(message) < 3:
                return
            length = int.from_bytes(message[:2], "little")
            if len(message) < length + 2:
                return
            raw = message[2:length+2].decode(errors="replace")
            try:
                meta = json.loads(raw) if raw.startswith("{") else {"id": raw}
            except json.JSONDecodeError:
                return
            request_id = _request_id(meta.get("id", ""))
            if request_id in self.pending:
                _, callback = self.pending.pop(request_id)
                callback(None, meta.get("extension", "wav"), message[length+2:])
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if "voices" in payload:
            with self.lock:
                self.voices = _voices(payload["voices"])
        elif any(key in payload for key in ("status", "error", "warning")):
            request_id = _request_id(payload.get("id", ""))
            text = payload.get("status") or payload.get("error") or payload.get("warning")
            if request_id in self.pending:
                _, callback = self.pending.pop(request_id)
                callback(str(text), None, None)

    def stop(self) -> None:
        self.stop_event.set()


class StarPool:
    def __init__(self, settings: StarSettings):
        self.connections = [StarConnection(str(i+1), host, settings.request_timeout) for i, host in enumerate(settings.hosts)]

    def start(self) -> None:
        for connection in self.connections:
            connection.start()

    def stop(self) -> None:
        for connection in self.connections:
            connection.stop()

    def join(self, timeout: float | None = None) -> None:
        for connection in self.connections:
            connection.join(timeout)

    def all_voices(self) -> list[str]:
        result: dict[str, str] = {}
        for connection in self.connections:
            with connection.lock:
                for voice in connection.voices:
                    result.setdefault(voice.casefold(), voice)
        return sorted(result.values(), key=str.casefold)

    def connected_count(self) -> int:
        return sum(connection.connected for connection in self.connections)

    def request(self, voice: str, text: str, callback: Callable) -> bool:
        bare_voice = voice.partition("<")[0].strip().casefold()
        preferred = [c for c in self.connections if any(v.casefold() == bare_voice for v in c.voices)]
        for connection in preferred + [c for c in self.connections if c not in preferred]:
            if connection.request(voice, text, callback):
                return True
        return False


@dataclass
class Track:
    source: str
    name: str = ""
    kind: str = "youtube"
    is_live: bool = False

class YouTubeService:
    def __init__(self, settings: MediaSettings):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="youtube")

    def _options(self) -> dict:
        options: dict[str, object] = {
            "quiet": True, "no_warnings": True, "noprogress": True,
            "socket_timeout": 5, "source_address": "0.0.0.0",
            "format": "m4a/bestaudio/best[protocol!=m3u8_native]/best",
        }
        cookie = Path(self.settings.cookiefile_path).expanduser() if self.settings.cookiefile_path else None
        if cookie and cookie.is_file():
            options["cookiefile"] = str(cookie)
        return options

    def _explain_error(self, exc: Exception) -> RuntimeError:
        message = str(exc)
        if "sign in to confirm" in message.casefold():
            configured = Path(self.settings.cookiefile_path).expanduser() if self.settings.cookiefile_path else None
            if configured:
                return RuntimeError(
                    f"YouTube rejected the configured cookies in {configured} as expired or not fully signed in. "
                    "Export a fresh Netscape cookies.txt from a browser that is signed in to YouTube; "
                    "the export must include the Google authentication cookies as well as youtube.com cookies."
                )
            return RuntimeError(
                "YouTube requires a signed-in session for this connection. Configure media.youtube_cookiefile "
                "with a fresh Netscape cookies.txt export from a browser signed in to YouTube."
            )
        return RuntimeError(message)

    def submit_search(self, query: str, callback: Callable) -> None:
        self.executor.submit(self._search_done, query, callback)

    def _search_done(self, query: str, callback: Callable) -> None:
        try:
            result = VideosSearch(query, limit=self.settings.search_results, timeout=10).result()
            tracks = [Track(item["link"], item.get("title", ""))
                      for item in result.get("result", []) if item.get("link")]
            callback(None, tracks)
        except Exception as exc:
            callback(str(exc), None)

    def submit_url(self, url: str, callback: Callable) -> None:
        self.executor.submit(self._url_done, url, callback)

    def _url_done(self, url: str, callback: Callable) -> None:
        youtube_host = False
        try:
            if Path(url).expanduser().exists():
                path = Path(url).expanduser().resolve()
                tracks = [Track(str(item), item.name, "local") for item in (sorted(path.rglob("*")) if path.is_dir() else [path]) if item.is_file()]
                callback(None, tracks)
                return
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").casefold().removeprefix("www.")
            youtube_host = hostname in {"youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}
            if youtube_host and "list" not in parse_qs(parsed.query):
                # A single YouTube item needs only the later stream-resolution pass.
                callback(None, [Track(url, "", "youtube")])
                return
            options = self._options() | {"skip_download": True}
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
            callback(None, self._tracks_from_info(info, url))
        except Exception as exc:
            if youtube_host:
                callback(str(self._explain_error(exc)), None)
                return
            # Preserve TTMediaBot's direct-stream fallback for non-YouTube URLs.
            print(f"URL inspection failed for {url!r}; trying it as a direct stream: {exc}")
            callback(None, [Track(url, url, "direct")])

    @classmethod
    def _tracks_from_info(cls, info: dict | None, fallback: str) -> list[Track]:
        if not info:
            return [Track(fallback, fallback, "direct")]
        if info.get("_type") in {"playlist", "multi_video"} or info.get("entries") is not None:
            tracks: list[Track] = []
            for entry in info.get("entries") or []:
                tracks.extend(cls._tracks_from_info(entry, fallback))
            return tracks
        source = info.get("webpage_url") or info.get("original_url") or info.get("url") or fallback
        if info.get("ie_key") == "Youtube" and "://" not in source:
            source = f"https://www.youtube.com/watch?v={source}"
        return [Track(source, info.get("title", ""), "youtube", bool(info.get("is_live")))]

    def prepare(self, track: Track, directory: Path) -> tuple[Path | str, bool]:
        if track.kind == "local":
            return Path(track.source), False
        if track.kind == "direct":
            return track.source, False
        options = self._options() | {"noplaylist": True, "skip_download": True}
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(track.source, download=False)
                title = info.get("title") or track.name
                if title and "uploader" in info:
                    title += f" - {info['uploader']}"
                track.name = title
                track.is_live = bool(info.get("is_live"))
                stream_url = info.get("url")
                if not stream_url:
                    requested = info.get("requested_formats") or info.get("requested_downloads") or []
                    stream_url = next((item.get("url") for item in requested if item.get("url")), None)
                if not stream_url:
                    raise RuntimeError("yt-dlp did not return a playable audio stream")
                return stream_url, track.is_live
        except Exception as exc:
            raise self._explain_error(exc) from exc

    def download(self, track: Track, directory: Path) -> tuple[Path, bool]:
        options = self._options() | {
            "noplaylist": True, "outtmpl": str(directory / "media.%(ext)s"),
            "restrictfilenames": True,
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(track.source, download=True)
                if info.get("is_live"):
                    raise RuntimeError("Live streams cannot be uploaded")
                requested = info.get("requested_downloads") or []
                filename = requested[0].get("filepath") if requested else ydl.prepare_filename(info)
                return Path(filename), False
        except Exception as exc:
            raise self._explain_error(exc) from exc

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
