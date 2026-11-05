"""Push the built CodeSync exe to connected clients and restart them."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from ..paths import home_dir, is_frozen

logger = logging.getLogger("codesync")

CHUNK = 1024 * 1024
MAX_UPDATE = 256 * 1024 * 1024
_VERSION_LINE = re.compile(r'(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")')
# The onefile bootloader inherits these from the process that spawned it.
# Same exe path means "I am a child, use the parent's temp dir" — which that
# parent deletes on exit, so python312.dll is already gone.
_BOOTLOADER_ENV = (
    "_MEIPASS",
    "_MEIPASS2",
    "_PYI_APPLICATION_HOME_DIR",
    "_PYI_ARCHIVE_FILE",
    "_PYI_PARENT_PROCESS_LEVEL",
    "_PYI_SPLASH_IPC",
)


class UpgradeBuffer:
    """Assemble ordered chunks for one update. Duplicates of the current part are ignored."""

    def __init__(self) -> None:
        self.sha = ""
        self.size = 0
        self.parts = 0
        self.next_part = 0
        self._data = bytearray()

    def feed(self, header: dict, body: bytes) -> bytes | None:
        try:
            sha = str(header.get("sha256") or "")
            size = int(header.get("size") or 0)
            part = int(header.get("part") or 0)
            parts = int(header.get("parts") or 0)
        except (TypeError, ValueError):
            return None
        if not sha or size <= 0 or size > MAX_UPDATE or parts <= 0 or part < 0 or part >= parts:
            return None
        if sha != self.sha or size != self.size or parts != self.parts:
            self.sha = sha
            self.size = size
            self.parts = parts
            self.next_part = 0
            self._data = bytearray()
        if part != self.next_part:
            return None
        self._data += body
        self.next_part += 1
        if self.next_part < parts:
            return None
        if len(self._data) != self.size:
            self.next_part = 0
            self._data = bytearray()
            return None
        digest = hashlib.sha256(self._data).hexdigest()
        if digest != self.sha or not self._data.startswith(b"MZ"):
            self.next_part = 0
            self._data = bytearray()
            return None
        payload = bytes(self._data)
        self.next_part = 0
        self._data = bytearray()
        return payload


def dist_executable() -> Path | None:
    """The exe a dev checkout sends. Packaged clients never send a build."""
    if is_frozen():
        return None
    candidate = _repo_root() / "dist" / "CodeSync.exe"
    return candidate if candidate.is_file() else None


def build_dist() -> str | None:
    """Build dist\\CodeSync.exe. Returns an error message, or None when the exe is ready."""
    if is_frozen():
        return "Updates can only be sent from the dev build"
    root = _repo_root()
    script = root / "packaging" / "build.ps1"
    if not script.is_file():
        return "Build script was not found"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        lines = [line.strip() for line in (completed.stderr or completed.stdout or "").splitlines() if line.strip()]
        return lines[-1][:240] if lines else "Build failed"
    if not (root / "dist" / "CodeSync.exe").is_file():
        return "Build finished without dist\\CodeSync.exe"
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _version_file() -> Path:
    return Path(__file__).resolve().parents[1] / "__init__.py"


def read_version() -> str:
    """Version to show. Dev mode reads the source file so a build bump shows up immediately."""
    if not is_frozen():
        text = _version_file().read_text(encoding="utf-8")
        match = _VERSION_LINE.search(text)
        if match:
            return f"{match.group(2)}.{match.group(3)}.{match.group(4)}"
    from codesync import __version__

    return __version__


def bump_version(path: Path | None = None) -> str:
    """Increment the last number of 0.0.x in codesync/__init__.py."""
    path = path or _version_file()
    text = path.read_text(encoding="utf-8")
    match = _VERSION_LINE.search(text)
    if match is None:
        raise RuntimeError("Could not find __version__")
    major, minor, patch = match.group(2), match.group(3), int(match.group(4)) + 1
    updated = f"{major}.{minor}.{patch}"
    path.write_text(_VERSION_LINE.sub(rf"\g<1>{updated}\5", text, count=1), encoding="utf-8")
    return updated


def machine_key(peer_id: str) -> str:
    return peer_id.split(":", 1)[0] if peer_id else ""


def apply_executable(payload: bytes) -> None:
    """Replace this process's exe and start that copy. No-op unless this is the frozen exe."""
    if not is_frozen():
        logger.info("upgrade received while running from source; not replacing a script")
        return
    target = Path(sys.executable).resolve()
    if target.name.lower() != "codesync.exe":
        logger.info("upgrade ignored; running executable is %s", target.name)
        return
    staging = home_dir() / "CodeSync.update.exe"
    staging.write_bytes(payload)
    old = target.with_name("CodeSync.exe.old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        logger.info("could not remove previous %s", old)
    os.replace(target, old)
    os.replace(staging, target)
    _relaunch_after_exit(target, old)


def cleanup_previous_executable() -> None:
    if not is_frozen():
        return
    old = Path(sys.executable).resolve().with_name("CodeSync.exe.old")
    try:
        old.unlink()
    except OSError:
        pass


def restart_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a freshly started copy of this exe.

    PYINSTALLER_RESET_ENVIRONMENT=1 forces the bootloader to unpack again
    instead of reusing the exiting process's temporary directory.
    """
    env = dict(os.environ if base is None else base)
    for name in _BOOTLOADER_ENV:
        env.pop(name, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _restart_script(target: Path, old: Path) -> str:
    clears = [f"set {name}=" for name in _BOOTLOADER_ENV]
    return "\r\n".join(
        [
            "@echo off",
            *clears,
            "set PYINSTALLER_RESET_ENVIRONMENT=1",
            "ping 127.0.0.1 -n 4 >nul",
            f'start "" "{target}"',
            f'del /f /q "{old}"',
            'del /f /q "%~f0"',
            "",
        ]
    )


def _relaunch_after_exit(target: Path, old: Path) -> None:
    helper = home_dir() / "restart-codesync.bat"
    helper.write_text(_restart_script(target, old), encoding="utf-8")
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        env=restart_environment(),
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    os._exit(0)
