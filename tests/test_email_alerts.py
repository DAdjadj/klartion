"""The alert emails: every one renders as HTML and plain text, and is sent as both.

The HTML is Klartion's Notion-page look from the licence emails. These tests
build each alert from sample sync results, check what the reader sees in both
parts, and check that sending still goes through the same SMTP path with the
unsubscribe check in front of it.

Run from the repo root: python -m unittest discover -s tests
"""
import datetime
import email
import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

from app import config, email_design, email_notify  # noqa: E402

NOW = datetime.datetime(2026, 10, 26, 7, 0, tzinfo=datetime.timezone.utc)
BALANCES = ["ING (NL): 2,481.07 EUR", "Revolut (PT): 312.50 EUR"]
ERRORS = ["Wise (BE): SimpleFIN subscription lapsed. Renew at bridge.simplefin.org.",
          "ING (NL): Notion rejected the page: the Amount column is missing."]

SETTINGS = dict(NOTIFY_EMAIL="alex@example.com", SMTP_USER="alex@icloud.com", SMTP_PASSWORD="pw",
                SMTP_HOST="smtp.mail.me.com", SMTP_PORT=587, SMTP_FROM="", NOTIFY_ON="all", TIMEZONE="",
                KLARTION_URL="http://192.168.1.20:3001", NOTION_DATABASE_ID="1f2e3d4c-5b6a-4789-8a9b-0c1d2e3f4a5b")

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")


def use_settings(test):
    # config answers through a module __getattr__ backed by the database;
    # a real attribute set here wins over it.
    for k, v in SETTINGS.items():
        p = patch.object(config, k, v, create=True)
        p.start()
        test.addCleanup(p.stop)
    p = patch.object(email_notify, "_now", return_value=NOW)
    p.start()
    test.addCleanup(p.stop)


def every_alert():
    return {
        "success": email_notify.build_success(12, BALANCES),
        "success-none": email_notify.build_success(0, []),
        "partial": email_notify.build_partial(9, BALANCES, ERRORS[:1]),
        "failure-accounts": email_notify.build_failure("0 transactions written. Errors: ...", ERRORS),
        "failure-single": email_notify.build_failure("Licence invalid: This licence key has been deactivated."),
        "expiry": email_notify.build_token_expiry_warning("ING", 5),
        "test": email_notify.build_test(),
    }


class AlertContentTest(unittest.TestCase):
    def setUp(self):
        use_settings(self)

    def test_every_alert_renders_as_a_notion_page(self):
        for name, (subject, e) in every_alert().items():
            with self.subTest(name):
                html, text = email_design.render_html(e), email_design.render_text(e)
                self.assertTrue(subject.startswith("Klartion: "))
                self.assertIn("kl-cover.jpg", html)
                self.assertIn("family=Lora", html)
                self.assertIn("prefers-color-scheme:dark", html)
                self.assertIn(e["title"], text)
                for part in (subject, html, text):
                    self.assertNotIn("—", part)
                    self.assertNotIn("–", part)
                    self.assertIsNone(EMOJI.search(part), name)

    def test_images_come_from_the_licence_server(self):
        _, e = email_notify.build_success(1, BALANCES)
        srcs = re.findall(r'src="([^"]+)"', email_design.render_html(e))
        self.assertTrue(srcs)
        for src in srcs:
            self.assertTrue(src.startswith("https://api.klartion.com/static/email/"), src)

    def test_success_shows_balances_and_both_buttons(self):
        _, e = email_notify.build_success(12, BALANCES)
        self.assertEqual(e["intro"][0], "12 new transactions are in your Notion database.")
        self.assertEqual(e["table"]["rows"], [("ING (NL)", "2,481.07 EUR"), ("Revolut (PT)", "312.50 EUR")])
        self.assertEqual([c["href"] for c in e["ctas"]],
                         ["http://192.168.1.20:3001", "https://www.notion.so/1f2e3d4c5b6a47898a9b0c1d2e3f4a5b"])
        text = email_design.render_text(e)
        self.assertIn("Written: 12 transactions", text)
        self.assertIn("- ING (NL): 2,481.07 EUR", text)
        self.assertNotIn("Status: Synced", text)  # the tag is for the eye only

    def test_partial(self):
        subject, e = email_notify.build_partial(9, BALANCES, ERRORS[:1])
        self.assertEqual(subject, "Klartion: sync partially complete")
        self.assertEqual(e["intro"][0], "9 new transactions are in your Notion database, but one thing went wrong.")
        self.assertEqual(e["problems"], [{"name": "Wise (BE)", "detail": "SimpleFIN subscription lapsed. Renew at bridge.simplefin.org."}])

    def test_failure_lists_each_problem(self):
        subject, e = email_notify.build_failure("0 transactions written. Errors: ...", ERRORS)
        self.assertEqual(subject, "Klartion: sync failed")
        self.assertEqual([p["name"] for p in e["problems"]], ["Wise (BE)", "ING (NL)"])
        # The message keeps its own colons: the split is after the account label only.
        self.assertEqual(e["problems"][1]["detail"], "Notion rejected the page: the Amount column is missing.")
        text = email_design.render_text(e)
        self.assertIn("What went wrong:\n- Wise (BE): SimpleFIN subscription lapsed.", text)

    def test_failure_with_one_message(self):
        _, e = email_notify.build_failure("No bank connection found. Please connect your bank.")
        self.assertEqual(e["problems"], [{"name": "", "detail": "No bank connection found. Please connect your bank."}])

    def test_bank_connection_expiry(self):
        subject, e = email_notify.build_token_expiry_warning("ING", 5)
        self.assertEqual(subject, "Klartion: your ING connection expires in 5 days")
        self.assertIn({"label": "Expires", "value": "Saturday 31 October"}, e["facts"])
        self.assertEqual(e["cta"]["href"], "http://192.168.1.20:3001/connect")
        subject, e = email_notify.build_token_expiry_warning("ING", 1)
        self.assertEqual(subject, "Klartion: your ING connection expires in 1 day")
        self.assertEqual(e["title"], "Your ING connection expires tomorrow")
        subject, _ = email_notify.build_token_expiry_warning("ING", 0)
        self.assertEqual(subject, "Klartion: your ING connection expires today")

    def test_test_email_shows_the_settings(self):
        _, e = email_notify.build_test(notify_email="sam@example.com", notify_on="errors", host="smtp.gmail.com")
        facts = {f["label"]: f["value"] for f in e["facts"] if not f.get("text_skip")}
        self.assertEqual(facts, {"Sent to": "sam@example.com", "When to notify": "Errors only", "Sent through": "smtp.gmail.com"})

    def test_names_from_the_bank_are_escaped(self):
        _, e = email_notify.build_success(1, ["<b>Evil</b> & Co: 1.00 EUR"])
        html = email_design.render_html(e)
        self.assertNotIn("<b>Evil</b>", html)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt; &amp; Co", html)


class SendingTest(unittest.TestCase):
    def setUp(self):
        use_settings(self)
        p = patch.object(email_notify, "_is_unsubscribed", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.smtp = MagicMock()
        p = patch.object(email_notify.smtplib, "SMTP", return_value=self.smtp)
        self.SMTP = p.start()
        self.addCleanup(p.stop)

    def sent(self):
        conn = self.smtp.__enter__.return_value
        conn.sendmail.assert_called_once()
        sender, to, raw = conn.sendmail.call_args.args
        return sender, to, email.message_from_string(raw), conn

    def test_alerts_go_out_as_text_and_html(self):
        email_notify.send_success(12, BALANCES)
        self.SMTP.assert_called_once_with("smtp.mail.me.com", 587)
        sender, to, msg, conn = self.sent()
        conn.starttls.assert_called_once()
        conn.login.assert_called_once_with("alex@icloud.com", "pw")
        self.assertEqual((sender, to), ("alex@icloud.com", "alex@example.com"))
        self.assertEqual(msg["Subject"], "Klartion: sync complete")
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        plain, html = msg.get_payload()
        self.assertEqual(plain.get_content_type(), "text/plain")
        self.assertEqual(html.get_content_type(), "text/html")
        self.assertIn("Written: 12 transactions", plain.get_payload(decode=True).decode("utf-8"))
        self.assertIn("kl-cover.jpg", html.get_payload(decode=True).decode("utf-8"))

    def test_notify_on_is_respected(self):
        with patch.object(config, "NOTIFY_ON", "errors", create=True):
            email_notify.send_success(3, BALANCES)
            self.smtp.__enter__.return_value.sendmail.assert_not_called()
            email_notify.send_partial(3, BALANCES, ERRORS[:1])
        self.assertEqual(self.sent()[2]["Subject"], "Klartion: sync partially complete")
        with patch.object(config, "NOTIFY_ON", "never", create=True):
            self.smtp.reset_mock()
            email_notify.send_failure("boom")
            self.smtp.__enter__.return_value.sendmail.assert_not_called()

    def test_unsubscribed_address_gets_nothing(self):
        with patch.object(email_notify, "_is_unsubscribed", return_value=True):
            email_notify.send_token_expiry_warning("ING", 5)
        self.SMTP.assert_not_called()


class SyncNotifyTest(unittest.TestCase):
    """How a finished sync picks its email: some transactions written means partial, none means failed."""

    def run_sync(self, written, errors):
        from app import sync
        tokens = [{"id": 1, "provider": "enablebanking", "bank_name": "ING", "bank_country": "NL"}]
        with patch.object(sync.licence, "validate", return_value={"valid": True}), \
             patch.object(sync.licence, "sync_bank_seats", return_value={"ok": True}), \
             patch.object(sync.db, "get_all_tokens", return_value=tokens), \
             patch.object(sync.db, "set_setting"), patch.object(sync.db, "log_sync"), \
             patch.object(sync.db, "get_category_rules", return_value={}), \
             patch.object(sync.notion, "ensure_schema", return_value=""), \
             patch.object(sync.notion, "fetch_category_rules", return_value={}), \
             patch.object(sync, "_sync_enablebanking_token", return_value=(written, list(errors), ["ING (NL): 1.00 EUR"])), \
             patch.object(sync, "_check_for_update"), \
             patch.object(sync.email_notify, "send_partial") as partial, \
             patch.object(sync.email_notify, "send_failure") as failure, \
             patch.object(sync.email_notify, "send_success") as success:
            sync._run_impl("test")
        return partial, failure, success

    def test_some_written_with_errors_is_partial(self):
        partial, failure, _ = self.run_sync(4, ["ING (NL): boom"])
        partial.assert_called_once_with(4, ["ING (NL): 1.00 EUR"], ["ING (NL): boom"])
        failure.assert_not_called()

    def test_nothing_written_with_errors_is_failure(self):
        partial, failure, _ = self.run_sync(0, ["ING (NL): boom"])
        partial.assert_not_called()
        failure.assert_called_once()
        self.assertEqual(failure.call_args.kwargs["errors"], ["ING (NL): boom"])

    def test_no_errors_is_success(self):
        partial, failure, success = self.run_sync(2, [])
        success.assert_called_once_with(2, balance_lines=["ING (NL): 1.00 EUR"])


if __name__ == "__main__":
    unittest.main()
