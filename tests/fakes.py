"""Fake model replies for pattern clustering, assignment and themes, driven by the findings' reasons.

A finding whose reason contains `mode X` belongs to failure mode X. Clustering returns one mode per X seen at least
twice (named "Mode X"); assignment picks the mode named "Mode X" if the rule has one, else other; themes put steps,
nevers and warning signs in one theme each (reusing existing theme keys in order)."""

from __future__ import annotations

import re
from collections import defaultdict

from sopops.ai.signatures import ClusterFindings, GroupThemes


def mode_of(reason: str) -> str | None:
    m = re.search(r"mode (\w+)", reason)
    return m.group(1) if m else None


def pattern_reply(role, signature, inputs):
    """Clustering and themes (the reasoning model)."""
    if signature is ClusterFindings:
        modes = defaultdict(list)
        for f in inputs["findings"]:
            if mode_of(f.reason):
                modes[mode_of(f.reason)].append(f.key)
        return {"modes": [{"name": f"Mode {x}", "description": f"Findings of kind {x}.", "findings": ks} for x, ks in sorted(modes.items()) if len(ks) >= 2]}
    if signature is GroupThemes:
        existing = [t.key for t in inputs["existing_themes"]]
        groups = defaultdict(list)
        for p in inputs["patterns"]:
            groups[p.rule.split(" ")[0] if not p.rule.startswith("Warning sign") else "Warning sign"].append(p.key)
        order = ["Step", "Never", "Warning sign"]
        themes = []
        for i, (kind, keys) in enumerate(sorted(groups.items(), key=lambda kv: order.index(kv[0]))):
            themes.append({"key": existing[i] if i < len(existing) else "new", "name": f"{kind} problems", "description": f"{kind} rules.", "patterns": keys})
        return {"themes": themes}
    raise AssertionError(f"not a pattern signature: {signature.__name__}")


def pattern_answer(state, questions):
    """Assignment (a decision): the mode named "Mode X" for a finding whose reason says mode X, else other."""
    findings = state["inputs"]["findings"]
    out = {}
    for key, q in questions.items():
        x = mode_of(findings[key]["reason"])
        named = next((label for label, desc in q["criteria"].items() if desc and x and desc.startswith(f"Mode {x}.")), "other")
        out[key] = named
    return out
