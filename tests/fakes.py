"""Fake model replies for pattern clustering, assignment and themes, driven by the findings' reasons.

A finding whose reason contains `mode X` belongs to failure mode X. Clustering returns one mode per X seen at least
twice (named "Mode X"); assignment picks the mode named "Mode X" if the rule has one, else other; themes put steps,
nevers and warning signs in one theme each (reusing existing theme keys in order)."""

from __future__ import annotations

import re
from collections import defaultdict

from sopops.llm import text_of

PATTERN_SCHEMAS = ("ClusterReply", "AssignReply", "ThemesReply")
FINDING = re.compile(r"^- (f\d+) \[\w+\] (.*)$", re.M)


def pattern_reply(role, messages, schema):
    text = text_of(messages)
    name = schema.__name__
    if name == "ClusterReply":
        modes = defaultdict(list)
        for key, reason in FINDING.findall(text):
            m = re.search(r"mode (\w+)", reason)
            if m:
                modes[m.group(1)].append(key)
        return {"modes": [{"name": f"Mode {x}", "description": f"Findings of kind {x}.", "findings": ks} for x, ks in sorted(modes.items()) if len(ks) >= 2]}
    if name == "AssignReply":
        out = []
        for block in re.findall(r"<rule>(.*?)</rule>", text, re.S):
            modes = dict((label, key) for key, label in re.findall(r"^- (m\d+): Mode (\w+)\.", block, re.M))
            for key, reason in FINDING.findall(block):
                m = re.search(r"mode (\w+)", reason)
                out.append({"finding": key, "mode": modes.get(m.group(1), "other") if m else "other"})
        return {"assignments": out}
    if name == "ThemesReply":
        existing = re.findall(r"^- (t\d+): ", text, re.M)
        groups = defaultdict(list)
        for key, kind in re.findall(r"^- (r\d+): (Step|Never|Warning sign) \d+:", text, re.M):
            groups[kind].append(key)
        themes = []
        for i, (kind, keys) in enumerate(sorted(groups.items(), key=lambda kv: ["Step", "Never", "Warning sign"].index(kv[0]))):
            themes.append({"key": existing[i] if i < len(existing) else "new", "name": f"{kind} problems", "description": f"{kind} rules.", "patterns": keys})
        return {"themes": themes}
    raise AssertionError(f"not a pattern schema: {name}")
