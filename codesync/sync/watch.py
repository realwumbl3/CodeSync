"""Settle directory events before the sync engine reads a file."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class Watcher:
    def __init__(self, root: Path, on_flush) -> None:
        self.root = root
        self._on_flush = on_flush
        self._dirty: dict[str, float] = {}
        self._moves: list[tuple[str, str, bool]] = []
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="codesync-watch", daemon=True)
        # watchdog waits one extra timeout after each change batch before it
        # hands the event over. Keep that drain short so a settled save is not
        # sitting behind a one-second delay.
        self._observer = Observer(timeout=0.02)
        self._observer.schedule(_Handler(self), str(root), recursive=True)

    def start(self) -> None:
        self._observer.start()
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._observer.stop()
        self._observer.join(timeout=2)
        self._thread.join(timeout=2)

    def mark(self, path: str) -> None:
        with self._cv:
            self._dirty[path] = time.monotonic() + 0.05
            self._cv.notify()

    def moved(self, src: str, dest: str, is_directory: bool) -> None:
        with self._cv:
            self._moves.append((src, dest, is_directory))
            self._dirty[src] = time.monotonic() + 0.05
            self._dirty[dest] = time.monotonic() + 0.05
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                if self._stop:
                    return
                if not self._dirty:
                    self._cv.wait(timeout=0.5)
                    continue
                now = time.monotonic()
                due = [path for path, when in self._dirty.items() if when <= now]
                if not due:
                    earliest = min(self._dirty.values())
                    delay = earliest - time.monotonic()
                    if delay > 0:
                        self._cv.wait(timeout=delay)
                    continue
                for path in due:
                    del self._dirty[path]
                moves = self._moves
                self._moves = []
            try:
                self._on_flush(due, moves)
            except Exception:
                with self._cv:
                    again = time.monotonic() + 0.05
                    for path in due:
                        self._dirty.setdefault(path, again)
                    self._cv.notify()


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: Watcher) -> None:
        self._watcher = watcher

    def on_created(self, event) -> None:
        self._watcher.mark(event.src_path)

    def on_modified(self, event) -> None:
        self._watcher.mark(event.src_path)

    def on_deleted(self, event) -> None:
        self._watcher.mark(event.src_path)

    def on_moved(self, event) -> None:
        self._watcher.moved(event.src_path, event.dest_path, bool(event.is_directory))
