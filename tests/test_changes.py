import unittest
from datetime import datetime, timezone

from gate.changes import misses

SHA = "a" * 40
DOC = {"version": SHA, "environments": ["prod-us", "prod-eu"],
       "window": {"start": "2026-11-02T09:00:00-05:00", "end": "2026-11-02T17:00:00-05:00"}}
INSIDE = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)  # 10:00 Toronto


class Misses(unittest.TestCase):
    def test_matching_deploy(self):
        self.assertEqual(misses(DOC, SHA, "prod-eu", INSIDE), [])

    def test_other_version(self):
        self.assertEqual(misses(DOC, "b" * 40, "prod-eu", INSIDE), ["version is aaaaaaa, not bbbbbbb"])

    def test_other_environment(self):
        self.assertEqual(misses(DOC, SHA, "prod-ap", INSIDE), ["environments are prod-us, prod-eu, not prod-ap"])

    def test_before_window(self):
        self.assertEqual(misses(DOC, SHA, "prod-eu", datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)),
                         ["window starts 2026-11-02T09:00:00-05:00"])

    def test_after_window(self):
        self.assertEqual(misses(DOC, SHA, "prod-eu", datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),
                         ["window ended 2026-11-02T17:00:00-05:00"])

    def test_yaml_parsed_timestamps(self):
        doc = {**DOC, "window": {"start": datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc),
                                 "end": datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)}}
        self.assertEqual(misses(doc, SHA, "prod-eu", INSIDE), [])


if __name__ == "__main__":
    unittest.main()
