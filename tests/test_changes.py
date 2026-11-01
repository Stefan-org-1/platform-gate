import unittest
from datetime import datetime, timezone

from gate.changes import covers

SHA = "a" * 40
DOC = {"version": SHA, "environments": ["prod-us", "prod-eu"],
       "window": {"start": "2026-11-02T09:00:00-05:00", "end": "2026-11-02T17:00:00-05:00"}}
INSIDE = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)  # 10:00 Toronto


class Covers(unittest.TestCase):
    def test_matching_deploy(self):
        self.assertTrue(covers(DOC, SHA, "prod-eu", INSIDE))

    def test_other_version(self):
        self.assertFalse(covers(DOC, "b" * 40, "prod-eu", INSIDE))

    def test_other_environment(self):
        self.assertFalse(covers(DOC, SHA, "prod-ap", INSIDE))

    def test_outside_window(self):
        self.assertFalse(covers(DOC, SHA, "prod-eu", datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)))

    def test_yaml_parsed_timestamps(self):
        doc = {**DOC, "window": {"start": datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc),
                                 "end": datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)}}
        self.assertTrue(covers(doc, SHA, "prod-eu", INSIDE))


if __name__ == "__main__":
    unittest.main()
