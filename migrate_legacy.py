#!/usr/bin/env python3
"""One-time migration from the six TTMediaBots, tt-star, and tt-chatgpt."""
from __future__ import annotations

import configparser
from collections import deque
import json
from pathlib import Path
import pickle
import re

from galactitalk.config import (
    AppConfig, BotSettings, MediaSettings, OpenAISettings, ServerSettings,
    StarSettings, TeamTalkSettings, parse_bool, parse_list, write_config,
)
from galactitalk.services import Track


HOME = Path.home()
DOCUMENTS = HOME / "Documents"
STAR_DIR = HOME / "tt-star"
CHAT_DIR = HOME / "tt-chatgpt"
DESTINATION = Path(__file__).resolve().parent / "config.ini"


def ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", text.casefold()).strip("_") or "server"


def server_from_media(name: str, data: dict) -> ServerSettings:
    team = data["teamtalk"]
    users = team.get("users", {})
    return ServerSettings(
        name=safe_name(name), host=team.get("hostname", ""), tcp_port=int(team.get("tcp_port", 10333)),
        udp_port=int(team.get("udp_port", team.get("tcp_port", 10333))), encrypted=bool(team.get("encrypted")),
        nickname="GalactiTalk", previous_nickname=team.get("nickname", ""),
        username=team.get("username", ""), password=team.get("password", ""),
        channel=str(team.get("channel", "/")), channel_password=team.get("channel_password", ""),
        status=team.get("status", ""), gender=team.get("gender", "n"),
        admins=list(users.get("admins", [])), banned_users=list(users.get("banned_users", [])),
        blocked_media_commands=list(data.get("general", {}).get("blocked_commands", [])),
        start_commands=list(data.get("general", {}).get("start_commands", [])),
    )


def server_from_ini(name: str, section) -> ServerSettings:
    return ServerSettings(
        name=safe_name(name), host=section.get("host", ""), tcp_port=section.getint("tcp_port", fallback=10333),
        udp_port=section.getint("udp_port", fallback=section.getint("tcp_port", fallback=10333)),
        encrypted=parse_bool(section.get("encrypted")), nickname="GalactiTalk",
        previous_nickname=section.get("nickname", ""),
        username=section.get("username", ""), password=section.get("password", ""),
        channel=section.get("channel", "/"), channel_password=section.get("channel_password", ""),
        enabled=parse_bool(section.get("enabled"), True),
    )


def identity(server: ServerSettings) -> tuple:
    return server.host.casefold(), server.tcp_port, server.udp_port, server.channel.casefold()


def migrate_config() -> tuple[AppConfig, list[tuple[str, ServerSettings]]]:
    media_files = sorted(DOCUMENTS.glob("*/config.json"))
    media_data = [(path.parent.name, json.loads(path.read_text(encoding="utf-8"))) for path in media_files]
    star_ini = ini(STAR_DIR / "config.ini")
    chat_ini = ini(CHAT_DIR / "config.ini")
    chat = chat_ini["openai"]
    star = star_ini["star"]
    star_bot = star_ini["bot"]
    chat_bot = chat_ini["bot"]
    chat_tt = chat_ini["teamtalk"]
    sample = media_data[0][1]
    player, general = sample.get("player", {}), sample.get("general", {})
    yt = sample.get("services", {}).get("yt", {})
    config = AppConfig(
        openai=OpenAISettings(
            model=chat.get("model", "gpt-5.6-luna"), api_key_env=chat.get("api_key_env", "OPENAI_API_KEY"),
            api_key=chat.get("api_key", ""), base_url=chat.get("base_url", ""),
            instructions=chat.get("instructions", ""), timeout_seconds=chat.getfloat("timeout_seconds", fallback=90),
            max_output_tokens=chat.getint("max_output_tokens", fallback=700),
            reasoning_effort=chat.get("reasoning_effort", ""), max_workers=chat.getint("max_workers", fallback=4),
            max_history_messages=chat.getint("max_history_messages", fallback=20),
        ),
        star=StarSettings(
            hosts=parse_list(star.get("hosts", "")), request_timeout=star.getfloat("request_timeout", fallback=60),
            default_voice=star_bot.get("default_voice", ""), voice_preview_text=star_bot.get("voice_preview_text", "Hello there, my name is {voice}."),
            max_voice_results=star_bot.getint("max_voice_results", fallback=20), temp_dir=star_bot.get("temp_dir", ""),
        ),
        media=MediaSettings(
            default_volume=int(player.get("default_volume", 50)), max_volume=int(player.get("max_volume", 100)),
            seek_step=int(player.get("seek_step", 5)), cookiefile_path=yt.get("cookiefile_path", ""),
            delete_uploaded_files_after=max(0, int(general.get("delete_uploaded_files_after", 300))),
            send_channel_messages=bool(general.get("send_channel_messages", True)),
        ),
        bot=BotSettings(
            max_prompt_chars=chat_bot.getint("max_prompt_chars", fallback=6000),
            max_pending_per_server=chat_bot.getint("max_pending_per_server", fallback=8),
        ),
        teamtalk=TeamTalkSettings(
            client_name="GalactiTalk", reconnect_seconds=chat_tt.getfloat("reconnect_seconds", fallback=5),
            license_name=chat_tt.get("license_name", ""), license_key=chat_tt.get("license_key", ""),
        ),
    )
    chosen: dict[tuple, ServerSettings] = {}
    legacy: list[tuple[str, ServerSettings]] = []
    for source, data in media_data:
        server = server_from_media(source, data)
        chosen[identity(server)] = server
    for prefix, parser in (("star", star_ini), ("chatgpt", chat_ini)):
        for section_name in parser.sections():
            if not section_name.startswith("server:"):
                continue
            source = f"{prefix}_{section_name.split(':', 1)[1]}"
            server = server_from_ini(source, parser[section_name])
            key = identity(server)
            if key in chosen:
                legacy.append((source, server))
            else:
                chosen[key] = server
    config.servers = list(chosen.values())
    return config, legacy


class LegacyTrack:
    def __setstate__(self, state):
        self.__dict__.update(state)


class LegacyEnum:
    def __new__(cls, value=None):
        obj = object.__new__(cls)
        obj.value = value
        return obj


class LegacyObject(dict):
    def __new__(cls, *args, **kwargs):
        return dict.__new__(cls)

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)


class RestrictedUnpickler(pickle.Unpickler):
    ALLOWED = {("collections", "deque"): deque}
    def find_class(self, module, name):
        if (module, name) in self.ALLOWED:
            return self.ALLOWED[module, name]
        if module == "bot.player.track" and name == "Track":
            return LegacyTrack
        if module == "bot.player.enums" and name == "TrackType":
            return LegacyEnum
        if module.startswith("yt_dlp."):
            return LegacyObject
        raise pickle.UnpicklingError(f"blocked legacy type {module}.{name}")


def converted(item) -> Track | None:
    source = getattr(item, "_url", "") or getattr(item, "url", "")
    if not isinstance(source, str) or not source:
        extra = getattr(item, "extra_info", None) or {}
        source = extra.get("webpage_url") or extra.get("original_url") or ""
    if not source:
        return None
    name = getattr(item, "_name", "") or ""
    service = getattr(item, "service", "")
    kind = "youtube" if service == "yt" or "youtu" in source else ("local" if Path(source).exists() else "direct")
    return Track(source, name, kind)


def migrate_cache(destination: Path) -> tuple[int, int]:
    recents: list[Track] = []
    favorites: dict[str, list[Track]] = {}
    for cache_path in sorted(DOCUMENTS.glob("*/cache.dat")):
        try:
            with cache_path.open("rb") as stream:
                data = RestrictedUnpickler(stream).load()
        except Exception as exc:
            print(f"Warning: could not migrate {cache_path}: {exc}")
            continue
        for old in data.get("recents", []):
            if track := converted(old): recents.append(track)
        for username, tracks in data.get("favorites", {}).items():
            target = favorites.setdefault(username, [])
            for old in tracks:
                if track := converted(old): target.append(track)
    # Preserve more than the runtime window in the migration artifact; runtime trims new additions.
    destination.write_text(json.dumps({
        "recents": [vars(track) for track in recents],
        "favorites": {user: [vars(track) for track in tracks] for user, tracks in favorites.items()},
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(recents), sum(map(len, favorites.values()))


def main() -> int:
    config, legacy = migrate_config()
    write_config(DESTINATION, config)
    parser = ini(DESTINATION)
    for source, server in legacy:
        parser[f"legacy-account:{safe_name(source)}"] = {
            "enabled": "no", "host": server.host, "tcp_port": str(server.tcp_port),
            "udp_port": str(server.udp_port), "encrypted": "yes" if server.encrypted else "no",
            "nickname": server.nickname, "username": server.username, "password": server.password,
            "previous_nickname": server.previous_nickname,
            "channel": server.channel, "channel_password": server.channel_password,
            "source_feature": "preserved alternate account",
        }
    with DESTINATION.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    DESTINATION.chmod(0o600)
    recents, favorites = migrate_cache(DESTINATION.parent / config.media.cache_file)
    print(f"Migrated {len(config.servers)} unique TeamTalk connections and preserved {len(legacy)} alternate accounts.")
    print(f"Migrated {recents} recent entries and {favorites} favorite entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
