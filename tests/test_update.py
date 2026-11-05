import hashlib
import tempfile
import unittest
from pathlib import Path

from codesync.sync.update import UpgradeBuffer, machine_key, restart_environment


class UpdateTests(unittest.TestCase):
    def test_chunks_assemble_in_order_and_ignore_duplicates(self) -> None:
        payload = b"MZ" + b"build-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        first, second = payload[:4], payload[4:]
        header = {"sha256": digest, "size": len(payload), "parts": 2}
        buffer = UpgradeBuffer()
        self.assertIsNone(buffer.feed({**header, "part": 0}, first))
        self.assertIsNone(buffer.feed({**header, "part": 0}, first))
        self.assertEqual(buffer.feed({**header, "part": 1}, second), payload)

    def test_machine_key_groups_extra_shares(self) -> None:
        self.assertEqual(machine_key("abc-def"), "abc-def")
        self.assertEqual(machine_key("abc-def:12ab"), "abc-def")

    def test_bump_increments_last_number(self) -> None:
        from codesync.sync.update import bump_version

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "__init__.py"
            path.write_text('__version__ = "0.0.4"\n', encoding="utf-8")
            self.assertEqual(bump_version(path), "0.0.5")
            self.assertIn('__version__ = "0.0.5"', path.read_text(encoding="utf-8"))

    def test_restart_drops_bootloader_temp_dir(self) -> None:
        from codesync.sync.update import _restart_script

        env = restart_environment(
            {
                "_PYI_APPLICATION_HOME_DIR": r"C:\Users\x\AppData\Local\Temp\_MEI111",
                "_PYI_ARCHIVE_FILE": r"C:\CodeSync\CodeSync.exe",
                "_PYI_PARENT_PROCESS_LEVEL": "1",
                "_MEIPASS": r"C:\Users\x\AppData\Local\Temp\_MEI111",
                "PATH": r"C:\Windows",
            }
        )
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["PATH"], r"C:\Windows")
        for name in (
            "_PYI_APPLICATION_HOME_DIR",
            "_PYI_ARCHIVE_FILE",
            "_PYI_PARENT_PROCESS_LEVEL",
            "_MEIPASS",
        ):
            self.assertNotIn(name, env)
        script = _restart_script(Path(r"C:\CodeSync\CodeSync.exe"), Path(r"C:\CodeSync\CodeSync.exe.old"))
        self.assertIn("set PYINSTALLER_RESET_ENVIRONMENT=1", script)
        self.assertIn("set _PYI_APPLICATION_HOME_DIR=", script)
        self.assertIn("set _PYI_ARCHIVE_FILE=", script)

    def test_packaged_build_has_no_dist_to_send(self) -> None:
        from unittest.mock import patch

        from codesync.sync.update import dist_executable

        with patch("codesync.sync.update.is_frozen", return_value=True):
            self.assertIsNone(dist_executable())


if __name__ == "__main__":
    unittest.main()
