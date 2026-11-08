"""The Enable Banking key upload: the application ID names the file, so it must be a UUID
and the file must land directly in the data folder, never anywhere else.

Run from the repo root: python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

from app import enablebanking  # noqa: E402

APP_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
BAD_IDS = [
    "../../usr/local/lib/python3.12/site-packages/certifi/cacert",
    "/usr/local/lib/python3.12/site-packages/certifi/cacert",
    "../" + APP_ID,
    APP_ID + "/../x",
    "sub/" + APP_ID,
    "not-a-uuid",
    APP_ID + "\n" + APP_ID,
    APP_ID + "x",
    "",
    None,
]


class PemPathTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        p = patch.object(enablebanking, "DATA_DIR", self.data)
        p.start()
        self.addCleanup(p.stop)

    def test_an_application_id_goes_straight_into_the_data_folder(self):
        path = enablebanking.pem_path_for(APP_ID)
        self.assertEqual(path, os.path.join(os.path.realpath(self.data), APP_ID + ".pem"))
        self.assertEqual(enablebanking.pem_path_for("  " + APP_ID.upper() + " "),
                         os.path.join(os.path.realpath(self.data), APP_ID.upper() + ".pem"))

    def test_anything_else_is_refused(self):
        for bad in BAD_IDS:
            with self.subTest(bad):
                self.assertIsNone(enablebanking.pem_path_for(bad))

    def test_a_symlink_named_like_the_key_cannot_lead_out(self):
        outside = tempfile.mkdtemp()
        os.symlink(os.path.join(outside, "x.pem"), os.path.join(self.data, APP_ID + ".pem"))
        self.assertIsNone(enablebanking.pem_path_for(APP_ID))


if __name__ == "__main__":
    unittest.main()
