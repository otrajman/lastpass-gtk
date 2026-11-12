"""Thin, GUI-agnostic wrapper around the official `lpass` CLI.

All vault crypto and network protocol work is done by lastpass-cli
(https://github.com/lastpass/lastpass-cli). This module only builds command
lines, feeds stdin, parses machine-readable output, and bridges lpass's
LPASS_ASKPASS prompts to a callback supplied by the UI.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Separators that will never appear in names/urls/usernames.
FS = "\x1f"  # field separator
RS = "\x1e"  # record separator

SECURE_NOTE_URL = "http://sn"

# Field names (from `show --all`) that are not user-defined custom fields.
_BUILTIN = {"Username", "Password", "URL", "Notes", "Reprompt", "Application"}


class LpassError(Exception):
    """lpass exited non-zero. `.message` is lpass's own error text."""

    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.message = message
        self.returncode = returncode

    @property
    def cancelled(self) -> bool:
        return "askpass" in self.message.lower()


@dataclass
class Entry:
    id: str
    fullname: str  # folder path + name, e.g. "Shared-Team/Email/gmail"
    name: str
    username: str = ""
    url: str = ""
    group: str = ""
    share: str = ""
    last_modified: str = ""
    last_touch: str = ""

    @property
    def is_note(self) -> bool:
        return self.url == SECURE_NOTE_URL

    @property
    def folder(self) -> str:
        """Folder path (everything in fullname before the last '/')."""
        return self.fullname.rpartition("/")[0]


@dataclass
class Detail:
    """Everything `lpass show --all` reveals about one entry."""

    entry_id: str
    username: str = ""
    password: str = ""
    url: str = ""
    application: str = ""
    notes: str = ""
    reprompt: bool = False
    fields: list[tuple[str, str]] = field(default_factory=list)  # custom/note-type fields
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (att-id, filename)


def find_lpass() -> str:
    """Locate the lpass binary: env override, bundled build, installed copy, PATH."""
    env = os.environ.get("LASTPASS_GTK_LPASS")
    if env:
        return env
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "libexec" / "lpass",  # installed/packaged alongside the package
        here.parent / "build" / "lpass" / "lpass",  # repo checkout (scripts/build-lpass.sh)
        here.parent.parent / "lastpass-cli" / "build" / "lpass",  # sibling checkout
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    found = shutil.which("lpass")
    if found:
        return found
    raise FileNotFoundError(
        "Could not find the `lpass` binary. Build lastpass-cli (see README) "
        "or set LASTPASS_GTK_LPASS=/path/to/lpass."
    )


# A prompt handler receives lpass's prompt label ("Master Password", "Code", ...)
# the attempt number for that label within the current command (2+ means the
# previous attempt failed), and lpass's reason for that failure ("" if unknown,
# e.g. with an unpatched lpass). It returns the secret, or None to cancel.
# It is called from a background thread.
PromptHandler = Callable[[str, int, str], Optional[str]]


class AskpassBridge:
    """Unix-socket server that answers prompts coming from askpass.py."""

    def __init__(self, handler: PromptHandler):
        self._handler = handler
        self.token = secrets.token_hex(16)
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        # Private dir so the socket isn't reachable by other users even briefly.
        self._dir = tempfile.mkdtemp(prefix="lpgtk-", dir=runtime)
        os.chmod(self._dir, stat.S_IRWXU)
        self.path = os.path.join(self._dir, "askpass.sock")
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        self._sock.listen(4)
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            try:
                req = json.loads(buf)
            except ValueError:
                return
            if not secrets.compare_digest(str(req.get("token", "")), self.token):
                return
            answer = self._handler(str(req.get("prompt", "Password")), str(req.get("error", "")))
            reply = {"cancel": True} if answer is None else {"answer": answer}
            try:
                conn.sendall(json.dumps(reply).encode() + b"\n")
            except OSError:
                pass

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        finally:
            shutil.rmtree(self._dir, ignore_errors=True)


class Lpass:
    def __init__(self, prompt_handler: PromptHandler, binary: str | None = None,
                 extra_env: dict[str, str] | None = None):
        self.binary = binary or find_lpass()
        self._attempts: dict[str, int] = {}
        self._answers: list[tuple[str, str]] = []
        self.command = ""  # the lpass subcommand currently running
        # Optional hooks (used by "stay signed in"):
        self.agent_timeout: Optional[Callable[[], int]] = None   # -> LPASS_AGENT_TIMEOUT
        self.on_command_start: Optional[Callable[[str], None]] = None
        self.on_command_success: Optional[Callable[[list[tuple[str, str]]], None]] = None

        def counted(prompt: str, error: str) -> Optional[str]:
            self._attempts[prompt] = self._attempts.get(prompt, 0) + 1
            answer = prompt_handler(prompt, self._attempts[prompt], error)
            if answer is not None:
                self._answers.append((prompt, answer))
            return answer
        self.bridge = AskpassBridge(counted)
        askpass = Path(__file__).resolve().parent / "askpass.py"
        mode = askpass.stat().st_mode
        if not mode & stat.S_IXUSR:
            os.chmod(askpass, mode | stat.S_IXUSR)
        self.env = dict(os.environ)
        self.env.update({
            "LPASS_ASKPASS": str(askpass),
            "LASTPASS_GTK_SOCK": self.bridge.path,
            "LASTPASS_GTK_TOKEN": self.bridge.token,
            # askpass.py's shebang uses `python3`; make sure it's the same one.
            "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""),
        })
        if extra_env:
            self.env.update(extra_env)

    def close(self) -> None:
        self.bridge.close()

    # -- low level ---------------------------------------------------------
    def run(self, *args: str, stdin: str | None = None, binary_out: bool = False):
        self._attempts.clear()  # callers serialize lpass invocations
        self._answers.clear()
        self.command = args[0]
        env = self.env
        if self.agent_timeout:
            env = {**env, "LPASS_AGENT_TIMEOUT": str(int(self.agent_timeout()))}
        if self.on_command_start:
            self.on_command_start(self.command)
        try:
            proc = subprocess.run(
                [self.binary, *args],
                input=stdin.encode() if stdin is not None else None,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                env=env,
            )
        finally:
            answers, self._answers = list(self._answers), []
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            out = proc.stdout.decode(errors="replace").strip()
            msg = err or out or f"lpass {args[0]} failed ({proc.returncode})"
            if msg.startswith("Error: "):
                msg = msg[len("Error: "):]
            raise LpassError(msg, proc.returncode)
        if self.on_command_success and answers:
            self.on_command_success(answers)
        return proc.stdout if binary_out else proc.stdout.decode(errors="replace")

    def agent_socket_path(self) -> str:
        """Where lpass's agent listens (mirrors config_path() in lastpass-cli)."""
        env = self.env
        if env.get("LPASS_HOME"):
            return os.path.join(env["LPASS_HOME"], "agent.sock")
        if env.get("XDG_RUNTIME_DIR"):
            return os.path.join(env["XDG_RUNTIME_DIR"], "lpass", "agent.sock")
        return os.path.join(env.get("HOME", ""), ".lpass", "agent.sock")

    def lock(self) -> bool:
        """Stop the lpass agent so the next command needs the master password.

        Like lastpass-cli's agent_kill(): find the agent's pid from the socket's peer
        credentials and SIGTERM it. Returns True if an agent was stopped.
        """
        path = self.agent_socket_path()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(path)
                pid, uid, _gid = struct.unpack("3i", s.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        except OSError:
            return False
        if uid != os.getuid() or pid <= 1:
            return False
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"lpass" not in f.read():
                    return False
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True

    # -- session -----------------------------------------------------------
    def status(self) -> Optional[str]:
        """Return logged-in username, or None."""
        try:
            out = self.run("status", "--color=never")
        except LpassError:
            return None
        out = out.strip()
        prefix = "Logged in as "
        if out.startswith(prefix):
            return out[len(prefix):].rstrip(".")
        return None

    def login(self, username: str, trust: bool = False) -> None:
        args = ["login", "--color=never"]
        if trust:
            args.append("--trust")
        self.run(*args, username)

    def logout(self) -> None:
        self.run("logout", "--force", "--color=never")

    def sync(self) -> None:
        self.run("sync", "--color=never")

    # -- reading -----------------------------------------------------------
    def list_entries(self, sync: str = "auto") -> list[Entry]:
        fmt = FS.join(["%ai", "%aN", "%an", "%au", "%al", "%ag", "%as", "%am", "%aU"]) + RS
        out = self.run("ls", f"--sync={sync}", "--color=never", "-l", f"--format={fmt}")
        return parse_entries(out)

    def show(self, entry_id: str, sync: str = "no") -> Detail:
        out = self.run(
            "show", f"--sync={sync}", "--all", "--color=never",
            f"--title-format={RS}", f"--format=%fn{FS}%fv{RS}", entry_id,
        )
        return parse_detail(entry_id, out)

    def save_attachment(self, entry_id: str, attach_id: str) -> bytes:
        return self.run("show", "--sync=no", "--quiet", f"--attach={attach_id}", entry_id,
                        binary_out=True)

    # -- writing -----------------------------------------------------------
    def add(self, fullname: str, template: str) -> None:
        self.run("add", "--sync=now", "--non-interactive", "--color=never", fullname,
                 stdin=template)

    def edit(self, entry_id: str, template: str) -> None:
        self.run("edit", "--sync=now", "--non-interactive", "--color=never", entry_id,
                 stdin=template)

    def remove(self, entry_id: str) -> None:
        self.run("rm", "--sync=now", "--color=never", entry_id)

    def duplicate(self, entry_id: str) -> None:
        self.run("duplicate", "--sync=now", "--color=never", entry_id)


# -- parsing / templating (pure functions, unit tested) -----------------------

def parse_entries(out: str) -> list[Entry]:
    entries = []
    for rec in out.split(RS):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        parts = rec.split(FS)
        if len(parts) < 9:
            continue
        eid, fullname, name, user, url, group, share, mod, touch = parts[:9]
        entries.append(Entry(eid, fullname, name, user, url, group, share, mod, touch))
    return entries


def parse_detail(entry_id: str, out: str) -> Detail:
    d = Detail(entry_id)
    recs = out.split(RS)
    # first record is the title (empty); the rest are "name FS value"
    for rec in recs[1:]:
        rec = rec[1:] if rec.startswith("\n") else rec
        if FS not in rec:
            continue
        name, value = rec.split(FS, 1)
        if name != "Notes":
            value = value.strip()  # secure-note fields come back as " value"
        if name == "Username":
            d.username = value
        elif name == "Password":
            d.password = value
        elif name == "URL":
            d.url = value
        elif name == "Application":
            d.application = value
        elif name == "Notes":
            d.notes = value
        elif name == "Reprompt":
            d.reprompt = value.strip() == "Yes"
        elif name.startswith("att-"):
            d.attachments.append((name, value))
        else:
            d.fields.append((name, value))
    return d


def _clean(value: str) -> str:
    # lpass's template is line based; only Notes may span lines.
    return " ".join(value.splitlines()).strip()


def build_template(fullname: str, *, url: str = "", username: str = "", password: str = "",
                   fields: list[tuple[str, str]] | None = None, notes: str = "",
                   reprompt: bool = False, is_note: bool = False) -> str:
    """Build the text lpass's editor would produce, for `--non-interactive`."""
    # Reprompt goes right after Name: in typed secure notes, unknown keys that
    # follow a multi-line field would otherwise be folded into that field.
    lines = [f"Name: {_clean(fullname)}", f"Reprompt: {'Yes' if reprompt else 'No'}"]
    if not is_note:
        lines += [f"URL: {_clean(url)}", f"Username: {_clean(username)}",
                  f"Password: {_clean(password)}"]
    else:
        # Typed secure notes (Server, Database, ...) carry these as note fields.
        for k, v in (("URL", url), ("Username", username), ("Password", password)):
            if v:
                lines.append(f"{k}: {_clean(v)}")
    for k, v in fields or []:
        k = _clean(k).replace(":", "")
        if k and k not in _BUILTIN:
            lines.append(f"{k}: {_clean(v)}")
    lines.append("Notes:    # Add notes below this line.")
    return "\n".join(lines) + "\n" + notes
