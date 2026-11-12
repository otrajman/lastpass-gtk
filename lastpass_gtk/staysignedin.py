"""'Stay signed in for 30 days': policy plus a keyring-backed store.

lastpass-cli keeps the encrypted vault and session on disk, but the decryption key
lives in an in-memory agent that exits after LPASS_AGENT_TIMEOUT (1 h by default)
and on reboot. When the user opts in, we:

  * run the agent with a timeout that lasts until the 30-day window ends, and
  * keep the master password in the desktop keyring (Secret Service: GNOME Keyring,
    KWallet, ...) so unlock prompts after a reboot or app restart are answered
    automatically until the window ends.

The stored password is never used to answer a *re-prompt*: the extra confirmation
lastpass-cli asks for before showing items marked "require master password"
(`show` / `export`). Those always go to the user.

This module has no GTK dependency so the policy can be unit tested.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

DURATION = 30 * 24 * 3600
DEFAULT_AGENT_TIMEOUT = 3600            # lpass's own default
UNLOCK_PROMPT = "Master Password"
REPROMPT_COMMANDS = {"show", "export"}  # where "Master Password" may be a re-prompt


class SecretStore(Protocol):
    def available(self) -> bool: ...
    def load(self, username: str) -> Optional[str]: ...
    def save(self, username: str, password: str, expires: int) -> None: ...
    def clear(self, username: str) -> None: ...


class KeyringStore:
    """Master password in the desktop keyring via libsecret (if installed)."""

    def __init__(self):
        self._secret = None
        self._schema = None
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            self._secret = Secret
            self._schema = Secret.Schema.new(
                "io.github.otrajman.LastPassGtk.MasterPassword", Secret.SchemaFlags.NONE,
                {"username": Secret.SchemaAttributeType.STRING,
                 "expires": Secret.SchemaAttributeType.STRING})
        except (ImportError, ValueError):
            pass

    def available(self) -> bool:
        if self._secret is None:
            return False
        try:
            self._secret.Service.get_sync(self._secret.ServiceFlags.NONE, None)
            return True
        except Exception:  # noqa: BLE001 - no Secret Service on the bus
            return False

    def load(self, username: str) -> Optional[str]:
        if self._secret is None:
            return None
        try:
            items = self._secret.password_search_sync(
                self._schema, {"username": username},
                self._secret.SearchFlags.UNLOCK | self._secret.SearchFlags.LOAD_SECRETS, None)
        except Exception:  # noqa: BLE001
            return None
        now = time.time()
        for item in items:
            try:
                expires = int(item.get_attributes().get("expires", "0"))
            except ValueError:
                expires = 0
            if expires > now:
                value = item.retrieve_secret_sync(None)
                if value is not None:
                    return value.get_text()
        return None

    def save(self, username: str, password: str, expires: int) -> None:
        if self._secret is None:
            return
        self.clear(username)
        self._secret.password_store_sync(
            self._schema, {"username": username, "expires": str(expires)},
            self._secret.COLLECTION_DEFAULT, f"LastPass GTK master password ({username})",
            password, None)

    def clear(self, username: str) -> None:
        if self._secret is None:
            return
        try:
            self._secret.password_clear_sync(self._schema, {"username": username}, None)
        except Exception:  # noqa: BLE001
            pass


class StaySignedIn:
    """Decides when to answer lpass's unlock prompt from the keyring.

    `state` is the app's persisted settings dict; we use the key "signed_in_until"
    (epoch seconds, absent when the feature is off).
    """

    def __init__(self, store: SecretStore, state: dict, clock: Callable[[], float] = time.time):
        self.store = store
        self.state = state
        self.clock = clock
        self.suspended = False      # set by "Lock now" until the user unlocks by hand
        self._autofilled = False    # did we answer the current command's unlock prompt?

    # -- settings ----------------------------------------------------------
    @property
    def until(self) -> int:
        return int(self.state.get("signed_in_until", 0) or 0)

    def active(self) -> bool:
        if self.until and self.until <= self.clock():
            self.disable()          # window ended: forget the password
        return bool(self.until)

    def enable(self) -> None:
        self.state["signed_in_until"] = int(self.clock()) + DURATION

    def disable(self) -> None:
        user = self.state.get("username", "")
        self.state.pop("signed_in_until", None)
        if user:
            self.store.clear(user)

    def agent_timeout(self) -> int:
        """Seconds the lpass agent should stay unlocked (LPASS_AGENT_TIMEOUT)."""
        if not self.active():
            return DEFAULT_AGENT_TIMEOUT
        return max(DEFAULT_AGENT_TIMEOUT, self.until - int(self.clock()))

    # -- prompt handling (called from a background thread) -----------------
    def begin_command(self, command: str) -> None:
        self._command = command
        self._autofilled = False

    def answer(self, prompt: str, attempt: int) -> Optional[str]:
        """The stored password if this prompt may be answered automatically."""
        user = self.state.get("username", "")
        if prompt != UNLOCK_PROMPT or not user or self.suspended or not self.active():
            return None
        if attempt > 1:
            if self._autofilled:     # stored password was rejected (changed elsewhere)
                self.store.clear(user)
            return None
        if getattr(self, "_command", "") in REPROMPT_COMMANDS:
            return None
        password = self.store.load(user)
        if password is not None:
            self._autofilled = True
        return password

    def command_succeeded(self, answers: list[tuple[str, str]]) -> None:
        """lpass accepted the master password the user typed: remember it if opted in."""
        typed = [a for p, a in answers if p == UNLOCK_PROMPT]
        user = self.state.get("username", "")
        if typed and user:
            self.suspended = False
            if self.active():
                self.store.save(user, typed[-1], self.until)
