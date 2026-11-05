import unittest

from codesync.sync.plan import Entry, SendPut, SendRename, plan_sends


class PlanTests(unittest.TestCase):
    def test_higher_counter_sends(self) -> None:
        local = {"a.txt": Entry("aaa", 2, False)}
        remote = {"a.txt": Entry("old", 1, False)}
        _aligns, sends = plan_sends(local, remote, "a", "b")
        self.assertEqual(sends, [SendPut("a.txt", "aaa", 2)])

    def test_equal_content_aligns_counter(self) -> None:
        local = {"a.txt": Entry("aaa", 1, False)}
        remote = {"a.txt": Entry("aaa", 4, False)}
        aligns, sends = plan_sends(local, remote, "a", "b")
        self.assertEqual(sends, [])
        self.assertEqual(aligns, [("a.txt", 4)])

    def test_tie_uses_machine_id(self) -> None:
        local = {"a.txt": Entry("local", 2, False)}
        remote = {"a.txt": Entry("remote", 2, False)}
        _aligns, left_sends = plan_sends(local, remote, "m-a", "m-b")
        _aligns, right_sends = plan_sends(remote, local, "m-b", "m-a")
        self.assertEqual(left_sends, [])
        self.assertEqual(right_sends, [SendPut("a.txt", "remote", 2)])

    def test_same_hash_delete_and_add_is_a_rename(self) -> None:
        local = {
            "old.txt": Entry("abc", 2, True),
            "new.txt": Entry("abc", 2, False),
        }
        remote = {"old.txt": Entry("abc", 1, False)}
        _aligns, sends = plan_sends(local, remote, "a", "b")
        self.assertEqual(sends, [SendRename("old.txt", "new.txt", "abc", 2)])


if __name__ == "__main__":
    unittest.main()
