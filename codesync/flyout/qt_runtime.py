"""Verify the installed Qt runtime supports window anchoring."""

def load_qt() -> None:
    from PySide6.QtWidgets import QWidget

    if not hasattr(QWidget, "setWindowAnchor"):
        raise RuntimeError("CodeSync requires the patched PySide6 wheels in requirements.txt.")
