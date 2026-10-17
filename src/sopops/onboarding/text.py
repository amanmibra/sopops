"""Text handling that needs no judgment: splitting prompts into units, comparing passages, and turning
near-copies into one template with slots for the values that differ."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

HEADING = re.compile(r"^#{1,6}\s+\S")
LIST_MARK = re.compile(r"^(\s*)(?:\d+[.)]|[-*+•])\s+", re.M)
TOKEN = re.compile(r"[\w'’]+|\s+|[^\w\s]")
URLISH = re.compile(r"\w[./]\w|@|https?:|www\.", re.I)
PLACEHOLDER = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

# Words whose difference between two near-copies changes a rule rather than a fact: "once" vs "twice",
# "always" vs "never", "before" vs "after". Differences in these are drift, not per-agent values.
POLICY_WORDS = {
    "once", "twice", "thrice", "always", "never", "before", "after", "can", "cannot", "can't", "do", "don't", "does",
    "doesn't", "must", "mustn't", "should", "shouldn't", "may", "might", "not", "no", "yes", "only", "every", "all",
    "any", "more", "less", "fewer", "most", "least", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "first", "last", "unless", "and", "or", "but", "will", "won't", "allowed", "forbidden",
}


def split_units(prompt: str) -> list[str]:
    """Paragraphs (split at blank lines), with a Markdown heading in the middle of one starting a new unit.
    Joining the units with blank lines gives the prompt back, up to blank-line runs and trailing spaces."""
    units: list[str] = []
    for para in re.split(r"\n[ \t]*\n", prompt.replace("\r\n", "\n").strip("\n")):
        lines = para.rstrip().split("\n")
        current: list[str] = []
        for line in lines:
            if HEADING.match(line) and current and any(x.strip() for x in current):
                units.append("\n".join(current).strip("\n"))
                current = []
            current.append(line.rstrip())
        text = "\n".join(current).strip("\n")
        if text.strip():
            units.append(text)
    return units


def norm(text: str) -> str:
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return re.findall(r"[\w'’]+", text.lower())


def similarity(a: str, b: str) -> float:
    """Word-level similarity, 0-1."""
    wa, wb = words(a), words(b)
    if not wa and not wb:
        return 1.0
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def strip_list_marks(text: str) -> str:
    """Without list numbering and bullets, so a procedure with a step inserted still matches its copies."""
    return LIST_MARK.sub(r"\1", text)


def slug(text: str, fallback: str = "item") -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].strip("-")
    return s or fallback


def var_name(text: str, fallback: str = "value") -> str:
    s = slug(text, fallback).replace("-", "_")
    return s if re.match(r"^[a-z]", s) else f"v_{s}"


def unique(base: str, taken: set[str], sep: str = "-") -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base}{sep}{n}"
        n += 1
    taken.add(name)
    return name


# --- templates ------------------------------------------------------------------------------------


@dataclass
class Slot:
    """A place where near-copies differ. `values[i]` is variant i's text there."""

    values: list[str]
    name: str = ""

    @property
    def is_policy(self) -> bool:
        """Drift (a rule differs) rather than a per-agent value (a name, link or number)."""
        texts = [v.strip() for v in self.values]
        if any(URLISH.search(t) or re.search(r"\d", t) for t in texts):
            return False
        toks = [w for t in texts for w in words(t)]
        if toks and all(w in POLICY_WORDS for w in toks):
            return True
        # Capitalized names ("Golden Wok") are values; lowercase phrases ("the head chef") are treated as drift.
        return not any(t[:1].isupper() for t in texts if t)


@dataclass
class Template:
    """Text with `{{slot}}` places; `parts` alternates literal text and Slot objects."""

    parts: list[str | Slot] = field(default_factory=list)

    @property
    def slots(self) -> list[Slot]:
        return [p for p in self.parts if isinstance(p, Slot)]

    def render(self, names: bool = True, variant: int | None = None) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, Slot):
                out.append(f"{{{{{p.name}}}}}" if variant is None and names else p.values[variant or 0])
            else:
                out.append(p)
        return "".join(out)


MAX_SLOT_WORDS = 8
MAX_SLOTS = 5
MAX_SLOT_SHARE = 0.3


def template(variants: list[str]) -> Template | None:
    """One template for several near-copies, or None if they differ by more than short values.
    variants[0] is the base; every variant renders back exactly (`render(variant=i)`)."""
    toks = [TOKEN.findall(v) for v in variants]
    base = toks[0]
    n = len(base)
    ranges: list[tuple[int, int]] = []  # base token ranges that differ in some variant
    maps: list[tuple[dict[int, int], dict[int, int]]] = []
    for other in toks[1:]:
        left: dict[int, int] = {}
        right: dict[int, int] = {}

        def put(b: int, j: int) -> None:
            left[b] = min(left.get(b, j), j)
            right[b] = max(right.get(b, j), j)

        for tag, i1, i2, j1, j2 in SequenceMatcher(None, base, other, autojunk=False).get_opcodes():
            if tag == "equal":
                for k in range(i1, i2 + 1):
                    put(k, j1 + k - i1)
            else:
                put(i1, j1)
                put(i2, j2)
                ranges.append((i1, i2))
        maps.append((left, right))
    if not ranges:
        return Template([variants[0]])

    # Merge overlapping ranges, and ranges separated only by whitespace or one punctuation mark.
    ranges.sort()
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged:
            gap = base[merged[-1][1] : s]
            if s <= merged[-1][1] or (len(gap) <= 2 and all(not re.match(r"[\w'’]", t) for t in gap)):
                merged[-1][1] = max(merged[-1][1], e)
                continue
        merged.append([s, e])

    parts: list[str | Slot] = []
    pos = 0
    content = sum(1 for t in base if t.strip())
    covered = 0
    for s, e in merged:
        parts.append("".join(base[pos:s]))
        values = ["".join(base[s:e])]
        for (left, right), other in zip(maps, toks[1:]):
            values.append("".join(other[left[s] : right[e]]))
        # A value is a phrase inside one sentence: never a line break or a sentence boundary.
        if any(len(words(v)) > MAX_SLOT_WORDS or "\n" in v or re.search(r"[.!?]\s+\S", v) for v in values):
            return None
        covered += max(len([t for t in TOKEN.findall(v) if t.strip()]) for v in values)
        parts.append(Slot(values))
        pos = e
    parts.append("".join(base[pos:]))
    if len(merged) > MAX_SLOTS or covered > max(2, MAX_SLOT_SHARE * content):
        return None
    t = Template([p for p in parts if p != ""])
    if any(t.render(variant=i) != v for i, v in enumerate(variants)):
        return None
    return t
