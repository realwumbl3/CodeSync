"""Which processes a new launch is allowed to stop.

Run: python -m unittest tests.test_instance -v
"""

from __future__ import annotations

import unittest

from codesync.app import listener_pids, replacement_targets

RUN_PY = r"C:\Users\wumbl\Documents\DEV\CodeSync\run.py"


class ReplacementTests(unittest.TestCase):
    def test_listener_pids_keep_only_the_requested_ports(self) -> None:
        text = "\n".join(
            [
                "  TCP    127.0.0.1:47330    0.0.0.0:0    LISTENING    100",
                "  TCP    0.0.0.0:47331      0.0.0.0:0    LISTENING    100",
                "  TCP    0.0.0.0:80         0.0.0.0:0    LISTENING    4",
                "  TCP    127.0.0.1:47330    127.0.0.1:9  ESTABLISHED  100",
                "  UDP    0.0.0.0:47331      *:*                      100",
            ]
        )
        self.assertEqual(listener_pids(text, {47330, 47331}), {100})

    def test_replacement_skips_this_process_and_unrelated_pythons(self) -> None:
        processes = [
            (10, "python.exe", f'"{RUN_PY}"'),
            (11, "python.exe", r"C:\other\run.py"),
            (12, "CodeSync.exe", r"C:\dist\CodeSync.exe"),
            (13, "pythonw.exe", f'pythonw.exe "{RUN_PY}"'),
        ]
        self.assertEqual(
            replacement_targets(processes, self_pid=10, run_py=RUN_PY),
            [12, 13],
        )


if __name__ == "__main__":
    unittest.main()
