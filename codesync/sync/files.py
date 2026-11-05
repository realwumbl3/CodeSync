"""Content hashes, NTFS file ids, and crash-safe replacements."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def replace_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".codesync-tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def conflict_copy(path: Path, machine: str) -> Path | None:
    if not path.is_file():
        return None
    stamp = _stamp()
    label = re.sub(r"[^A-Za-z0-9_-]", "", machine)[:24] or "peer"
    suffix = path.suffix
    stem = path.stem if suffix else path.name
    extra = suffix if suffix else ""
    target = path.with_name(f"{stem}.sync-conflict-{label}-{stamp}{extra}")
    index = 2
    while target.exists():
        target = path.with_name(f"{stem}.sync-conflict-{label}-{stamp}-{index}{extra}")
        index += 1
    target.write_bytes(path.read_bytes())
    return target


def _stamp() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")


def safe_path(root: Path, rel: str) -> Path:
    text = rel.replace("\\", "/").strip("/")
    if not text or text.startswith("/") or ":" in text:
        raise ValueError(rel)
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(rel)
    candidate = root.joinpath(*parts)
    root_resolved = root.resolve()
    parent = candidate.parent.resolve()
    if parent != root_resolved and root_resolved not in parent.parents:
        raise ValueError(rel)
    return candidate


def relative_of(root: Path, path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    text = rel.as_posix()
    if not text or text == ".":
        return None
    return text


def file_id(path: Path) -> str:
    if sys.platform != "win32" or not path.exists():
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.restype = wintypes.BOOL
    share = 0x1 | 0x2 | 0x4
    flags = 0x02000000
    handle = kernel32.CreateFileW(str(path), 0x80000000, share, None, 3, flags, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        return ""

    class _Info(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    info = _Info()
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    ok = kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
    kernel32.CloseHandle(handle)
    if not ok:
        return ""
    return f"{info.dwVolumeSerialNumber:08x}-{info.nFileIndexHigh:08x}{info.nFileIndexLow:08x}"
