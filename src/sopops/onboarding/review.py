"""Changes a reviewer makes to a proposal: deciding drift, and keeping `used_by` in step with the agents' blocks."""

from __future__ import annotations

import re

from .models import Finding, Proposal, ProposalContent


class ReviewError(ValueError):
    pass


def refresh_used_by(content: ProposalContent) -> None:
    groups = {g.id: g.blocks for g in content.groups}

    def expand(blocks: list[str], seen: frozenset = frozenset()) -> list[str]:
        out: list[str] = []
        for b in blocks:
            if b in groups and b not in seen:
                out += expand(groups[b], seen | {b})
            else:
                out.append(b)
        return out

    users: dict[str, list[str]] = {}
    for a in content.agents:
        for b in expand(a.blocks):
            users.setdefault(b, [])
            if a.id not in users[b]:
                users[b].append(a.id)
    for item in [*content.instructions, *content.procedures]:
        item.used_by = users.get(item.id, [])


def _fill(text: str, name: str, value: str) -> str:
    return re.sub(r"\{\{\s*" + re.escape(name) + r"\s*\}\}", lambda _: value, text)


def resolve(proposal: Proposal, finding_id: str, choice: str) -> Proposal:
    """Decide a drift finding: `keep` leaves each agent's version; an agent id gives everyone that agent's version."""
    out = proposal.model_copy(deep=True)
    f: Finding | None = next((x for x in out.findings if x.id == finding_id), None)
    if f is None:
        raise ReviewError(f"no finding '{finding_id}'")
    if choice == "keep":
        f.resolved = "keep"
        return out
    chosen = next((v for v in f.variants if choice in v.agents), None)
    if chosen is None:
        raise ReviewError(f"'{choice}' isn't one of the agents in this finding")
    agents = {a.id: a for a in out.agents}
    if f.variable:
        value = agents[choice].variables.get(f.variable)
        if value is None:
            raise ReviewError(f"{choice} has no value for {{{{{f.variable}}}}}")
        for i in out.instructions:
            i.text = _fill(i.text, f.variable, value)
        for p in out.procedures:
            for field in ("name", "goal", "when", "guidance", "source"):
                setattr(p, field, _fill(getattr(p, field), f.variable, value))
            for s in [*p.steps, *p.never, *p.warning_signs]:
                s.text = _fill(s.text, f.variable, value)
        for a in out.agents:
            a.context = _fill(a.context, f.variable, value)
            a.variables.pop(f.variable, None)
    else:
        keep = chosen.block
        dropped = {v.block for v in f.variants if v.block and v.block != keep}
        for a in out.agents:
            a.blocks = list(dict.fromkeys(keep if b in dropped else b for b in a.blocks))
        for g in out.groups:
            g.blocks = list(dict.fromkeys(keep if b in dropped else b for b in g.blocks))
        out.instructions = [i for i in out.instructions if i.id not in dropped]
        out.procedures = [p for p in out.procedures if p.id not in dropped]
        out.findings = [x for x in out.findings if x.id == f.id or x.block not in dropped]
        f = next(x for x in out.findings if x.id == finding_id)
    f.resolved = choice
    refresh_used_by(out)
    return out
