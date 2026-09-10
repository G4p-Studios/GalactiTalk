from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


PYTHON_MODULES = (("openai", "py7zr", "websockets", "yt_dlp", "youtubesearchpython") +
                  (("secretstorage",) if sys.platform.startswith("linux") else ()))
SYSTEM_COMMANDS = ("mpv", "pactl")


class DependencyError(RuntimeError):
    pass


def _run(command: list[str], description: str) -> None:
    print(description, flush=True)
    result = subprocess.run(command)
    if result.returncode:
        raise DependencyError(f"{description} failed with exit status {result.returncode}")


def _privileged(command: list[str], description: str) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        _run(command, description)
        return
    sudo = shutil.which("sudo")
    if not sudo:
        raise DependencyError(
            f"{description} requires administrator access, but sudo is unavailable. "
            f"Install these packages once: {' '.join(command)}"
        )
    prefix = [sudo] if sys.stdin.isatty() else [sudo, "-n"]
    _run(prefix + command, description)


def _system_install_commands(missing: set[str]) -> list[tuple[list[str], str]]:
    if shutil.which("apt-get"):
        packages = []
        if "mpv" in missing:
            packages.append("mpv")
        if "pactl" in missing:
            packages.append("pulseaudio-utils")
        return [(["apt-get", "update"], "Updating the system package index"),
                (["apt-get", "install", "-y", *packages],
                 "Installing GalactiTalk media dependencies")]
    if shutil.which("dnf"):
        packages = (["mpv"] if "mpv" in missing else []) + (["pulseaudio-utils"] if "pactl" in missing else [])
        return [(["dnf", "install", "-y", *packages], "Installing GalactiTalk media dependencies")]
    if shutil.which("pacman"):
        packages = (["mpv"] if "mpv" in missing else []) + (["libpulse"] if "pactl" in missing else [])
        return [(["pacman", "-S", "--needed", "--noconfirm", *packages],
                 "Installing GalactiTalk media dependencies")]
    if shutil.which("zypper"):
        packages = (["mpv"] if "mpv" in missing else []) + (["pulseaudio-utils"] if "pactl" in missing else [])
        return [(["zypper", "--non-interactive", "install", *packages],
                 "Installing GalactiTalk media dependencies")]
    raise DependencyError(
        "Missing required commands: " + ", ".join(sorted(missing)) +
        ". GalactiTalk could not identify a supported system package manager."
    )


def ensure_system_dependencies() -> None:
    missing = {command for command in SYSTEM_COMMANDS if not shutil.which(command)}
    if not missing:
        return
    print("GalactiTalk found missing media tools: " + ", ".join(sorted(missing)), flush=True)
    for command, description in _system_install_commands(missing):
        _privileged(command, description)
    remaining = [command for command in SYSTEM_COMMANDS if not shutil.which(command)]
    if remaining:
        raise DependencyError("Installation finished, but these commands are still unavailable: " +
                              ", ".join(remaining))


def _requirements_changed(root: Path, python: Path) -> bool:
    requirements = root / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = python.parent.parent / ".galactitalk-requirements.sha256"
    if not stamp.exists() or stamp.read_text(encoding="ascii").strip() != digest:
        return True
    probe = "import importlib.util,sys;sys.exit(any(importlib.util.find_spec(x) is None for x in " + repr(PYTHON_MODULES) + "))"
    return subprocess.run([str(python), "-c", probe], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0


def _install_python_dependencies(root: Path, python: Path) -> None:
    if not _requirements_changed(root, python):
        return
    if subprocess.run([str(python), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        _run([str(python), "-m", "ensurepip", "--upgrade"],
             "Repairing pip in GalactiTalk's private environment")
    _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r",
          str(root / "requirements.txt")], "Installing GalactiTalk Python dependencies")
    digest = hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()
    (python.parent.parent / ".galactitalk-requirements.sha256").write_text(digest + "\n", encoding="ascii")


def bootstrap(root: Path, argv: list[str]) -> None:
    """Create/use the local venv, install missing packages, then check media tools."""
    root = root.resolve()
    venv_python = root / ".venv" / "bin" / "python"
    running_python = Path(sys.executable).resolve()
    running_in_venv = Path(sys.prefix).resolve() == (root / ".venv").resolve()
    if venv_python.exists() and not running_in_venv:
        _install_python_dependencies(root, venv_python)
        os.execv(str(venv_python), [str(venv_python), str(root / "bot.py"), *argv])
    if sys.version_info < (3, 10):
        raise DependencyError("GalactiTalk requires Python 3.10 or newer")
    if not venv_python.exists():
        command = [str(running_python), "-m", "venv", str(root / ".venv")]
        print("Creating GalactiTalk's private Python environment", flush=True)
        if subprocess.run(command).returncode:
            if not shutil.which("apt-get"):
                raise DependencyError("Python could not create a virtual environment")
            _privileged(["apt-get", "update"], "Updating the system package index")
            _privileged(["apt-get", "install", "-y", "python3-venv"],
                        "Installing Python virtual-environment support")
            _run(command, "Creating GalactiTalk's private Python environment")
    if not running_in_venv:
        _install_python_dependencies(root, venv_python)
        os.execv(str(venv_python), [str(venv_python), str(root / "bot.py"), *argv])
    _install_python_dependencies(root, venv_python)
    ensure_system_dependencies()
