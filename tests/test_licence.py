"""License checks: offline use is limited to 30 days after the license server last said yes,
and a 409 from /activate is a refusal.

Run from the repo root: python -m unittest discover -s tests
"""
import datetime
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

from app import licence  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
KEY = "KL-TEST-KEY"


def answer(status, body):
    resp = MagicMock(status_code=status)
    return resp, body


class OfflineDaysTest(unittest.TestCase):
    def test_days_left(self):
        days = datetime.timedelta(days=1)
        self.assertEqual(licence.offline_days_left(NOW, NOW), 30)
        self.assertEqual(licence.offline_days_left(NOW - 10 * days, NOW), 20)
        self.assertEqual(licence.offline_days_left(NOW - 29 * days - datetime.timedelta(hours=23), NOW), 1)
        self.assertEqual(licence.offline_days_left(NOW - 30 * days, NOW), 0)
        self.assertEqual(licence.offline_days_left(NOW - 400 * days, NOW), 0)
        self.assertEqual(licence.offline_days_left(NOW + 5 * days, NOW), 30)  # clock went back


class LicenceTest(unittest.TestCase):
    def setUp(self):
        self.settings = {"licence_key": KEY}
        self.now = NOW
        for target, attr, value in [
            (licence.db, "get_setting", lambda k: self.settings.get(k)),
            (licence.db, "set_setting", lambda k, v: self.settings.__setitem__(k, v)),
            (licence, "_get_fingerprint", lambda: "fp"),
            (licence, "_now", lambda: self.now),
        ]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def server(self, *answers):
        p = patch.object(licence, "_post_json", side_effect=list(answers))
        p.start()
        self.addCleanup(p.stop)

    def offline(self):
        self.server(*[licence.requests.RequestException("no route to host")] * 4)

    def last_online(self, days_ago):
        self.settings[licence.LAST_ONLINE] = (NOW - datetime.timedelta(days=days_ago)).isoformat()

    def test_a_good_answer_records_the_date(self):
        self.server(answer(200, {"valid": True}))
        self.assertEqual(licence.validate(KEY), {"valid": True, "error": None})
        self.assertEqual(self.settings[licence.LAST_ONLINE], NOW.isoformat())

    def test_offline_within_30_days_keeps_working(self):
        self.last_online(10)
        self.offline()
        r = licence.validate(KEY)
        self.assertTrue(r["valid"])
        self.assertTrue(r["offline"])
        self.assertEqual(r["offline_days_left"], 20)

    def test_offline_after_30_days_stops(self):
        self.last_online(30)
        self.offline()
        r = licence.validate(KEY)
        self.assertFalse(r["valid"])
        self.assertFalse(r.get("offline"))
        self.assertTrue(r["offline_expired"])
        self.assertIn("since 9 Oct 2026", r["error"])
        self.assertIn("syncing has stopped", r["error"])
        self.assertIn("support@david-alves.com", r["error"])

    def test_an_install_from_before_this_gets_30_days_from_now(self):
        self.offline()
        r = licence.validate(KEY)
        self.assertEqual((r["valid"], r["offline_days_left"]), (True, 30))
        self.assertEqual(self.settings[licence.LAST_ONLINE], NOW.isoformat())
        # Still offline a month later: the grace is over.
        self.now = NOW + datetime.timedelta(days=30, hours=1)
        r = licence.validate(KEY)
        self.assertTrue(r["offline_expired"])
        self.assertIn("since 8 Nov 2026", r["error"])

    def test_back_online_starts_again(self):
        self.last_online(45)
        self.server(answer(200, {"valid": True}))
        self.assertTrue(licence.validate(KEY)["valid"])
        self.offline()
        self.assertEqual(licence.validate(KEY)["offline_days_left"], 30)

    def test_offline_only_helps_the_key_already_here(self):
        self.last_online(1)
        self.offline()
        r = licence.validate("SOMEONE-ELSES-KEY")
        self.assertEqual((r["valid"], r.get("offline")), (False, None))
        r = licence.activate("SOMEONE-ELSES-KEY")
        self.assertEqual((r["valid"], r.get("offline")), (False, None))

    def test_activate_409_is_a_refusal(self):
        del self.settings["licence_key"]
        self.server(answer(409, {"error": "Activation limit reached (2)", "valid": False, "limit_reached": True}))
        r = licence.activate(KEY)
        self.assertEqual(r, {"valid": False, "error": "Activation limit reached (2)"})
        self.assertNotIn("licence_key", self.settings)
        self.assertNotIn(licence.LAST_ONLINE, self.settings)

    def test_validate_refused_then_409_on_reactivation_is_invalid(self):
        self.server(answer(403, {"valid": False, "error": "This license key has been deactivated."}),
                    answer(409, {"valid": False, "error": "A free trial has already been used on this machine."}))
        r = licence.validate(KEY)
        self.assertEqual(r, {"valid": False, "error": "This license key has been deactivated."})

    def test_activate_ok_stores_the_key_and_date(self):
        del self.settings["licence_key"]
        self.server(answer(200, {"valid": True, "status": "already activated"}))
        self.assertTrue(licence.activate(KEY)["valid"])
        self.assertEqual(self.settings["licence_key"], KEY)
        self.assertEqual(self.settings[licence.LAST_ONLINE], NOW.isoformat())


class SyncStopsWhenGraceIsOverTest(unittest.TestCase):
    def test_sync_fails_with_the_offline_message(self):
        from app import sync
        expired = {"valid": False, "offline_expired": True, "error": "Klartion hasn't reached the license server since 9 Oct 2026."}
        with patch.object(sync.licence, "validate", return_value=expired), \
             patch.object(sync.db, "log_sync") as log, \
             patch.object(sync.db, "get_all_tokens") as tokens, \
             patch.object(sync.email_notify, "send_failure") as failure:
            ok, written, msg = sync._run_impl("scheduled")
        self.assertFalse(ok)
        self.assertEqual(msg, expired["error"])
        log.assert_called_once_with("failure", message=expired["error"])
        failure.assert_called_once_with(expired["error"])
        tokens.assert_not_called()  # nothing is fetched from the bank


if __name__ == "__main__":
    unittest.main()
