import shutil
import tempfile
import time
import unittest
from pathlib import Path

from codesync.sync.node import Node


def wait_until(predicate, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


class SyncTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.node_b.stop()
        self.node_a.stop()
        self._tmp.cleanup()

    def test_sync_ignore_edit_rename_and_conflict(self) -> None:
        (self.folder_a / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (self.folder_a / ".git").mkdir()
        (self.folder_a / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (self.folder_a / ".git" / "index.lock").write_text("lock", encoding="utf-8")
        (self.folder_a / "note.log").write_text("local only\n", encoding="utf-8")
        (self.folder_a / "main.py").write_text("print('v1')\n", encoding="utf-8")
        link = self.node_a.create_share(self.folder_a)["link"]
        self.node_b.join_share(link, self.folder_b)

        def copied() -> bool:
            return (self.folder_b / "main.py").is_file() and (self.folder_b / ".gitignore").is_file()

        wait_until(copied)
        wait_until(lambda: (self.folder_b / ".git" / "HEAD").is_file())
        self.assertFalse((self.folder_b / "note.log").exists())
        self.assertEqual(
            (self.folder_b / ".git" / "HEAD").read_text(encoding="utf-8"),
            "ref: refs/heads/main\n",
        )
        self.assertFalse((self.folder_b / ".git" / "index.lock").exists())
        self.assertEqual((self.folder_b / "main.py").read_text(encoding="utf-8"), "print('v1')\n")

        started = time.monotonic()
        (self.folder_a / "main.py").write_text("print('v2')\n", encoding="utf-8")
        wait_until(lambda: (self.folder_b / "main.py").is_file() and (self.folder_b / "main.py").read_text(encoding="utf-8") == "print('v2')\n")
        self.assertLess(time.monotonic() - started, 2.0)

        (self.folder_a / "main.py").rename(self.folder_a / "app.py")
        wait_until(lambda: (self.folder_b / "app.py").is_file() and not (self.folder_b / "main.py").exists())
        self.assertEqual((self.folder_b / "app.py").read_text(encoding="utf-8"), "print('v2')\n")

        share_b = self.node_b.list_shares()[0]["id"]
        self.node_b.set_paused(share_b, True)
        (self.folder_a / "app.py").write_text("from A\n", encoding="utf-8")
        time.sleep(0.3)
        (self.folder_b / "app.py").write_text("from B\n", encoding="utf-8")
        self.node_b.set_paused(share_b, False)

        def settled() -> bool:
            if not (self.folder_a / "app.py").is_file() or not (self.folder_b / "app.py").is_file():
                return False
            if (self.folder_a / "app.py").read_text(encoding="utf-8") != "from B\n":
                return False
            if (self.folder_b / "app.py").read_text(encoding="utf-8") != "from B\n":
                return False
            return any(
                path.name.startswith("app.sync-conflict-") and not path.name.endswith(".codesync-tmp")
                for path in self.folder_b.iterdir()
            )

        wait_until(settled, timeout=8)
        conflict = next(
            path
            for path in self.folder_b.iterdir()
            if path.name.startswith("app.sync-conflict-") and not path.name.endswith(".codesync-tmp")
        )
        self.assertEqual(conflict.read_text(encoding="utf-8"), "from A\n")

    def test_nested_git_is_copied(self) -> None:
        repo = self.folder_a / "app"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (repo / ".git" / "index.lock").write_text("lock", encoding="utf-8")
        (repo / "main.py").write_text("print('v1')\n", encoding="utf-8")
        link = self.node_a.create_share(self.folder_a)["link"]
        self.node_b.join_share(link, self.folder_b)

        wait_until(lambda: (self.folder_b / "app" / ".git" / "HEAD").is_file())
        self.assertEqual(
            (self.folder_b / "app" / ".git" / "HEAD").read_text(encoding="utf-8"),
            "ref: refs/heads/main\n",
        )
        self.assertFalse((self.folder_b / "app" / ".git" / "index.lock").exists())
        self.assertTrue((repo / ".git" / "HEAD").is_file())

    def test_empty_folders_sync_and_removed_folders_disappear(self) -> None:
        (self.folder_a / "empty").mkdir()
        (self.folder_a / "box" / "sub").mkdir(parents=True)
        (self.folder_a / "box" / "note.txt").write_text("hi\n", encoding="utf-8")
        link = self.node_a.create_share(self.folder_a)["link"]
        self.node_b.join_share(link, self.folder_b)

        wait_until(
            lambda: (self.folder_b / "empty").is_dir()
            and (self.folder_b / "box" / "sub").is_dir()
            and (self.folder_b / "box" / "note.txt").is_file()
        )

        (self.folder_a / "box").rename(self.folder_a / "crate")
        wait_until(
            lambda: (self.folder_b / "crate" / "note.txt").is_file()
            and (self.folder_b / "crate" / "sub").is_dir()
            and not (self.folder_b / "box").exists()
        )

        (self.folder_a / "crate" / "note.txt").unlink()
        (self.folder_a / "tick.txt").write_text("1\n", encoding="utf-8")
        wait_until(
            lambda: (self.folder_b / "tick.txt").is_file()
            and not (self.folder_b / "crate" / "note.txt").exists()
        )
        self.assertTrue((self.folder_b / "crate").is_dir())
        self.assertTrue((self.folder_b / "crate" / "sub").is_dir())

        shutil.rmtree(self.folder_a / "crate")
        (self.folder_a / "empty").rmdir()
        wait_until(lambda: not (self.folder_b / "crate").exists() and not (self.folder_b / "empty").exists())


class SameProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.folder_a = root / "a"
        self.folder_b = root / "b"
        self.folder_a.mkdir()
        self.folder_b.mkdir()
        self.node = Node(
            root / "home",
            listen_port=0,
            advertise="127.0.0.1",
            auto_trust=True,
            machine_id="m-local",
            machine_name="Local",
        )
        self.node.start()

    def tearDown(self) -> None:
        self.node.stop()
        self._tmp.cleanup()

    def test_one_process_syncs_two_folders(self) -> None:
        (self.folder_a / "main.py").write_text("print('v1')\n", encoding="utf-8")
        link = self.node.create_share(self.folder_a)["link"]
        self.node.join_share(link, self.folder_b)
        self.assertEqual(len(self.node.list_shares()), 2)

        wait_until(
            lambda: (self.folder_b / "main.py").is_file()
            and (self.folder_b / "main.py").read_text(encoding="utf-8") == "print('v1')\n"
        )

        (self.folder_b / "main.py").write_text("print('v2')\n", encoding="utf-8")
        wait_until(lambda: (self.folder_a / "main.py").read_text(encoding="utf-8") == "print('v2')\n")

        (self.folder_a / "extra.py").write_text("x\n", encoding="utf-8")
        wait_until(
            lambda: (self.folder_b / "extra.py").is_file()
            and (self.folder_b / "extra.py").read_text(encoding="utf-8") == "x\n"
        )

    def test_chunked_file_arrives_whole(self) -> None:
        import codesync.sync.gitfiles as gitfiles

        original = gitfiles.PUT_CHUNK
        gitfiles.PUT_CHUNK = 4
        try:
            (self.folder_a / "blob.bin").write_bytes(b"abcdefghij")
            link = self.node.create_share(self.folder_a)["link"]
            self.node.join_share(link, self.folder_b)
            wait_until(
                lambda: (self.folder_b / "blob.bin").is_file()
                and (self.folder_b / "blob.bin").read_bytes() == b"abcdefghij"
            )
            self.assertFalse(list(self.folder_b.glob("*.codesync-part")))
        finally:
            gitfiles.PUT_CHUNK = original

    def test_pack_index_waits_for_its_pack(self) -> None:
        from codesync.sync.files import hash_bytes

        repo = self.folder_a / "app" / ".git" / "objects" / "pack"
        repo.mkdir(parents=True)
        view = self.node.create_share(self.folder_a)
        self.node.set_paused(view["id"], True)
        share = self.node.shares[view["id"]]
        idx_rel = "app/.git/objects/pack/pack-abc.idx"
        pack_rel = "app/.git/objects/pack/pack-abc.pack"
        idx_body = b"idx-bytes"
        pack_body = b"pack-bytes"
        self.node._apply_put(
            share,
            None,
            {"path": idx_rel, "hash": hash_bytes(idx_body), "counter": 1, "size": len(idx_body)},
            idx_body,
        )
        self.assertFalse((self.folder_a / idx_rel).is_file())
        self.assertTrue((self.folder_a / idx_rel).with_name("pack-abc.idx.codesync-hold").is_file())
        self.node._apply_put(
            share,
            None,
            {"path": pack_rel, "hash": hash_bytes(pack_body), "counter": 1, "size": len(pack_body)},
            pack_body,
        )
        self.assertEqual((self.folder_a / pack_rel).read_bytes(), pack_body)
        self.assertEqual((self.folder_a / idx_rel).read_bytes(), idx_body)
        self.assertFalse((self.folder_a / idx_rel).with_name("pack-abc.idx.codesync-hold").exists())


class PeerListTests(unittest.TestCase):
    def test_names_stay_through_a_short_disconnect(self) -> None:
        from types import SimpleNamespace

        from codesync.sync.node import Share

        root = Path(self.id())
        with tempfile.TemporaryDirectory() as tmp:
            node = Node(Path(tmp))
            share = Share(
                id="s",
                token="t",
                root=root,
                role="hub",
                hub_host="127.0.0.1",
                hub_port=1,
                name="n",
                key="s",
            )
            node.shares["s"] = share
            link = SimpleNamespace(peer_name="Other", alive=True)
            share.conns.append(link)
            self.assertEqual(node.view("s")["peers"], ["Other"])
            link.alive = False
            self.assertEqual(node.view("s")["peers"], ["Other"])
            share.peer_hold_until = time.monotonic() - 1
            self.assertEqual(node.view("s")["peers"], [])
            share.conns.clear()
            node.index.close()

    def test_hub_hides_itself_and_a_self_addressed_peer_becomes_the_hub(self) -> None:
        from types import SimpleNamespace

        from codesync.sync.node import Share

        with tempfile.TemporaryDirectory() as tmp:
            node = Node(Path(tmp), advertise="10.0.0.242", listen_port=47331, machine_id="local-id", machine_name="CUMPUTER")
            node.port = 47331
            share = Share(
                id="s",
                token="t",
                root=Path(tmp),
                role="peer",
                hub_host="10.0.0.242",
                hub_port=47331,
                name="rogally",
                key="s",
            )
            node.shares["s"] = share
            self.assertTrue(node._claim_if_self_hosted(share))
            share.role = "hub"
            share.conns.append(SimpleNamespace(peer_name="CUMPUTER", peer_id="local-id", alive=True))
            share.conns.append(SimpleNamespace(peer_name="CUMPUTER", peer_id="local-id", alive=True))
            share.conns.append(SimpleNamespace(peer_name="ROG-RC71L-Z1X", peer_id="rog-id", alive=True))
            node._set_presence(share)
            self.assertEqual(node.view("s")["peers"], ["ROG-RC71L-Z1X"])
            self.assertEqual(node.view("s")["detail"], "1 connected")
            node.index.close()


if __name__ == "__main__":
    unittest.main()
