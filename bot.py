#!/usr/bin/env python3
from pathlib import Path
import sys

from dependency_manager import DependencyError, bootstrap

if __name__ == "__main__":
    install_only = "--install-deps" in sys.argv[1:]
    arguments = [arg for arg in sys.argv[1:] if arg != "--install-deps"]
    try:
        bootstrap(Path(__file__).resolve().parent, arguments + (["--install-deps"] if install_only else []))
    except DependencyError as exc:
        print(f"Dependency setup failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if install_only:
        print("All GalactiTalk dependencies are installed.")
        raise SystemExit(0)
    from galactitalk.main import main

    raise SystemExit(main(arguments))
