from __future__ import annotations

import configparser
import getpass
import math
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_INSTRUCTIONS = (
    "You are a helpful assistant in a public TeamTalk text channel. Answer clearly "
    "and concisely. Do not use Markdown tables. Remember that messages may be read "
    "aloud by screen readers."
)


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().casefold() in {"1", "yes", "y", "true", "on"}


def parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def prompt(message: str, default: str = "", *, secret: bool = False) -> str:
    suffix = " [configured]" if secret and default else (f" [{default}]" if default else "")
    reader = getpass.getpass if secret else input
    value = reader(f"{message}{suffix}: ").strip()
    return value if value else default


def prompt_bool(message: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{message} [{suffix}]: ").strip().casefold()
        if not value:
            return default
        if value in {"y", "yes", "true", "1", "on"}:
            return True
        if value in {"n", "no", "false", "0", "off"}:
            return False
        print("Please enter yes or no.")


def prompt_int(message: str, default: int, minimum: int = 0) -> int:
    while True:
        try:
            value = int(prompt(message, str(default)))
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < minimum:
            print(f"Please enter {minimum} or greater.")
            continue
        return value


@dataclass
class OpenAISettings:
    model: str = "gpt-5.6-luna"
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str = ""
    base_url: str = ""
    instructions: str = DEFAULT_INSTRUCTIONS
    timeout_seconds: float = 90.0
    max_output_tokens: int = 700
    reasoning_effort: str = ""
    max_workers: int = 4
    max_history_messages: int = 20


@dataclass
class StarSettings:
    hosts: list[str] = field(default_factory=lambda: ["ws://localhost:7774"])
    request_timeout: float = 60.0
    default_voice: str = ""
    voice_preview_text: str = "Hello there, my name is {voice}."
    max_voice_results: int = 20
    temp_dir: str = ""


@dataclass
class MediaSettings:
    default_volume: int = 50
    max_volume: int = 100
    seek_step: int = 5
    default_mode: str = "tl"
    search_results: int = 300
    cookiefile_path: str = ""
    cache_file: str = "galactitalk-cache.json"
    delete_uploaded_files_after: int = 300
    send_channel_messages: bool = True
    temp_dir: str = ""


@dataclass
class BotSettings:
    max_prompt_chars: int = 6000
    max_pending_per_server: int = 8


@dataclass
class TeamTalkSettings:
    client_name: str = "GalactiTalk"
    reconnect_seconds: float = 5.0
    license_name: str = ""
    license_key: str = ""


@dataclass
class ServerSettings:
    name: str
    host: str
    tcp_port: int = 10333
    udp_port: int = 10333
    encrypted: bool = False
    nickname: str = "GalactiTalk"
    previous_nickname: str = ""
    username: str = ""
    password: str = ""
    channel: str = "/"
    channel_password: str = ""
    enabled: bool = True
    status: str = ""
    gender: str = "n"
    start_commands: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    openai: OpenAISettings = field(default_factory=OpenAISettings)
    star: StarSettings = field(default_factory=StarSettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    bot: BotSettings = field(default_factory=BotSettings)
    teamtalk: TeamTalkSettings = field(default_factory=TeamTalkSettings)
    servers: list[ServerSettings] = field(default_factory=list)


def read_config(path: Path) -> AppConfig:
    p = configparser.ConfigParser(interpolation=None)
    p.read(path, encoding="utf-8")
    ai = p["openai"] if p.has_section("openai") else {}
    star = p["star"] if p.has_section("star") else {}
    media = p["media"] if p.has_section("media") else {}
    bot = p["bot"] if p.has_section("bot") else {}
    teamtalk = p["teamtalk"] if p.has_section("teamtalk") else {}
    config = AppConfig(
        openai=OpenAISettings(
            model=ai.get("model", "gpt-5.6-luna").strip() or "gpt-5.6-luna",
            api_key_env=ai.get("api_key_env", "OPENAI_API_KEY").strip(),
            api_key=ai.get("api_key", "").strip(), base_url=ai.get("base_url", "").strip(),
            instructions=ai.get("instructions", DEFAULT_INSTRUCTIONS).strip() or DEFAULT_INSTRUCTIONS,
            timeout_seconds=float(ai.get("timeout_seconds", 90)),
            max_output_tokens=int(ai.get("max_output_tokens", 700)),
            reasoning_effort=ai.get("reasoning_effort", "").strip().casefold(),
            max_workers=int(ai.get("max_workers", 4)),
            max_history_messages=int(ai.get("max_history_messages", 20)),
        ),
        star=StarSettings(
            hosts=parse_list(star.get("hosts", "ws://localhost:7774")),
            request_timeout=float(star.get("request_timeout", 60)),
            default_voice=star.get("default_voice", "").strip(),
            voice_preview_text=star.get("voice_preview_text", "Hello there, my name is {voice}."),
            max_voice_results=int(star.get("max_voice_results", 20)),
            temp_dir=star.get("temp_dir", "").strip(),
        ),
        media=MediaSettings(
            default_volume=int(media.get("default_volume", 50)),
            max_volume=int(media.get("max_volume", 100)), seek_step=int(media.get("seek_step", 5)),
            default_mode=media.get("default_mode", "tl").strip().casefold(),
            search_results=int(media.get("search_results", 300)),
            cookiefile_path=media.get("youtube_cookiefile", media.get("cookiefile_path", "")).strip(),
            cache_file=media.get("cache_file", "galactitalk-cache.json").strip(),
            delete_uploaded_files_after=int(media.get("delete_uploaded_files_after", 300)),
            send_channel_messages=parse_bool(media.get("send_channel_messages"), True),
            temp_dir=media.get("temp_dir", "").strip(),
        ),
        bot=BotSettings(
            max_prompt_chars=int(bot.get("max_prompt_chars", 6000)),
            max_pending_per_server=int(bot.get("max_pending_per_server", 8)),
        ),
        teamtalk=TeamTalkSettings(
            client_name=teamtalk.get("client_name", "GalactiTalk"),
            reconnect_seconds=float(teamtalk.get("reconnect_seconds", 5)),
            license_name=teamtalk.get("license_name", ""), license_key=teamtalk.get("license_key", ""),
        ),
    )
    for section_name in p.sections():
        if not section_name.casefold().startswith("server:"):
            continue
        s = p[section_name]
        if not s.get("host", "").strip():
            continue
        config.servers.append(ServerSettings(
            name=section_name.split(":", 1)[1].strip() or f"server{len(config.servers)+1}",
            host=s.get("host", "").strip(), tcp_port=int(s.get("tcp_port", 10333)),
            udp_port=int(s.get("udp_port", s.get("tcp_port", 10333))),
            encrypted=parse_bool(s.get("encrypted")), nickname=s.get("nickname", "GalactiTalk").strip() or "GalactiTalk",
            previous_nickname=s.get("previous_nickname", "").strip(),
            username=s.get("username", ""), password=s.get("password", ""), channel=s.get("channel", "/"),
            channel_password=s.get("channel_password", ""), enabled=parse_bool(s.get("enabled"), True),
            status=s.get("status", ""), gender=s.get("gender", "n").strip().casefold(),
            start_commands=parse_list(s.get("start_commands", "")),
        ))
    return config


def write_config(path: Path, config: AppConfig) -> None:
    p = configparser.ConfigParser(interpolation=None)
    if path.exists():
        p.read(path, encoding="utf-8")
        for section in list(p.sections()):
            if section in {"openai", "star", "media", "bot", "teamtalk"} or section.casefold().startswith("server:"):
                p.remove_section(section)
    p["openai"] = {k: str(v) for k, v in vars(config.openai).items()}
    p["star"] = {**{k: str(v) for k, v in vars(config.star).items() if k != "hosts"},
                 "hosts": "\n".join(config.star.hosts)}
    p["media"] = {
        "default_volume": str(config.media.default_volume), "max_volume": str(config.media.max_volume),
        "seek_step": str(config.media.seek_step), "default_mode": config.media.default_mode,
        "search_results": str(config.media.search_results), "youtube_cookiefile": config.media.cookiefile_path,
        "cache_file": config.media.cache_file,
        "delete_uploaded_files_after": str(config.media.delete_uploaded_files_after),
        "send_channel_messages": "yes" if config.media.send_channel_messages else "no",
        "temp_dir": config.media.temp_dir,
    }
    p["bot"] = {k: str(v) for k, v in vars(config.bot).items()}
    p["teamtalk"] = {k: str(v) for k, v in vars(config.teamtalk).items()}
    for server in config.servers:
        p[f"server:{server.name}"] = {
            "enabled": "yes" if server.enabled else "no", "host": server.host,
            "tcp_port": str(server.tcp_port), "udp_port": str(server.udp_port),
            "encrypted": "yes" if server.encrypted else "no", "nickname": server.nickname,
            "previous_nickname": server.previous_nickname,
            "username": server.username, "password": server.password, "channel": server.channel,
            "channel_password": server.channel_password,
            "status": server.status, "gender": server.gender,
            "start_commands": ", ".join(server.start_commands),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        p.write(stream)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def validate_config(config: AppConfig) -> list[str]:
    errors: list[str] = []
    if not config.servers:
        errors.append("No TeamTalk servers are configured.")
    if config.openai.max_workers < 1 or config.openai.max_output_tokens < 1:
        errors.append("OpenAI worker and output limits must be at least 1.")
    if config.openai.max_history_messages < 0:
        errors.append("openai.max_history_messages cannot be negative.")
    if config.openai.reasoning_effort not in {"", "none", "low", "medium", "high", "xhigh", "max"}:
        errors.append("openai.reasoning_effort is not recognized.")
    if not math.isfinite(config.openai.timeout_seconds) or config.openai.timeout_seconds <= 0:
        errors.append("openai.timeout_seconds must be positive and finite.")
    if not math.isfinite(config.teamtalk.reconnect_seconds) or config.teamtalk.reconnect_seconds <= 0:
        errors.append("teamtalk.reconnect_seconds must be positive and finite.")
    if not 0 <= config.media.default_volume <= config.media.max_volume or config.media.max_volume < 1:
        errors.append("Media volume settings are invalid.")
    if config.media.default_mode not in {"st", "rt", "tl", "rtl", "rnd"}:
        errors.append("media.default_mode must be st, rt, tl, rtl, or rnd.")
    names: set[str] = set()
    for server in config.servers:
        if server.name.casefold() in names:
            errors.append(f"Duplicate server section name: {server.name}")
        names.add(server.name.casefold())
        if not 1 <= server.tcp_port <= 65535 or not 1 <= server.udp_port <= 65535:
            errors.append(f"Server {server.name} has an invalid port.")
    return errors


def configure(path: Path) -> None:
    old = read_config(path) if path.exists() else AppConfig()
    print("GalactiTalk interactive configuration")
    print(f"Writing {path}")
    store_key = prompt_bool("Store an API key in this config file", bool(old.openai.api_key))
    key = prompt("OpenAI API key", old.openai.api_key, secret=True) if store_key else ""
    effort = prompt("Reasoning effort (blank, none, low, medium, high, xhigh, or max)", old.openai.reasoning_effort).casefold()
    if effort not in {"", "none", "low", "medium", "high", "xhigh", "max"}:
        print("Unknown reasoning effort; leaving it unset.")
        effort = ""
    ai = OpenAISettings(
        model=prompt("OpenAI model", old.openai.model), api_key_env=prompt("API key environment variable", old.openai.api_key_env),
        api_key=key, base_url=prompt("Custom OpenAI-compatible base URL (blank for OpenAI)", old.openai.base_url),
        instructions=prompt("Assistant instructions", old.openai.instructions),
        timeout_seconds=float(prompt_int("API timeout in seconds", int(old.openai.timeout_seconds), 1)),
        max_output_tokens=prompt_int("Maximum output tokens", old.openai.max_output_tokens, 1), reasoning_effort=effort,
        max_workers=prompt_int("Maximum simultaneous API requests", old.openai.max_workers, 1),
        max_history_messages=prompt_int("Conversation history messages per channel", old.openai.max_history_messages, 0),
    )
    print("\nSTAR settings")
    hosts = prompt("STAR WebSocket hosts (comma-separated)", ", ".join(old.star.hosts))
    star = StarSettings(
        hosts=parse_list(hosts), request_timeout=float(prompt_int("STAR request timeout in seconds", int(old.star.request_timeout), 1)),
        default_voice=prompt("Default STAR voice", old.star.default_voice),
        voice_preview_text=prompt("Voice preview text", old.star.voice_preview_text),
        max_voice_results=prompt_int("Maximum voice search results", old.star.max_voice_results, 1),
        temp_dir=prompt("Temporary audio directory (blank for system default)", old.star.temp_dir),
    )
    print("\nYouTube media settings")
    media = MediaSettings(
        default_volume=prompt_int("Default media volume", old.media.default_volume, 0),
        max_volume=prompt_int("Maximum media volume", old.media.max_volume, 1),
        seek_step=prompt_int("Default seek step in seconds", old.media.seek_step, 1), default_mode=old.media.default_mode,
        search_results=prompt_int("Maximum YouTube search results", old.media.search_results, 1),
        cookiefile_path=prompt("YouTube cookie file path", old.media.cookiefile_path), cache_file=old.media.cache_file,
        delete_uploaded_files_after=prompt_int("Delete uploaded files after seconds (0 keeps them)", old.media.delete_uploaded_files_after, 0),
        send_channel_messages=prompt_bool("Announce media requests in the channel", old.media.send_channel_messages),
        temp_dir=prompt("Media temporary directory (blank for system default)", old.media.temp_dir),
    )
    bot = BotSettings(
        max_prompt_chars=prompt_int("Maximum ChatGPT prompt characters", old.bot.max_prompt_chars, 1),
        max_pending_per_server=prompt_int("Maximum queued ChatGPT requests per server", old.bot.max_pending_per_server, 1),
    )
    teamtalk = TeamTalkSettings(
        client_name=prompt("TeamTalk client name", old.teamtalk.client_name),
        reconnect_seconds=float(prompt_int("Reconnect delay in seconds", int(old.teamtalk.reconnect_seconds), 1)),
        license_name=prompt("TeamTalk SDK license name, if required", old.teamtalk.license_name),
        license_key=prompt("TeamTalk SDK license key, if required", old.teamtalk.license_key, secret=True),
    )
    servers: list[ServerSettings] = []
    count = prompt_int("How many TeamTalk servers should the bot connect to", len(old.servers) or 1, 1)
    for index in range(count):
        prior = old.servers[index] if index < len(old.servers) else ServerSettings(f"server{index+1}", "")
        print(f"\nTeamTalk server {index+1}")
        servers.append(ServerSettings(
            name=prompt("Config section name", prior.name), host=prompt("Host address", prior.host),
            tcp_port=prompt_int("TCP port", prior.tcp_port, 1), udp_port=prompt_int("UDP port", prior.udp_port, 1),
            encrypted=prompt_bool("Encrypted connection", prior.encrypted), nickname=prompt("Bot nickname", prior.nickname),
            username=prompt("Username", prior.username), password=prompt("Password", prior.password, secret=True),
            channel=prompt("Channel path to join", prior.channel),
            channel_password=prompt("Channel password", prior.channel_password, secret=True),
            enabled=prompt_bool("Enable this server", prior.enabled),
            status=prompt("Status text", prior.status), gender=prompt("Gender (n, m, or f)", prior.gender).casefold(),
            start_commands=parse_list(prompt("Startup media commands (comma-separated)", ", ".join(prior.start_commands))),
        ))
    write_config(path, AppConfig(ai, star, media, bot, teamtalk, servers))
    print(f"Configuration written to {path}")


def remove_deprecated_media_controls(path: Path) -> bool:
    """Remove retired TTMediaBot moderation options without rewriting secrets."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    changed = False
    for section in parser.sections():
        if not section.casefold().startswith("server:"):
            continue
        for option in ("admins", "banned_users", "blocked_media_commands"):
            if parser.remove_option(section, option):
                changed = True
    if changed:
        with path.open("w", encoding="utf-8") as stream:
            parser.write(stream)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return changed
