import unittest
from datetime import datetime, timezone

from collector.main import is_backfill, year_start


class CollectorBackfillTests(unittest.TestCase):
    def test_year_start_uses_moscow_calendar_year(self):
        now = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(year_start(now), datetime(2025, 12, 31, 21, 0, tzinfo=timezone.utc))

    def test_materials_published_before_monitoring_are_archive(self):
        started = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
        old = {"published_at": "2026-11-02T12:00:00+00:00"}
        new = {"published_at": "2026-11-03T10:00:00+00:00"}
        self.assertTrue(is_backfill(old, started))
        self.assertFalse(is_backfill(new, started))


if __name__ == "__main__":
    unittest.main()
