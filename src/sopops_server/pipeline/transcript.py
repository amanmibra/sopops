"""How a session and a procedure are shown to a model, and how quotes are checked against the transcript."""

from __future__ import annotations

import json
import re
from typing import Any

from .procedures import Procedure, fill

ROLE_LABEL = {"agent": "AGENT", "user": "USER", "tool": "TOOL", "system": "SYSTEM"}


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(max(seconds, 0))
    return f"{s // 60:02d}:{s % 60:02d}"


def render_transcript(turns: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> str:
    lines = ["<transcript>"]
    for i, t in enumerate(turns):
        text = t.get("text") or t.get("name") or ""
        lines.append(f"[{i}] {clock(t.get('offset_s'))} {ROLE_LABEL.get(t.get('role'), 'SYSTEM')}: {text}")
    lines.append("</transcript>")
    if tool_calls:
        lines.append("<tool_calls>")
        for c in tool_calls:
            args = _compact(c.get("arguments"))
            res = _compact(c.get("result"))
            err = " (error)" if c.get("is_error") else ""
            lines.append(f"- {clock(c.get('offset_s'))} {c.get('name')}({args}) -> {res}{err}")
        lines.append("</tool_calls>")
    return "\n".join(lines)


def render_procedure(p: Procedure, variables: dict[str, str], with_rule_keys: bool = True) -> str:
    out = [f'<procedure id="{p.id}">', f"# {p.name}"]
    if p.goal:
        out.append(f"Goal: {fill(p.goal, variables)}")
    if p.when:
        out.append(f"When it applies: {fill(p.when, variables)}")
    if p.guidance:
        out.append(f"Guidance: {fill(p.guidance, variables)}")
    for kind, title in (("step", "Steps"), ("never", "Never"), ("warning", "Warning signs")):
        rules = [r for r in p.rules if r.kind == kind]
        if not rules:
            continue
        out.append(f"## {title}")
        for r in rules:
            tool = f" (tool: {r.tool}{', required' if r.required else ''})" if r.tool else ""
            label = f"{r.key}: " if with_rule_keys else f"{r.index}. "
            out.append(f"- {label}{fill(r.text, variables)}{tool}")
    out.append("</procedure>")
    return "\n".join(out)


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", s.lower()).strip()


def verify_evidence(evidence: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep each quote only where it's really in the transcript: fix the turn number if the quote is in another turn,
    and drop a quote found nowhere. Turn numbers out of range are dropped too."""
    texts = [_norm(t.get("text") or "") for t in turns]
    out = []
    for e in evidence:
        turn, quote = e.get("turn"), (e.get("quote") or "").strip()
        q = _norm(quote)
        if isinstance(turn, int) and 0 <= turn < len(turns) and (not q or q in texts[turn]):
            out.append({"turn": turn, "quote": quote, "source": e.get("source", "transcript")})
            continue
        found = next((i for i, t in enumerate(texts) if q and q in t), None)
        if found is not None:
            out.append({"turn": found, "quote": quote, "source": e.get("source", "transcript")})
        elif isinstance(turn, int) and 0 <= turn < len(turns) and q and _overlap(q, texts[turn]) >= 0.6:
            out.append({"turn": turn, "quote": quote, "source": e.get("source", "transcript"), "approximate": True})
    return out


def agent_quote(evidence: list[dict[str, Any]], turns: list[dict[str, Any]]) -> bool:
    """Some evidence quotes an agent turn exactly (not an approximate match)."""
    return any(
        not e.get("approximate") and (e.get("quote") or "").strip() and isinstance(e.get("turn"), int) and turns[e["turn"]].get("role") == "agent"
        for e in evidence
    )


def _overlap(q: str, text: str) -> float:
    words = q.split()
    have = set(text.split())
    return sum(w in have for w in words) / len(words) if words else 0.0


def _compact(v: Any, n: int = 300) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), default=str)
    return s if len(s) <= n else s[: n - 1] + "…"
