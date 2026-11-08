"""Sync time and frequency: only values the Sync page offers are saved, and a bad stored
value falls back to the defaults instead of stopping Klartion from starting.

Run from the repo root: python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

from app import config, scheduler  # noqa: E402


class CleanTest(unittest.TestCase):
    def test_times(self):
        for given, saved in [("08:00", "08:00"), ("8:05", "08:05"), (" 23:59 ", "23:59"), ("00:00", "00:00")]:
            self.assertEqual(scheduler.clean_time(given), saved)
        for bad in ["8", "xx:yy", "24:00", "12:60", "12:5", "1:2:3", "", None, "08:00\n09:00", "-1:00"]:
            with self.subTest(bad):
                self.assertIsNone(scheduler.clean_time(bad))

    def test_frequencies(self):
        for f in ("0", "6", "12", "24", 24, " 6 "):
            self.assertEqual(scheduler.clean_frequency(f), int(f))
        for bad in ("abc", "5", "-6", "1e3", "", None, "48"):
            with self.subTest(bad):
                self.assertIsNone(scheduler.clean_frequency(bad))


class StoredValuesTest(unittest.TestCase):
    def use(self, sync_time, frequency):
        for k, v in (("SYNC_TIME", sync_time), ("SYNC_FREQUENCY", frequency), ("TIMEZONE", "")):
            p = patch.object(config, k, v, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_good_values_are_used(self):
        self.use("07:30", "12")
        self.assertEqual(scheduler.sync_settings(), ("07:30", 12))

    def test_bad_values_fall_back_to_defaults(self):
        for sync_time, frequency in [("xx:yy", "abc"), ("8", "5"), ("", ""), ("25:00", "0x18")]:
            with self.subTest((sync_time, frequency)):
                self.use(sync_time, frequency)
                self.assertEqual(scheduler.sync_settings(), ("08:00", 24))

    def test_the_scheduler_starts_with_bad_values(self):
        self.use("xx:yy", "abc")
        with patch.object(scheduler, "_started", True), \
             patch.object(scheduler, "_should_catchup", return_value=False), \
             patch.object(scheduler.db, "get_all_tokens", return_value=[]):
            scheduler.start()
            self.assertEqual(scheduler.get_job_count(), 1)  # once a day, at the default time
        scheduler.schedule.clear()

    def test_a_scheduler_failure_does_not_stop_the_web_ui(self):
        from app import main
        with patch.object(main, "start_scheduler", side_effect=ValueError("boom")):
            main._start_scheduler()  # logs instead of raising


if __name__ == "__main__":
    unittest.main()
