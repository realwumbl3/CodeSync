"""How fast the sync window is, and what a real overlap keeps.

A quiet save is hashed once the file has been still for 50 ms, then sent.
That gap is the race: if the other side writes the same path before this
write is applied, both bodies are kept. The incoming bytes replace the path.
The bytes already on disk are copied beside it as
``name.sync-conflict-<machine>-<time>.ext``. Different paths never do this.

Run: python -m unittest tests.test_reliability -v
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from codesync.sync.node import Node


def wait_until(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def conflict_names(root: Path) -> list[str]:
    return sorted(path.name for path in root.rglob("*") if path.is_file() and ".sync-conflict-" in path.name)


def tree_bytes(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


class ReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.folder_a = root / "a"
        self.folder_b = root / "b"
        self.folder_a.mkdir()
        self.folder_b.mkdir()
        self.node_a = Node(
            root / "home-a",
            listen_port=0,
            advertise="127.0.0.1",
            auto_trust=True,
            machine_id="m-a",
            machine_name="Alpha",
        )
        self.node_b = Node(
            root / "home-b",
            listen_port=0,
            advertise="127.0.0.1",
            auto_trust=True,
            machine_id="m-b",
            machine_name="Beta",
        )
        self.node_a.start()
        self.node_b.start()
        (self.folder_a / "seed.py").write_text("seed\n", encoding="utf-8")
        link = self.node_a.create_share(self.folder_a)["link"]
        self.node_b.join_share(link, self.folder_b)
        wait_until(lambda: (self.folder_b / "seed.py").is_file())

    def tearDown(self) -> None:
        self.node_b.stop()
        self.node_a.stop()
        self._tmp.cleanup()

    def test_sequential_edits_do_not_conflict(self) -> None:
        for index in range(8):
            text = f"from-a-{index}\n"
            (self.folder_a / "note.py").write_text(text, encoding="utf-8")
            wait_until(lambda text=text: _read(self.folder_b / "note.py") == text)
            text = f"from-b-{index}\n"
            (self.folder_b / "note.py").write_text(text, encoding="utf-8")
            wait_until(lambda text=text: _read(self.folder_a / "note.py") == text)
        self.assertEqual(conflict_names(self.folder_a), [])
        self.assertEqual(conflict_names(self.folder_b), [])
        self.assertEqual((self.folder_a / "note.py").read_text(encoding="utf-8"), "from-b-7\n")

    def test_different_files_converge_without_conflicts(self) -> None:
        for index in range(12):
            (self.folder_a / f"a{index}.py").write_bytes(f"a{index}\n".encode("utf-8"))
            (self.folder_b / f"b{index}.py").write_bytes(f"b{index}\n".encode("utf-8"))
        wait_until(lambda: tree_bytes(self.folder_a) == tree_bytes(self.folder_b))
        self.assertEqual(conflict_names(self.folder_a), [])
        self.assertEqual(conflict_names(self.folder_b), [])
        merged = tree_bytes(self.folder_a)
        self.assertEqual(merged["a0.py"], b"a0\n")
        self.assertEqual(merged["b11.py"], b"b11\n")

    def test_overlap_keeps_both_bodies_then_converges(self) -> None:
        (self.folder_a / "app.py").write_text("base\n", encoding="utf-8")
        wait_until(lambda: _read(self.folder_b / "app.py") == "base\n")
        self.node_a.set_paused(self.node_a.list_shares()[0]["id"], True)
        self.node_b.set_paused(self.node_b.list_shares()[0]["id"], True)
        (self.folder_a / "app.py").write_text("alpha-body\n", encoding="utf-8")
        (self.folder_b / "app.py").write_text("beta-body\n", encoding="utf-8")
        self.node_a.set_paused(self.node_a.list_shares()[0]["id"], False)
        self.node_b.set_paused(self.node_b.list_shares()[0]["id"], False)

        def converged() -> bool:
            left = tree_bytes(self.folder_a)
            right = tree_bytes(self.folder_b)
            if left != right or _read(self.folder_a / "app.py") != _read(self.folder_b / "app.py"):
                return False
            blob = b"\n".join(left.values())
            return b"alpha-body" in blob and b"beta-body" in blob

        wait_until(converged)
        self.assertEqual((self.folder_a / "app.py").read_text(encoding="utf-8"), (self.folder_b / "app.py").read_text(encoding="utf-8"))
        self.assertTrue(conflict_names(self.folder_a) or conflict_names(self.folder_b))

    def test_in_progress_write_is_not_published(self) -> None:
        payload = b"Q" * (256 * 1024)
        peer = self.folder_b / "big.bin"
        seen: list[int] = []
        stop = threading.Event()

        def sample() -> None:
            while not stop.is_set():
                if peer.is_file():
                    seen.append(peer.stat().st_size)
                time.sleep(0.01)

        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        target = self.folder_a / "big.bin"
        with target.open("wb") as handle:
            view = memoryview(payload)
            for offset in range(0, len(payload), 8 * 1024):
                handle.write(view[offset : offset + 8 * 1024])
                handle.flush()
                time.sleep(0.008)
        wait_until(lambda: peer.is_file() and peer.read_bytes() == payload)
        stop.set()
        thread.join(timeout=2)
        partial = [size for size in seen if size not in (0, len(payload))]
        self.assertEqual(partial, [])

    def test_quiet_save_propagation(self) -> None:
        samples: list[float] = []
        for index in range(8):
            data = f"sample-{index}-{time.time_ns()}".encode("utf-8")
            data = data + b"x" * (20 * 1024 - len(data))
            started = time.perf_counter()
            (self.folder_a / "lat.py").write_bytes(data)
            wait_until(lambda data=data: (self.folder_b / "lat.py").is_file() and (self.folder_b / "lat.py").read_bytes() == data)
            samples.append(time.perf_counter() - started)
        worst = max(samples)
        report = " ".join(f"{item * 1000:.0f}" for item in samples)
        print(f"propagation ms: {report}  worst {worst * 1000:.0f}", file=sys.stderr)
        self.assertLess(worst, 0.5, f"propagation ms: {report}")
        self.assertEqual(conflict_names(self.folder_a), [])
        self.assertEqual(conflict_names(self.folder_b), [])


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


if __name__ == "__main__":
    unittest.main()
