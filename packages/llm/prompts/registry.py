"""Versioned prompt registry.

A prompt is production configuration, not a string literal. Three properties
matter and none of them survive an inline triple-quoted constant:

  * versioning — a prompt edit changes model behaviour exactly as much as a
    model swap does. Every response carries the prompt version that produced
    it, so a result from three weeks ago can be explained.
  * pinning — a rollout can be held on v2 while v3 is evaluated, by
    configuration rather than by deploy.
  * regression testing — each prompt ships with a synthetic evaluation set, so
    "the new prompt is better" is a measurement instead of an impression.

Files are named `<name>.v<N>.txt` and live beside this module. The highest
version wins unless PROMPT_PIN_<NAME> pins one.

⚠️ Prompt files must never contain real personal data. Few-shot examples are
built from the synthetic generator, because a prompt is copied into logs,
issue reports and provider request bodies.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
FILENAME = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+)\.txt$")

# Every prompt must carry this instruction. Enforced by a test rather than by
# convention, because the single most expensive failure in this system is a
# model that fills a blank with something plausible.
REQUIRED_INSTRUCTION = "return null"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str          # "v3"
    text: str
    path: Path

    @property
    def id(self) -> str:
        return f"{self.name}.{self.version}"


def _discover() -> dict[str, dict[int, Path]]:
    found: dict[str, dict[int, Path]] = {}
    for path in sorted(PROMPT_DIR.glob("*.txt")):
        m = FILENAME.match(path.name)
        if not m:
            continue
        found.setdefault(m["name"], {})[int(m["version"])] = path
    return found


@cache
def _index() -> dict[str, dict[int, Path]]:
    return _discover()


def available() -> dict[str, list[int]]:
    """{prompt name: [versions]} — useful in /readyz and in tests."""
    return {name: sorted(versions) for name, versions in _index().items()}


def load(name: str, version: int | None = None) -> Prompt:
    """Load a prompt. Highest version wins unless pinned or requested.

    Pin with PROMPT_PIN_ID_VISUAL_ZONE=2 to hold a rollout on an older prompt
    without shipping code.
    """
    versions = _index().get(name)
    if not versions:
        raise KeyError(
            f"no prompt named {name!r}; available: {sorted(_index())}"
        )
    if version is None:
        pinned = os.getenv(f"PROMPT_PIN_{name.upper()}")
        if pinned:
            try:
                version = int(pinned)
            except ValueError as exc:
                raise ValueError(
                    f"PROMPT_PIN_{name.upper()}={pinned!r} is not a version number"
                ) from exc
    if version is None:
        version = max(versions)
    if version not in versions:
        raise KeyError(
            f"prompt {name} has no v{version}; available: {sorted(versions)}"
        )
    path = versions[version]
    return Prompt(name=name, version=f"v{version}",
                  text=path.read_text(encoding="utf-8").strip(), path=path)


def reload() -> None:
    """Drop the discovery cache. For tests that write prompt files."""
    _index.cache_clear()
