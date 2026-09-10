"""GalactiTalk tests and opt-in CI fixtures."""
from __future__ import annotations

import os
import sys


if os.environ.get("GALACTITALK_TEST_STUB_TEAMTALK") == "1":
    from ._teamtalk_stub import build_stub

    sys.modules.setdefault("TeamTalk5", build_stub())
