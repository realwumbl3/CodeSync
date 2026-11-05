"""CodeSync tray process. Other PCs run the frozen exe and nothing else."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import socket
import sys
import threading
from pathlib import Path

from .flyout.qt_runtime import load_qt
from .paths import ensure_home, is_frozen


CONTROL_PORT = 47330


def main() -> None:
    _lock_dpi()
    url = _argv_url()
    if not _claim_instance(url):
        return
    load_qt()
    home = ensure_home()
    _configure_log(home)
    from .sync.update import cleanup_previous_executable

    cleanup_previous_executable()
    log = logging.getLogger("codesync")
    from .sync.node import Node

    node = Node(home)
    try:
        node.start()
    except Exception:
        log.exception("CodeSync failed to listen")
        raise
    _register_protocol()
    urls: queue.Queue[str] = queue.Queue()
    if url:
        urls.put(url)
    threading.Thread(target=_control_server, args=(urls,), name="codesync-control", daemon=True).start()

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .flyout.icons import load_tray_icon
    from .flyout.tray import TrayIcon
    from .flyout.window import ControlsFlyout, MainFlyout, PeekFlyout

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("CodeSync")
    icon = load_tray_icon()
    icon_path = home / "tray.png"
    icon.save(icon_path)
    app.setWindowIcon(QIcon(str(icon_path)))

    main_flyout = MainFlyout(node)
    controls = ControlsFlyout(node, on_exit=lambda: _exit(node), launch_command=_launch_command())
    peek = PeekFlyout(node)

    def toggle_main() -> None:
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(peek, "dismiss", Qt.ConnectionType.QueuedConnection)
        QMetaObject.invokeMethod(controls, "dismiss", Qt.ConnectionType.QueuedConnection)
        QMetaObject.invokeMethod(main_flyout, "toggle_focused", Qt.ConnectionType.QueuedConnection)

    def toggle_controls() -> None:
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(peek, "dismiss", Qt.ConnectionType.QueuedConnection)
        QMetaObject.invokeMethod(main_flyout, "dismiss", Qt.ConnectionType.QueuedConnection)
        QMetaObject.invokeMethod(controls, "toggle_focused", Qt.ConnectionType.QueuedConnection)

    def hover() -> None:
        if main_flyout.isVisible() or controls.isVisible():
            return
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(peek, "notify_hover", Qt.ConnectionType.QueuedConnection)

    tray = TrayIcon(on_toggle=toggle_main, on_secondary_click=toggle_controls, on_hover=hover)
    tray.start()

    def drain_urls() -> None:
        while True:
            try:
                link = urls.get_nowait()
            except queue.Empty:
                return
            main_flyout.prefill_join(link)

    timer = QTimer()
    timer.timeout.connect(drain_urls)
    timer.start(300)
    QTimer.singleShot(200, main_flyout.toggle_focused)
    log.info("CodeSync listening on %s", node.port)
    app.exec()
    _exit(node)


def _exit(node) -> None:
    try:
        node.stop()
    except Exception:
        logging.getLogger("codesync").exception("shutdown failed")
    os._exit(0)


def _argv_url() -> str:
    for arg in sys.argv[1:]:
        if arg.lower().startswith("codesync:"):
            return arg
    return ""


def _claim_instance(url: str) -> bool:
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\CodeSync")
    already = ctypes.get_last_error() == 183
    if already:
        if url:
            _forward(url)
        return False
    return True


def _forward(url: str) -> None:
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=2) as sock:
            sock.sendall(b"JOIN " + url.encode("utf-8") + b"\n")
    except OSError:
        logging.getLogger("codesync").info("could not forward %s", url)


def _control_server(urls: queue.Queue) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", CONTROL_PORT))
    except OSError:
        return
    server.listen(4)
    server.settimeout(0.5)
    while True:
        try:
            client, _addr = server.accept()
        except TimeoutError:
            continue
        except OSError:
            return
        with client:
            data = b""
            while b"\n" not in data and len(data) < 4096:
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith("JOIN "):
            urls.put(text[5:].strip())


def _register_protocol() -> None:
    if os.name != "nt":
        return
    import winreg

    command = _launch_command() + ' "%1"'
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\codesync")
    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:CodeSync share")
    winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    command_key = winreg.CreateKey(key, r"shell\open\command")
    winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command)


def _launch_command() -> str:
    if is_frozen():
        return f'"{sys.executable}"'
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{sys.executable}" "{run_py}"'


def _lock_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _configure_log(home: Path) -> None:
    logging.basicConfig(
        filename=str(home / "codesync.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


if __name__ == "__main__":
    main()
