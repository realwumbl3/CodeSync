"""CodeSync tray process. Other PCs run the frozen exe and nothing else."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .flyout.qt_runtime import load_qt
from .paths import ensure_home, is_frozen


CONTROL_PORT = 47330
_MUTEX_NAME = "Local\\CodeSync"
_ERROR_ALREADY_EXISTS = 183
_instance_mutex = None


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
    threading.Thread(target=_control_server, args=(urls, node), name="codesync-control", daemon=True).start()

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
    icon_path = home / "tray.ico"
    icon.save(icon_path, format="ICO")
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
    # Ctrl+C is only delivered while Python is running. The timer above wakes
    # the interpreter, and this handler turns that interrupt into a normal quit.
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
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
    """One tray. A share link is handed to it; a new launch replaces it."""
    if os.name != "nt":
        return True
    if _take_mutex():
        return True
    if url:
        _forward(url)
        return False
    print("Quitting the CodeSync that's already running.", flush=True)
    if _request_quit() and _wait_until_replaced(5.0):
        return True
    print("Stopping the CodeSync that did not exit.", flush=True)
    _kill_existing()
    if _wait_until_replaced(4.0):
        return True
    print("CodeSync is already running and did not exit.", flush=True)
    return False


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetLastError.argtypes = [ctypes.c_uint]
    kernel32.SetLastError.restype = None
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _take_mutex() -> bool:
    """Own the session mutex. Closing that handle would release a mutex we just created."""
    global _instance_mutex
    kernel32 = _kernel32()
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    already = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
    if already or not handle:
        if handle:
            kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def _wait_until_replaced(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _take_mutex():
            return True
        time.sleep(0.05)
    return _take_mutex()


def _request_quit() -> bool:
    """Ask the running tray to exit. Older builds accept the connection and ignore QUIT."""
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=1) as sock:
            sock.sendall(b"QUIT\n")
            sock.shutdown(socket.SHUT_WR)
            data = sock.recv(16)
    except OSError:
        return False
    return data.startswith(b"OK")


def _kill_existing() -> None:
    from .sync.node import DEFAULT_PORT

    self_pid = os.getpid()
    pids = listener_pids(_netstat_text(), {CONTROL_PORT, DEFAULT_PORT})
    pids.update(replacement_targets(_process_rows(), self_pid=self_pid, run_py=str(_run_py())))
    pids.discard(self_pid)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for pid in sorted(pids):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )


def _run_py() -> Path:
    return Path(__file__).resolve().parent.parent / "run.py"


def _netstat_text() -> str:
    try:
        return subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError):
        return ""


def _process_rows() -> list[tuple[int, str, str]]:
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^(pythonw?|CodeSync)\\.exe$' } | "
        "ForEach-Object { "
        "$cmd = ([string]$_.CommandLine) -replace '[\\r\\n]', ' '; "
        "Write-Output ($_.ProcessId.ToString() + [char]9 + $_.Name + [char]9 + $cmd) "
        "}"
    )
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=8,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[int, str, str]] = []
    for line in out.splitlines():
        pid_text, sep, rest = line.partition("\t")
        if not sep or not pid_text.strip().isdigit():
            continue
        name, sep, command = rest.partition("\t")
        if not sep:
            continue
        rows.append((int(pid_text.strip()), name.strip(), command))
    return rows


def listener_pids(netstat_text: str, ports: set[int]) -> set[int]:
    """PIDs listening on TCP `ports`, from `netstat -ano` text."""
    found: set[int] = set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING" or ":" not in parts[1]:
            continue
        try:
            port = int(parts[1].rsplit(":", 1)[-1])
            pid = int(parts[-1])
        except ValueError:
            continue
        if port in ports and pid > 0:
            found.add(pid)
    return found


def replacement_targets(
    processes: list[tuple[int, str, str]],
    *,
    self_pid: int,
    run_py: str,
) -> list[int]:
    """Other CodeSync.exe processes, and Pythons launched with this run.py."""
    needle = run_py.casefold()
    found: list[int] = []
    for pid, name, command in processes:
        if pid <= 0 or pid == self_pid:
            continue
        if name.casefold() == "codesync.exe" or (needle and needle in (command or "").casefold()):
            found.append(pid)
    return found


def _forward(url: str) -> None:
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=2) as sock:
            sock.sendall(b"JOIN " + url.encode("utf-8") + b"\n")
    except OSError:
        logging.getLogger("codesync").info("could not forward %s", url)


def _control_server(urls: queue.Queue, node) -> None:
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
            if text == "QUIT":
                try:
                    client.sendall(b"OK\n")
                    client.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                _exit(node)
            elif text.startswith("JOIN "):
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
