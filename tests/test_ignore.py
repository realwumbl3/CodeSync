import tempfile
import unittest
from pathlib import Path

from codesync.sync.ignore import IgnoreIndex


class IgnoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gitignore_matches_git_rules(self) -> None:
        (self.root / ".gitignore").write_text(
            "*.log\n/secret.txt\nbuild/\n!keep.log\n",
            encoding="utf-8",
        )
        (self.root / "sub").mkdir()
        (self.root / "sub" / ".gitignore").write_text("!keep.log\n", encoding="utf-8")
        (self.root / ".git" / "info").mkdir(parents=True)
        (self.root / ".git" / "info" / "exclude").write_text("*.tmp\n", encoding="utf-8")
        ignore = IgnoreIndex(self.root)

        self.assertTrue(ignore.is_ignored("notes.log", False))
        self.assertTrue(ignore.is_ignored("sub/other.log", False))
        self.assertFalse(ignore.is_ignored("sub/keep.log", False))
        self.assertFalse(ignore.is_ignored("keep.log", False))
        self.assertTrue(ignore.is_ignored("secret.txt", False))
        self.assertFalse(ignore.is_ignored("nested/secret.txt", False))
        self.assertTrue(ignore.is_ignored("build", True))
        self.assertTrue(ignore.is_ignored("build/out.js", False))
        self.assertFalse(ignore.is_ignored("src/main.py", False))
        self.assertFalse(ignore.is_ignored(".git", True))
        self.assertFalse(ignore.is_ignored(".git/HEAD", False))
        self.assertFalse(ignore.is_ignored("app/.git", True))
        self.assertFalse(ignore.is_ignored("app/.git/HEAD", False))
        self.assertFalse(ignore.is_ignored("app/.git/objects/pack/pack-abc.idx", False))
        self.assertTrue(ignore.is_ignored(".git/index.lock", False))
        self.assertTrue(ignore.is_ignored("app/.git/objects/pack/tmp_pack_abc", False))
        self.assertTrue(ignore.is_ignored("cache.tmp", False))
        self.assertTrue(ignore.is_ignored("dir/file.codesync-tmp", False))

    def test_directory_star_can_reinclude_a_file(self) -> None:
        (self.root / ".gitignore").write_text("logs/*\n!logs/keep.txt\n", encoding="utf-8")
        ignore = IgnoreIndex(self.root)
        self.assertFalse(ignore.is_ignored("logs", True))
        self.assertFalse(ignore.is_ignored("logs/keep.txt", False))
        self.assertTrue(ignore.is_ignored("logs/drop.txt", False))

    def test_excluded_directory_hides_children(self) -> None:
        (self.root / ".gitignore").write_text("logs/\n!logs/keep.txt\n", encoding="utf-8")
        ignore = IgnoreIndex(self.root)
        self.assertTrue(ignore.is_ignored("logs", True))
        self.assertTrue(ignore.is_ignored("logs/keep.txt", False))

    def test_listing_prunes_ignored_directories(self) -> None:
        (self.root / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
        (self.root / ".git" / "objects").mkdir(parents=True)
        (self.root / ".git" / "objects" / "pack").write_text("x", encoding="utf-8")
        (self.root / "notes.log").write_text("x", encoding="utf-8")
        (self.root / "keep.txt").write_text("x", encoding="utf-8")
        build = self.root / "build"
        build.mkdir()
        (build / "out.js").write_text("x", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("x", encoding="utf-8")
        (self.root / "scratch.codesync-tmp").write_text("x", encoding="utf-8")
        ignore = IgnoreIndex(self.root)

        detected, entries = ignore.ignored_listing()
        self.assertTrue(detected)
        self.assertEqual(entries, (("build", True), ("notes.log", False)))
        self.assertEqual(ignore.cached_listing()["count"], 2)
        ignore.invalidate()
        self.assertIsNone(ignore.cached_listing())

    def test_listing_detects_nested_gitignore_only(self) -> None:
        nested = self.root / "app"
        nested.mkdir()
        (nested / ".gitignore").write_text("*.o\n", encoding="utf-8")
        (nested / "foo.o").write_text("x", encoding="utf-8")
        (nested / "foo.c").write_text("x", encoding="utf-8")
        (self.root / ".git").mkdir()
        ignore = IgnoreIndex(self.root)
        detected, entries = ignore.ignored_listing()
        self.assertTrue(detected)
        self.assertEqual(entries, (("app/foo.o", False),))

    def test_listing_skips_nested_git(self) -> None:
        app = self.root / "app"
        (app / ".git").mkdir(parents=True)
        (app / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (app / "main.py").write_text("x\n", encoding="utf-8")
        ignore = IgnoreIndex(self.root)
        _detected, entries = ignore.ignored_listing()
        paths = [path for path, _is_dir in entries]
        self.assertNotIn("app/.git", paths)
        self.assertFalse(any(path.startswith("app/.git/") for path in paths))
        self.assertFalse(ignore.is_ignored("app/.git/HEAD", False))
        self.assertFalse(ignore.is_ignored("app/main.py", False))

    def test_listing_without_rules_is_empty(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / "readme.txt").write_text("x", encoding="utf-8")
        ignore = IgnoreIndex(self.root)
        detected, entries = ignore.ignored_listing()
        self.assertFalse(detected)
        self.assertEqual(entries, ())


if __name__ == "__main__":
    unittest.main()
