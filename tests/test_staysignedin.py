"""Tests for 'stay signed in for 30 days'.

Policy tests use a fake keyring and clock. Integration tests drive the real
lastpass-cli code against its mock server (lpass-test) to check that the agent is
really stopped by lock(), gets the 30-day timeout, and that unlocks are answered
from the keyring while re-prompts for protected items are not.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lastpass_gtk.lpass import Lpass, LpassError  # noqa: E402
from lastpass_gtk.staysignedin import (DEFAULT_AGENT_TIMEOUT, DURATION,  # noqa: E402
                                       StaySignedIn)
from tests.test_lpass import MOCK  # noqa: E402

PW = "123456"  # the mock server's master password


class FakeStore:
    def __init__(self):
        self.items = {}

    def available(self):
        return True

    def load(self, user):
        return self.items.get(user)

    def save(self, user, password, expires):
        self.items[user] = password

    def clear(self, user):
        self.items.pop(user, None)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.store, self.clock = FakeStore(), Clock()
        self.state = {"username": "me@example.com"}
        self.stay = StaySignedIn(self.store, self.state, self.clock)

    def test_off_by_default(self):
        self.assertFalse(self.stay.active())
        self.assertEqual(self.stay.agent_timeout(), DEFAULT_AGENT_TIMEOUT)
        self.stay.begin_command("ls")
        self.assertIsNone(self.stay.answer("Master Password", 1))

    def test_typed_password_is_saved_and_reused(self):
        self.stay.enable()
        self.stay.command_succeeded([("Master Password", "secret")])
        self.assertEqual(self.store.items["me@example.com"], "secret")
        self.stay.begin_command("ls")
        self.assertEqual(self.stay.answer("Master Password", 1), "secret")
        self.assertIsNone(self.stay.answer("Code", 1), "MFA codes are never stored")

    def test_never_answers_reprompts(self):
        self.stay.enable()
        self.store.items["me@example.com"] = "secret"
        for cmd in ("show", "export"):
            self.stay.begin_command(cmd)
            self.assertIsNone(self.stay.answer("Master Password", 1))

    def test_rejected_stored_password_is_forgotten(self):
        self.stay.enable()
        self.store.items["me@example.com"] = "old"
        self.stay.begin_command("ls")
        self.assertEqual(self.stay.answer("Master Password", 1), "old")
        self.assertIsNone(self.stay.answer("Master Password", 2))
        self.assertNotIn("me@example.com", self.store.items)

    def test_expires_after_30_days(self):
        self.stay.enable()
        self.store.items["me@example.com"] = "secret"
        self.assertAlmostEqual(self.stay.agent_timeout(), DURATION, delta=1)
        self.clock.now += DURATION - 10
        self.assertEqual(self.stay.agent_timeout(), DEFAULT_AGENT_TIMEOUT)  # never below lpass's default
        self.clock.now += 20
        self.stay.begin_command("ls")
        self.assertIsNone(self.stay.answer("Master Password", 1))
        self.assertFalse(self.stay.active())
        self.assertNotIn("me@example.com", self.store.items)
        self.assertNotIn("signed_in_until", self.state)

    def test_lock_suspends_until_typed_unlock(self):
        self.stay.enable()
        self.store.items["me@example.com"] = "secret"
        self.stay.suspended = True
        self.stay.begin_command("ls")
        self.assertIsNone(self.stay.answer("Master Password", 1))
        self.stay.command_succeeded([("Master Password", "secret")])
        self.assertFalse(self.stay.suspended)

    def test_disable_forgets_password(self):
        self.stay.enable()
        self.store.items["me@example.com"] = "secret"
        self.stay.disable()
        self.assertFalse(self.stay.active())
        self.assertNotIn("me@example.com", self.store.items)


@unittest.skipUnless(MOCK.exists(), "lastpass-cli mock build (lpass-test) not available")
class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lps", dir="/tmp")
        os.environ["LPASS_HOME"] = self.home
        self.asked = []
        self.store = FakeStore()
        self.state = {"username": "user@example.com"}
        self.stay = StaySignedIn(self.store, self.state)

        def handler(prompt, attempt, error):
            stored = self.stay.answer(prompt, attempt)
            if stored is not None:
                return stored
            self.asked.append((self.lp.command, prompt))
            return PW
        # lpass makes its agent non-dumpable (its /proc environ is unreadable), so log
        # the LPASS_AGENT_TIMEOUT each lpass invocation gets; the agent inherits it.
        self.timeout_log = os.path.join(self.home, "timeouts")
        wrapper = os.path.join(self.home, "lpass-wrap")
        with open(wrapper, "w") as f:
            f.write(f'#!/bin/sh\necho "$LPASS_AGENT_TIMEOUT" >> {self.timeout_log}\nexec {MOCK} "$@"\n')
        os.chmod(wrapper, 0o755)
        self.lp = Lpass(handler, binary=wrapper)
        self.lp.agent_timeout = self.stay.agent_timeout
        self.lp.on_command_start = self.stay.begin_command
        self.lp.on_command_success = self.stay.command_succeeded

    def tearDown(self):
        self.lp.lock()
        self.lp.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def last_timeout(self):
        with open(self.timeout_log) as f:
            return int(f.read().split()[-1])

    def test_stay_signed_in_flow(self):
        self.stay.enable()
        self.lp.login("user@example.com")
        self.lp.list_entries()  # first sync downloads the vault
        self.assertEqual(self.asked, [("login", "Master Password")])
        self.assertEqual(self.store.items.get("user@example.com"), PW, "typed password saved")

        # lpass's agent got the 30-day timeout
        self.assertGreater(self.last_timeout(), DURATION - 60)

        # lock(): agent really stops; the next unlock comes from the keyring silently
        self.assertTrue(self.lp.lock())
        for _ in range(50):
            if not os.path.exists(self.lp.agent_socket_path()):
                break
            time.sleep(0.05)
        self.assertFalse(os.path.exists(self.lp.agent_socket_path()))
        self.asked.clear()
        self.assertTrue(self.lp.list_entries(sync="no"))
        self.assertEqual(self.asked, [], "unlock answered from keyring")

        # re-prompt for a protected item still goes to the user
        self.lp.show("0003")
        self.assertEqual(self.asked, [("show", "Master Password")])

    def test_without_stay_user_is_asked_after_lock(self):
        self.lp.login("user@example.com")
        self.lp.list_entries()
        self.assertEqual(self.store.items, {}, "nothing stored when opted out")
        self.assertEqual(self.last_timeout(), DEFAULT_AGENT_TIMEOUT)
        self.lp.lock()
        time.sleep(0.3)
        self.asked.clear()
        self.lp.list_entries(sync="no")
        self.assertEqual(self.asked, [("ls", "Master Password")])

    def test_no_session_needs_full_login(self):
        with self.assertRaises(LpassError) as cm:
            self.lp.list_entries(sync="no")
        self.assertIn("decryption key", cm.exception.message.lower())
        self.assertEqual(self.asked, [], "no prompt without a saved session")


if __name__ == "__main__":
    unittest.main()
