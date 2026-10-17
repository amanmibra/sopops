"""Prompt files. Each starts with `<!-- prompt-version: NAME-N -->`; bump N whenever the text changes, so every stored
result says which prompt produced it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_VERSION = re.compile(r"\A<!--\s*prompt-version:\s*(\S+)\s*-->\s*\n")


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str


@cache
def load(name: str) -> Prompt:
    raw = (Path(__file__).parent / f"{name}.md").read_text()
    m = _VERSION.match(raw)
    if not m:
        raise ValueError(f"prompt {name}.md has no '<!-- prompt-version: ... -->' first line")
    return Prompt(m.group(1), raw[m.end():].strip() + "\n")
