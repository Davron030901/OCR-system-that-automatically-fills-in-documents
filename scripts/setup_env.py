#!/usr/bin/env python3
"""Create .env from .env.example and fill in a generated encryption key.

Deliberately stdlib-only: this is the first command a person runs on a fresh
clone, before any dependency is installed. An AES-256 key is 32 random bytes,
so importing the application package here would make setup depend on a full
install for no reason.

Writing the key IN PLACE rather than appending matters. `make keys >> .env`
run twice leaves two ENCRYPTION_KEY lines, the later one silently wins, and
anything encrypted under the first key becomes unreadable.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

# Values that look filled in but are not. Shipping with these is worse than
# shipping with an empty variable, because an empty one fails loudly at
# startup while a placeholder silently becomes the production secret.
PLACEHOLDERS = {
    "generate-a-random-value",
    "change-me-in-production",
    "changeme",
    "",
}

KEYS_TO_FILL = {
    "ENCRYPTION_KEY": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "JWT_SECRET": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "INTERNAL_TOKEN": lambda: base64.urlsafe_b64encode(os.urandom(24)).decode(),
}


def main() -> int:
    if not EXAMPLE.exists():
        print(f"error: {EXAMPLE} not found", file=sys.stderr)
        return 1

    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text())
        print("created .env from .env.example")

    lines = ENV.read_text().splitlines()
    filled: list[str] = []

    for name, generate in KEYS_TO_FILL.items():
        prefix = f"{name}="
        existing = [
            ln for ln in lines
            if ln.startswith(prefix)
            and ln[len(prefix):].strip() not in PLACEHOLDERS
        ]
        if existing:
            continue

        value = generate()
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith(prefix):
                lines[i] = prefix + value
                replaced = True
                break
        if not replaced:
            lines.append(prefix + value)
        filled.append(name)

    # Strip lines that cannot be valid. A .env line without '=' is not a
    # setting under any interpretation, and neither is one whose name contains
    # a space -- both are almost always command output that landed here via a
    # shell redirect. Reporting them and stopping just moves the work to the
    # person; removing them is unambiguous and safe, so do it.
    kept: list[str] = []
    removed: list[tuple[int, str]] = []

    for n, ln in enumerate(lines, 1):
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(ln)
            continue
        if "=" not in stripped:
            removed.append((n, ln))
            continue
        if " " in stripped.split("=", 1)[0]:
            removed.append((n, ln))
            continue
        kept.append(ln)

    lines = kept
    ENV.write_text("\n".join(lines) + "\n")

    if filled:
        print("generated: " + ", ".join(filled))
    elif not removed:
        print(".env already has its secrets; nothing changed")

    if removed:
        print(f"\nremoved {len(removed)} invalid line(s) "
              "(command output that had been redirected into .env):")
        for n, ln in removed:
            print(f"  line {n}: {ln[:60]}")

    print("\n.env is ready. Next: make dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
