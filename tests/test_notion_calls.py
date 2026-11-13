"""Notion calls share one client, are spaced out, and are retried only when Notion
says it did not handle the request (429, 503), so a retried page create can't
duplicate a row.

Run from the repo root: python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

import httpx  # noqa: E402
from notion_client.errors import APIResponseError, HTTPResponseError  # noqa: E402

from app import config, notion  # noqa: E402


def _error(status, retry_after=None):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(status, headers=headers, text="{}", request=httpx.Request("POST", "https://api.notion.com"))
    return HTTPResponseError(response)


class CallTest(unittest.TestCase):
    def setUp(self):
        notion._shared.update(token=None, client=None, last_call=0.0)
        self.sleeps = []
        patcher = patch.object(notion.time, "sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_rate_limit_using_retry_after(self):
        fn = MagicMock(side_effect=[_error(429, "2"), {"id": "page"}])
        self.assertEqual(notion._call(fn, x=1), {"id": "page"})
        self.assertEqual(fn.call_count, 2)
        self.assertIn(2.0, self.sleeps)

    def test_retries_service_unavailable_with_backoff(self):
        fn = MagicMock(side_effect=[_error(503), _error(503), "ok"])
        self.assertEqual(notion._call(fn), "ok")
        self.assertEqual(fn.call_count, 3)

    def test_does_not_retry_other_errors(self):
        # 502/504 may have reached Notion: a retry could create a duplicate row.
        for status in (400, 404, 500, 502, 504):
            with self.subTest(status):
                fn = MagicMock(side_effect=_error(status))
                with self.assertRaises(HTTPResponseError):
                    notion._call(fn)
                self.assertEqual(fn.call_count, 1)

    def test_gives_up_after_max_attempts(self):
        fn = MagicMock(side_effect=_error(429, "1"))
        with self.assertRaises(HTTPResponseError):
            notion._call(fn)
        self.assertEqual(fn.call_count, notion._MAX_ATTEMPTS)

    def test_api_response_errors_are_covered(self):
        self.assertTrue(issubclass(APIResponseError, HTTPResponseError))


class ClientTest(unittest.TestCase):
    def setUp(self):
        notion._shared.update(token=None, client=None, last_call=0.0)

    @patch("app.notion_auth.refresh", return_value=False)
    def test_one_client_per_token(self, _refresh):
        with patch.object(config, "NOTION_API_KEY", "a"):
            first = notion._client()
            self.assertIs(notion._client(), first)
        with patch.object(config, "NOTION_API_KEY", "b"):
            self.assertIsNot(notion._client(), first)


if __name__ == "__main__":
    unittest.main()
