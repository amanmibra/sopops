"""Three-way line merge, for a suggestion written against a file that has changed since.

`merge(base, ours, theirs)`: `base` is the file the suggestion was written against, `ours` the file now, `theirs` the
suggested file. Each side's changes to `base` are hunks (`difflib`, no junk heuristic): a range of base lines and what
replaces it. Hunks from the two sides that touch the same base lines, or insert at the same place, are one region; a
region changed by one side takes that side's lines, one changed the same way by both takes them once, and any other is
a conflict. Edits to neighbouring lines don't conflict (each rule of a procedure is a line, and two fixes to two
neighbouring rules are independent); an insertion right before or after a changed line goes on its side of it.

`contains(base, edited, text)`: whether `text` already has the edit from `base` to `edited` (merging it in changes
nothing), for finding a suggestion's edit in a release that has other edits too.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Hunk:
    start: int  # base lines [start, end) are replaced by `lines`
    end: int
    lines: tuple[str, ...]
    side: str  # "ours" | "theirs"


@dataclass(frozen=True)
class Conflict:
    line: int  # where the region starts in `ours`, 1-based
    base: str
    ours: str
    theirs: str


@dataclass
class Merged:
    text: str | None  # None when there are conflicts
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def hunks(base: list[str], other: list[str], side: str) -> list[Hunk]:
    m = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [Hunk(i1, i2, tuple(other[j1:j2]), side) for op, i1, i2, j1, j2 in m.get_opcodes() if op != "equal"]


def _touches(h: Hunk, start: int, end: int) -> bool:
    """Whether a hunk is in the same region as base lines [start, end): overlapping lines, the same insertion point,
    or an insertion strictly inside the range."""
    if h.start == h.end and start == end:
        return h.start == start
    if h.start == h.end:
        return start < h.start < end
    if start == end:
        return h.start < start < h.end
    return h.start < end and start < h.end


def _apply(base: list[str], start: int, end: int, hs: list[Hunk]) -> list[str]:
    """Base lines [start, end) with one side's hunks (all inside the range) applied."""
    out, at = [], start
    for h in sorted(hs, key=lambda h: (h.start, h.end)):
        out += base[at : h.start]
        out += h.lines
        at = h.end
    return out + base[at:end]


def merge(base: str, ours: str, theirs: str) -> Merged:
    b, o, t = _lines(base), _lines(ours), _lines(theirs)
    all_hunks = sorted(hunks(b, o, "ours") + hunks(b, t, "theirs"), key=lambda h: (h.start, h.end, h.side))
    regions: list[list[Hunk]] = []
    lo = hi = 0
    for h in all_hunks:
        if regions and _touches(h, lo, hi):
            regions[-1].append(h)
            lo, hi = min(lo, h.start), max(hi, h.end)
        else:
            regions.append([h])
            lo, hi = h.start, h.end
    out: list[str] = []
    conflicts: list[Conflict] = []
    at = 0
    shift = 0  # lines `ours` has gained before the current base position, for conflict line numbers
    for region in regions:
        start, end = min(h.start for h in region), max(h.end for h in region)
        out += b[at:start]
        mine = [h for h in region if h.side == "ours"]
        yours = [h for h in region if h.side == "theirs"]
        o_part, t_part = _apply(b, start, end, mine), _apply(b, start, end, yours)
        if not yours or not mine or o_part == t_part:
            out += t_part if not mine else o_part
        else:
            conflicts.append(Conflict(start + shift + 1, "".join(b[start:end]), "".join(o_part), "".join(t_part)))
            out += o_part
        shift += len(o_part) - (end - start)
        at = end
    out += b[at:]
    return Merged(None if conflicts else "".join(out), conflicts)


def contains(base: str, edited: str, text: str) -> bool:
    got = merge(base, text, edited)
    return got.clean and got.text == text
