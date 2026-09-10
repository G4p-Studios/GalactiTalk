from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import sys

from .bot import Connection, RuntimeControl, tt
from .config import configure, read_config, validate_config
from .services import MediaCache, OpenAIService, StarPool, YouTubeService


EXIT_RESTART = 75
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.ini"


def run(config_path: Path) -> int:
    try:
        config = read_config(config_path)
    except (ValueError, configparser.Error) as exc:
        print(f"Invalid configuration: {exc}")
        return 2
    errors = validate_config(config)
    if errors:
        for error in errors:
            print(f"Configuration error: {error}")
        return 2
    if config.teamtalk.license_name or config.teamtalk.license_key:
        tt.setLicense(tt.ttstr(config.teamtalk.license_name), tt.ttstr(config.teamtalk.license_key))
    try:
        ai = OpenAIService(config.openai)
    except ValueError as exc:
        print(f"ChatGPT disabled: {exc}")
        ai = None
    star = StarPool(config.star)
    youtube = YouTubeService(config.media)
    cache_path = Path(config.media.cache_file).expanduser()
    if not cache_path.is_absolute():
        cache_path = config_path.parent / cache_path
    cache = MediaCache(cache_path)
    control = RuntimeControl()
    connections = [Connection(server, config, config_path, ai, star, youtube, cache, control)
                   for server in config.servers if server.enabled]
    if not connections:
        print("All TeamTalk server sections are disabled.")
        return 2
    star.start()
    for connection in connections:
        connection.start()
    print(f"GalactiTalk is running on {len(connections)} TeamTalk connection(s). Press Ctrl+C to stop.")
    try:
        while not control.stop_event.wait(1):
            pass
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        for connection in connections:
            connection.stop()
        star.stop()
        for connection in connections:
            connection.join(5)
        star.join(2)
        youtube.close()
        if ai:
            ai.close()
    return EXIT_RESTART if control.restart else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GalactiTalk multi-server TeamTalk bot")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to the single INI configuration")
    parser.add_argument("--configure", action="store_true", help="Run the complete interactive configuration wizard")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration without connecting")
    args = parser.parse_args(argv)
    path = Path(args.config).expanduser().resolve()
    if args.configure:
        configure(path)
        return 0
    if not path.exists():
        print(f"Configuration file not found: {path}. Run with --configure first.")
        return 2
    if args.check_config:
        try:
            errors = validate_config(read_config(path))
        except (ValueError, configparser.Error) as exc:
            print(f"Invalid configuration: {exc}")
            return 2
        if errors:
            for error in errors:
                print(f"Configuration error: {error}")
            return 2
        print("Configuration is valid.")
        return 0
    code = run(path)
    if code == EXIT_RESTART:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    return code
