"""Tests for the lpass wrapper.

Pure parsing/templating tests always run. The integration test drives the real
lastpass-cli code against its built-in mock server (`lpass-test`), so it needs
../lastpass-cli/build/lpass-test (built with `make lpass-test`).
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lastpass_gtk.lpass import (FS, RS, Lpass, LpassError, build_template,  # noqa: E402
                                parse_detail, parse_entries)

MOCK = next((p for p in (ROOT / "build" / "lpass" / "lpass-test",
                         ROOT / "build" / "lpass-ci" / "lpass-test",
                         ROOT.parent / "lastpass-cli" / "build" / "lpass-test") if p.exists()),
            ROOT / "build" / "lpass" / "lpass-test")


class ParseTests(unittest.TestCase):
    def test_parse_entries(self):
        rec = FS.join(["42", "Work/Email/gmail", "gmail", "me@x", "https://mail", "Work/Email",
                       "", "2026-01-01 10:00", ""]) + RS + "\n"
        note = FS.join(["7", "wifi", "wifi", "", "http://sn", "", "", "", ""]) + RS + "\n"
        es = parse_entries(rec + note)
        self.assertEqual(len(es), 2)
        self.assertEqual(es[0].folder, "Work/Email")
        self.assertFalse(es[0].is_note)
        self.assertTrue(es[1].is_note)
        self.assertEqual(es[1].folder, "")

    def test_parse_detail(self):
        out = (RS + "\n"
               + f"Username{FS}bob{RS}\n"
               + f"Password{FS}p w{RS}\n"
               + f"Hostname{FS} db.local{RS}\n"
               + f"att-123{FS}report.pdf{RS}\n"
               + f"Reprompt{FS}Yes{RS}\n"
               + f"Notes{FS}line1\n  line2{RS}\n")
        d = parse_detail("9", out)
        self.assertEqual(d.username, "bob")
        self.assertEqual(d.password, "p w")
        self.assertEqual(d.fields, [("Hostname", "db.local")])
        self.assertEqual(d.attachments, [("att-123", "report.pdf")])
        self.assertTrue(d.reprompt)
        self.assertEqual(d.notes, "line1\n  line2")  # notes keep whitespace/newlines

    def test_template_site(self):
        t = build_template("a/b", url="u", username="me", password="pw",
                           fields=[("PIN", "1\n2")], notes="n1\nn2")
        self.assertEqual(t.splitlines()[0], "Name: a/b")
        self.assertIn("Reprompt: No", t)
        self.assertIn("PIN: 1 2", t)  # single-line fields are flattened
        self.assertTrue(t.endswith("Notes:    # Add notes below this line.\nn1\nn2"))

    def test_template_note_skips_empty_login(self):
        t = build_template("wifi", fields=[("NoteType", "Wi-Fi Password")], is_note=True)
        self.assertNotIn("URL:", t)
        self.assertNotIn("Username:", t)
        self.assertIn("NoteType: Wi-Fi Password", t)


@unittest.skipUnless(MOCK.exists(), "lastpass-cli mock build (lpass-test) not available")
class MockIntegrationTests(unittest.TestCase):
    def setUp(self):
        # lpass needs a short path for its agent socket
        self.home = tempfile.mkdtemp(prefix="lpt", dir="/tmp" if os.access("/tmp", os.W_OK) else None)
        self.prompts = []
        self.errors = []
        os.environ["LPASS_HOME"] = self.home

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def make(self, *answers):
        """Answer prompts with `answers` in order; the last one repeats."""
        def handler(prompt, attempt, error):
            self.prompts.append((prompt, attempt))
            self.errors.append(error)
            return answers[min(len(self.prompts), len(answers)) - 1]
        lp = Lpass(handler, binary=str(MOCK))
        self.addCleanup(lp.close)
        return lp

    def test_login_list_show_logout(self):
        lp = self.make("123456")
        self.assertIsNone(lp.status())
        lp.login("user@example.com")
        self.assertEqual(self.prompts, [("Master Password", 1)])
        self.assertEqual(lp.status(), "user@example.com")
        ids = {e.id: e for e in lp.list_entries()}
        self.assertIn("0001", ids)
        self.assertTrue(ids["0002"].is_note)
        d = lp.show("0001")
        self.assertEqual(d.password, "test-account-password")
        note = lp.show("0002")
        self.assertEqual(dict(note.fields)["Hostname"], "foo.example.com")
        lp.edit("0001", build_template("x/y", url="u", username="v", password="w"))
        lp.logout()
        self.assertIsNone(lp.status())

    def test_cancelled_prompt(self):
        lp = self.make(None)
        with self.assertRaises(LpassError) as cm:
            lp.login("user@example.com")
        self.assertTrue(cm.exception.cancelled)

    def test_wrong_password_then_retry(self):
        # lpass re-prompts after a bad master password; attempt number tells the UI
        lp = self.make("000000", "123456")
        lp.login("user@example.com")
        self.assertEqual(self.prompts, [("Master Password", 1), ("Master Password", 2)])
        self.assertEqual(self.errors[0], "")
        self.assertTrue(self.errors[1], "patched lpass should report why attempt 1 failed")
        self.assertEqual(lp.status(), "user@example.com")

    def test_wrong_password_then_cancel(self):
        lp = self.make("000000", None)
        with self.assertRaises(LpassError) as cm:
            lp.login("user@example.com")
        self.assertTrue(cm.exception.cancelled)


if __name__ == "__main__":
    unittest.main()
