#!/usr/bin/env python3
"""LPASS_ASKPASS helper.

lpass runs this as `lpass-askpass PROMPT` whenever it needs a secret (master
password, MFA code, reprompt). Instead of reading a terminal, we forward the
prompt to the running GUI over a private Unix socket and print the reply.

Exit status 1 with no output means "cancelled"; lpass then aborts the command.
"""
import json
import os
import socket
import sys


def main() -> int:
    path = os.environ.get("LASTPASS_GTK_SOCK")
    token = os.environ.get("LASTPASS_GTK_TOKEN", "")
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Password"
    # Our patched lpass passes the reason the previous attempt failed as argv[2].
    error = sys.argv[2] if len(sys.argv) > 2 else ""
    if not path:
        return 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(path)
            s.sendall(json.dumps({"token": token, "prompt": prompt, "error": error}).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError:
        return 1
    if not buf:
        return 1
    try:
        reply = json.loads(buf)
    except ValueError:
        return 1
    if reply.get("cancel") or "answer" not in reply:
        return 1
    sys.stdout.write(reply["answer"] + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
