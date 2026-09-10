"""Discover or interactively install BearWare's separately licensed TeamTalk SDK."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from html.parser import HTMLParser
import importlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sys
import tempfile
from types import ModuleType
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


DOWNLOAD_PAGE = "https://www.bearware.dk/?page_id=419"
LICENSE_PAGE = "https://bearware.dk/?page_id=316"
SDK_DIR_ENV = "TEAMTALK_SDK_DIR"
USER_AGENT = "GalactiTalk SDK installer/1.0"
_DLL_HANDLES: list[object] = []


class TeamTalkSDKError(RuntimeError):
    pass


@dataclass(frozen=True)
class SDKDownload:
    version: str
    platform_name: str
    architecture: str
    url: str


class _SDKLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag.casefold() == "a" and href and re.search(r"/tt5sdk_v[^/]+\.7z(?:\?.*)?$", href, re.I):
            self.links.append(href)


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "GalactiTalk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GalactiTalk"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "galactitalk"


def _platform_selector() -> tuple[str, str, str]:
    machine = platform.machine().casefold()
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "Windows", "x64", "_win64.7z"
    if sys.platform == "darwin":
        return "macOS", "universal", "_macos_universal.7z"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "Linux", "x86_64", "_ubuntu22_x86_64.7z"
    if sys.platform.startswith("linux") and machine in {"aarch64", "arm64"}:
        return "Linux (Raspbian 12)", "arm64", "_raspbian_arm64.7z"
    raise TeamTalkSDKError(f"BearWare does not publish a compatible Standard SDK for {sys.platform}/{machine}.")


def discover_latest_download(timeout: float = 20) -> SDKDownload:
    platform_name, architecture, suffix = _platform_selector()
    try:
        with urlopen(Request(DOWNLOAD_PAGE, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise TeamTalkSDKError(f"Could not read BearWare's SDK download page: {exc}") from exc
    parser = _SDKLinkParser()
    parser.feed(html)
    candidates = [urljoin(DOWNLOAD_PAGE, link) for link in parser.links if urlparse(link).path.casefold().endswith(suffix)]
    if not candidates:
        raise TeamTalkSDKError(f"BearWare's download page has no Standard SDK for {platform_name} {architecture}.")
    url = candidates[0]
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"bearware.dk", "www.bearware.dk"}:
        raise TeamTalkSDKError(f"Refusing unexpected SDK URL: {url}")
    filename = unquote(Path(parsed.path).name)
    match = re.search(r"tt5sdk_v(.+?)_(?:win64|macos_universal|ubuntu22_x86_64|raspbian_arm64)\.7z$", filename)
    if not match:
        raise TeamTalkSDKError(f"Could not determine SDK version from {filename!r}.")
    return SDKDownload(match.group(1), platform_name, architecture, url)


def _safe_archive_names(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise TeamTalkSDKError(f"The SDK archive contains an unsafe path: {name!r}")


def _find_wrapper(root: Path) -> Path | None:
    matches = list(root.rglob("TeamTalk5.py")) if root.exists() else []
    preferred = [path for path in matches if path.parent.name == "TeamTalkPy"]
    return (preferred or matches or [None])[0]


def _download(item: SDKDownload, destination: Path, timeout: float = 60) -> None:
    try:
        with urlopen(Request(item.url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response, destination.open("wb") as output:
            total, received = int(response.headers.get("Content-Length", 0)), 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                if total:
                    print(f"\rDownloading TeamTalk SDK: {received*100//total:3d}%", end="", flush=True)
            if total:
                print()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise TeamTalkSDKError(f"Could not download TeamTalk SDK: {exc}") from exc


def install_sdk(download: SDKDownload | None = None, destination: Path | None = None) -> Path:
    import py7zr
    item = download or discover_latest_download()
    base = destination or _default_data_dir() / "teamtalk-sdk"
    target = base / item.version
    if wrapper := _find_wrapper(target):
        return wrapper
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="galactitalk-sdk-") as temp_name:
        temp = Path(temp_name)
        archive, extracted = temp / "sdk.7z", temp / "extracted"
        extracted.mkdir()
        _download(item, archive)
        try:
            with py7zr.SevenZipFile(archive) as seven_zip:
                _safe_archive_names(seven_zip.getnames())
                seven_zip.extractall(extracted)
        except TeamTalkSDKError:
            raise
        except Exception as exc:
            raise TeamTalkSDKError(f"Could not extract TeamTalk SDK: {exc}") from exc
        wrapper = _find_wrapper(extracted)
        if not wrapper:
            raise TeamTalkSDKError("Downloaded SDK did not contain TeamTalk5.py.")
        sdk_root = wrapper.parent.parent.parent
        staging = base / f".{item.version}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(sdk_root, staging)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    installed = _find_wrapper(target)
    if not installed:
        raise TeamTalkSDKError("The SDK was extracted but its Python wrapper is missing.")
    return installed


def _import_wrapper(wrapper: Path) -> ModuleType:
    sdk_root = wrapper.parent.parent.parent
    library = {"win32": "TeamTalk5.dll", "darwin": "libTeamTalk5.dylib"}.get(sys.platform, "libTeamTalk5.so")
    matches = list(sdk_root.rglob(library))
    if not matches:
        raise TeamTalkSDKError(f"SDK at {sdk_root} does not contain {library}.")
    native = ([path for path in matches if path.parent.name == "TeamTalk_DLL"] or matches)[0]
    if sys.platform == "win32":
        _DLL_HANDLES.append(os.add_dll_directory(str(native.parent)))
    else:
        _DLL_HANDLES.append(ctypes.CDLL(str(native), mode=getattr(ctypes, "RTLD_GLOBAL", 0)))
    sys.path.insert(0, str(wrapper.parent))
    sys.modules.pop("TeamTalk5", None)
    return importlib.import_module("TeamTalk5")


def _try_existing() -> tuple[ModuleType | None, Exception | None]:
    try:
        return importlib.import_module("TeamTalk5"), None
    except (ImportError, OSError) as last:
        sys.modules.pop("TeamTalk5", None)
    roots = []
    if configured := os.environ.get(SDK_DIR_ENV):
        roots.append(Path(configured).expanduser())
    roots.append(_default_data_dir() / "teamtalk-sdk")
    # Reuse either predecessor's user-local SDK without retaining their bot source.
    legacy = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    roots += [legacy / "tt-star" / "teamtalk-sdk", legacy / "tt-chatgpt" / "teamtalk-sdk"]
    for root in roots:
        if wrapper := _find_wrapper(root):
            try:
                return _import_wrapper(wrapper), None
            except (ImportError, OSError, TeamTalkSDKError) as exc:
                last = exc
                sys.modules.pop("TeamTalk5", None)
    return None, last


def load_teamtalk_sdk() -> ModuleType:
    module, error = _try_existing()
    if module:
        return module
    item = discover_latest_download()
    print("The TeamTalk 5 SDK was not found or could not be loaded.", file=sys.stderr)
    if error:
        print(f"Load error: {error}", file=sys.stderr)
    print(f"BearWare offers TeamTalk SDK {item.version} for {item.platform_name} {item.architecture}.\n"
          "The download is a 30-day trial; an SDK license is required for end-user applications.\n"
          f"License: {LICENSE_PAGE}\nDownload: {item.url}")
    try:
        consent = input("Download and install this SDK for your user account? [y/N]: ").strip().casefold()
    except EOFError:
        consent = ""
    if consent not in {"y", "yes"}:
        raise TeamTalkSDKError(f"SDK installation declined. Install from {DOWNLOAD_PAGE} or set {SDK_DIR_ENV}.")
    return _import_wrapper(install_sdk(item))
