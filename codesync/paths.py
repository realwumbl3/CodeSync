"""Per-user data locations. The frozen exe keeps using LocalAppData."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def home_dir() -> Path:
    configured = os.environ.get("CODESYNC_HOME")
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "CodeSync"


def ensure_home() -> Path:
    path = home_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
