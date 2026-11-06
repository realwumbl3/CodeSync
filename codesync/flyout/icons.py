"""CodeSync icon. The tray and the frozen exe both use assets/icon.png."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from ..paths import is_frozen

_ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_MASTER: Image.Image | None = None


def load_tray_icon() -> Image.Image:
    """256px image whose ICO save includes every size the tray and taskbar ask for."""
    frames = {size: render_icon(size) for size in _ICO_SIZES}
    image = frames[256].copy()

    def save(fp, format=None, **kwargs):
        fmt = format if isinstance(format, str) else kwargs.get("format")
        if isinstance(fmt, str) and fmt.upper() == "ICO":
            _write_ico(fp, frames)
            return
        Image.Image.save(image, fp, format=format, **kwargs)

    image.save = save  # pystray writes an ICO via this method
    return image


def render_icon(size: int) -> Image.Image:
    master = _master()
    if master.size == (size, size):
        return master.copy()
    return master.resize((size, size), Image.Resampling.LANCZOS)


def save_application_icon(path: str | Path) -> None:
    """Write the multi-resolution icon PyInstaller embeds in CodeSync.exe."""
    frames = {size: render_icon(size) for size in _ICO_SIZES}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        _write_ico(handle, frames)


def _master() -> Image.Image:
    global _MASTER
    if _MASTER is None:
        _MASTER = Image.open(_icon_path()).convert("RGBA")
    return _MASTER


def _icon_path() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")) / "assets" / "icon.png"
    return Path(__file__).resolve().parents[2] / "assets" / "icon.png"


def _write_ico(fp, frames: dict[int, Image.Image]) -> None:
    largest = frames[max(frames)]
    rest = [frames[size] for size in sorted(frames) if frames[size].size != largest.size]
    Image.Image.save(
        largest,
        fp,
        format="ICO",
        sizes=[(size, size) for size in sorted(frames)],
        append_images=rest,
    )
