"""Diagnostics: the lines Klartion writes for support, the Detailed log switches that
turn themselves off after 24 hours, Clear, and the /diagnostics page.

Run from the repo root: python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

from app import config, db, diag  # noqa: E402

db.init()

SECRETS = [
    "PT50000201231234567890154",       # IBAN
    "alex@example.com",                # email address
    "secret_abcDEF1234567890abcdef",   # Notion key
    "192.168.1.20",                    # server address
    "1f2e3d4c-5b6a-4789-8a9b-0c1d2e3f4a5b",  # Notion page / account ID
    "https://user:pw@bridge.simplefin.org/simplefin",  # SimpleFIN access URL
]


class DiagCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "klartion-diagnostics.log")
        p = patch.dict(os.environ, {"DIAG_LOG_PATH": self.path})
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(config, "TIMEZONE", "Europe/Lisbon", create=True)
        p.start()
        self.addCleanup(p.stop)
        for key, _, _ in diag.SWITCHES:
            db.set_setting(f"diag_detail_{key}", "")
        db.set_setting("diag_cleared_at", "")

    def at(self, when):
        p = patch.object(diag, "_now", return_value=when)
        p.start()
        self.addCleanup(p.stop)

    def file_text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()


class LinesTest(DiagCase):
    def test_a_note_goes_into_the_log_tagged(self):
        self.at(datetime(2026, 11, 25, 8, 0, 42, tzinfo=timezone.utc))
        with self.assertLogs("app", "INFO") as logs:  # propagated, as it reaches docker logs
            diag.note("sync", "Done: 2 accounts, 6 new, 3 matched, 18 s")
        expected = "[diag] 2026-11-25T08:00:42Z Sync: Done: 2 accounts, 6 new, 3 matched, 18 s"
        self.assertIn(expected, logs.output[0])  # what Download log (docker logs) carries
        self.assertEqual(self.file_text().strip(), expected)  # what the page reads

    def test_the_page_shows_newest_first_in_12_hour_time_day_first(self):
        self.at(datetime(2026, 11, 25, 8, 0, 0, tzinfo=timezone.utc))
        diag.note("sync", "Started (schedule)")
        self.at(datetime(2026, 11, 25, 20, 5, 9, tzinfo=timezone.utc))
        diag.note("notion", "HTTP 429, waited 2 s, worked on retry", error=True)
        lines = diag.read()
        self.assertEqual([l["text"] for l in lines], ["Notion: HTTP 429, waited 2 s, worked on retry", "Started (schedule)"])
        self.assertEqual(lines[0]["time"], "25 Nov 2026 8:05:09 PM")  # Lisbon is on UTC in November
        self.assertEqual(lines[1]["time"], "25 Nov 2026 8:00:00 AM")
        self.assertEqual((lines[0]["area"], lines[0]["topic"], lines[0]["error"]), ("Error", "Notion", True))
        self.assertEqual((lines[1]["area"], lines[1]["topic"], lines[1]["error"]), ("Sync", "Sync", False))

    def test_a_note_never_raises(self):
        with patch.object(diag, "_file_handler", side_effect=OSError("disk full")):
            diag.note("sync", "Started (manual)")
            diag.detail("bank", "anything")


class RedactionTest(DiagCase):
    def test_no_secret_reaches_the_file_or_the_page(self):
        for s in SECRETS:
            diag.note("bank", f"Something about {s} happened", error=True)
        text = self.file_text() + "\n".join(l["text"] for l in diag.read()) + diag.export_text()
        for s in SECRETS:
            self.assertNotIn(s, text)
        self.assertNotIn("bridge.simplefin.org", text)

    def test_counts_codes_versions_and_times_survive(self):
        for kept in ["Started: Klartion v2026.11.25.2, Python 3.12.7, notion-client 2.3.0",
                     "Enable Banking transactions: HTTP 429, waiting 5 s", "at 08:00:42",
                     "Done: 2 accounts, 6 new, 28 matched, 18 s"]:
            self.assertEqual(diag.redact(kept), kept)

    def test_bank_names_lose_the_account_label(self):
        self.assertEqual(diag.bank_label({"bank_name": "ING · Joint account"}), "ING")
        self.assertEqual(diag.bank_label("Revolut"), "Revolut")
        self.assertEqual(diag.bank_label({}), "Unknown bank")

    def test_errors_are_named_without_their_message(self):
        class Resp:
            status_code = 403

        class HTTPError(Exception):
            response = Resp()

        err = HTTPError("403 for url: https://api.enablebanking.com/accounts/abc-123/transactions")
        self.assertEqual(diag.error_name(err), "HTTPError, HTTP 403")
        self.assertEqual(diag.error_name(ValueError("alex@example.com")), "ValueError")


class SwitchTest(DiagCase):
    def test_detail_lines_only_while_the_switch_is_on(self):
        diag.detail("bank", "Enable Banking transactions, page 1: HTTP 200")
        self.assertEqual(diag.read(), [])
        diag.set_switch("bank", True)
        diag.detail("bank", "Enable Banking transactions, page 1: HTTP 200")
        diag.detail("notion", "ING: 6 pages added")  # its own switch is still off
        texts = [l["text"] for l in diag.read()]
        self.assertIn("Enable Banking transactions, page 1: HTTP 200", texts)
        self.assertNotIn("ING: 6 pages added", texts)

    def test_update_lines_follow_the_license_switch(self):
        diag.set_switch("license", True)
        diag.detail("update", "Docker Hub answered HTTP 200")
        self.assertIn("Docker Hub answered HTTP 200", [l["text"] for l in diag.read()])

    def test_a_switch_turns_itself_off_after_24_hours(self):
        start = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        self.at(start)
        diag.set_switch("email", True)
        self.at(start + timedelta(hours=23, minutes=59))
        self.assertTrue(diag.is_on("email"))
        self.assertEqual(next(s for s in diag.switches() if s["key"] == "email")["until"], "26 Nov 2026 8:00:00 AM")
        self.at(start + timedelta(hours=24))
        self.assertFalse(diag.is_on("email"))
        self.assertFalse(next(s for s in diag.switches() if s["key"] == "email")["on"])

    def test_switching_off(self):
        diag.set_switch("notion", True)
        diag.set_switch("notion", False)
        self.assertFalse(diag.is_on("notion"))

    def test_unknown_switch(self):
        with self.assertRaises(ValueError):
            diag.set_switch("everything", True)


class ClearTest(DiagCase):
    def test_clear_hides_older_lines_but_keeps_the_file(self):
        start = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        self.at(start)
        diag.note("sync", "Started (schedule)")
        self.at(start + timedelta(seconds=5))
        diag.clear()
        self.assertEqual(diag.read(), [])
        self.assertIn("Started (schedule)", self.file_text())  # support still has it
        self.assertIn("Started (schedule)", diag.export_text())
        self.at(start + timedelta(seconds=10))
        diag.note("sync", "Started (manual)")
        self.assertEqual([l["text"] for l in diag.read()], ["Started (manual)"])


class PageTest(DiagCase):
    def setUp(self):
        super().setUp()
        from app.web import server
        self.server = server
        self.client = server.app.test_client()
        for k, v in dict(LICENCE_KEY="KL-TEST-KEY-123456789012", NOTION_API_KEY=SECRETS[2],
                         NOTION_DATABASE_ID=SECRETS[4], NOTIFY_EMAIL=SECRETS[1], SMTP_USER=SECRETS[1],
                         SMTP_PASSWORD="hunter2", KLARTION_URL="http://192.168.1.20:3001").items():
            p = patch.object(config, k, v, create=True)
            p.start()
            self.addCleanup(p.stop)
        for target, value in [("validate", {"valid": True, "error": None}),
                              ("get_activation_info", {"usage": 1, "limit": 2, "is_trial": False})]:
            p = patch.object(server.licence, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        from app import notion
        p = patch.object(notion, "verify_database", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(db, "get_all_tokens", return_value=[
            {"id": 1, "bank_name": "ING · Joint account PT50000201231234567890154", "provider": None,
             "expires_at": "2027-05-20T10:00:00+00:00", "access_token": SECRETS[4]},
            {"id": 2, "bank_name": "Chase", "provider": "simplefin"},
        ])
        p.start()
        self.addCleanup(p.stop)

    def test_renders_the_facts_switches_and_log(self):
        diag.note("sync", "Done: 2 accounts, 6 new, 3 matched, 18 s")
        html = self.client.get("/diagnostics").get_data(as_text=True)
        for text in ["Diagnostics", "Version", "Active, 1 of 2 machines", "Connected, database found",
                     "2 accounts (Enable Banking 1, SimpleFIN 1): Chase, ING. Consent ends 20 May 2027",
                     "Memory now", "Bank connection", "Writing to Notion", "License and updates",
                     "off again after 24 h", "Copy log", "Download log", "Clear",
                     "Done: 2 accounts, 6 new, 3 matched, 18 s", 'href="/diagnostics"', "&#x1FA7A;",
                     "never IBANs, payees, amounts, keys, Notion pages or addresses"]:
            self.assertIn(text, html)

    def test_no_secrets_in_the_page(self):
        for s in SECRETS:
            diag.note("bank", f"leak {s}", error=True)
        html = self.client.get("/diagnostics").get_data(as_text=True)
        for s in SECRETS + ["hunter2", "Joint account"]:
            self.assertNotIn(s, html)

    def test_switch_and_clear_endpoints(self):
        r = self.client.post("/diagnostics/detail", json={"area": "bank", "on": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["on"])
        self.assertTrue(r.get_json()["until"])
        self.assertTrue(diag.is_on("bank"))
        self.assertEqual(self.client.post("/diagnostics/detail", json={"area": "x", "on": True}).status_code, 400)
        self.assertEqual(self.client.post("/diagnostics/clear").status_code, 200)
        self.assertEqual(diag.read(), [])

    def test_a_broken_fact_does_not_break_the_page(self):
        with patch.object(self.server.licence, "validate", side_effect=RuntimeError("down")):
            r = self.client.get("/diagnostics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Couldn&#39;t read this", r.get_data(as_text=True))

    def test_status_links_to_diagnostics(self):
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "templates", "status.html")
        with open(path, encoding="utf-8") as f:
            status = f.read()
        self.assertIn('href="/diagnostics"', status)
        self.assertNotIn("Download logs", status)

    def test_download_log_falls_back_to_the_diagnostics_lines_without_docker(self):
        diag.note("sync", "Started (manual)")

        class Done:
            returncode, stdout, stderr = 1, "", "Cannot connect to the Docker daemon"

        with patch("subprocess.run", return_value=Done()):
            data = self.client.get("/api/logs").get_json()
        self.assertIn("Sync: Started (manual)", data["logs"])


if __name__ == "__main__":
    unittest.main()
