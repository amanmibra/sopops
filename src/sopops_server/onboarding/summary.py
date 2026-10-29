"""The review of an import as summaries, so it reads the same at 5 agents or 100: one line of counts, then the
disagreements, the variables, which agents use or miss each proposed block, each agent's unique text, and fidelity.

Groups come from `sop overlap --json` on the original prompts (shared text, near-copies, variables, text most agents
have but some lack, text only one agent has), with stable 12-hex ids, so a reviewer's decisions (`Proposal.decisions`,
keyed by those ids) survive a re-run. Drift the extraction proposed that overlap doesn't find (a paraphrased conflict)
is listed as suggested by the model.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from . import text as tx
from .review import ReviewError, refresh_used_by, resolve
from .types import Proposal, ProposedInstruction

DECIDE = ("all", "keep")  # a disagreement: one wording for every agent, or each keeps its own
UNIQUE = ("context", "block")  # unique text: stays in the agent's own context, or becomes a block


def unique_id(agent: str, text: str) -> str:
    return hashlib.sha256(f"unique\0{agent}\0{tx.norm(text)}".encode()).hexdigest()[:12]


def _variant_agents(group: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """A near-copy's wordings, the most agents first."""
    return sorted(group["agents_by_variant"].items(), key=lambda kv: (-len(kv[1]), kv[0]))


def disagreements(proposal: Proposal, overlap: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule-word drift from sop overlap (majority wording against the outliers, the smallest minority first), then
    drift the extraction found that overlap doesn't (suggested by the model)."""
    out = []
    seen_texts: set[str] = set()
    for g in overlap.get("near_copies", []):
        if g["kind"] != "rule_word":
            continue
        variants = _variant_agents(g)
        seen_texts |= {tx.norm(t) for t, _ in variants}
        majority, outliers = variants[0], variants[1:]
        out.append({
            "id": g["id"], "source": "overlap",
            "majority": {"text": majority[0], "agents": majority[1]},
            "outliers": [{"text": t, "agents": a} for t, a in outliers],
            "minority": sum(len(a) for _, a in outliers),
            "words": g.get("differing_tokens", {}),
            "decision": proposal.decisions.get(g["id"]),
        })
    for f in proposal.findings:
        if f.kind != "drift" or any(tx.norm(v.text) in seen_texts or any(tx.norm(v.text) in s or s in tx.norm(v.text) for s in seen_texts) for v in f.variants):
            continue
        variants = sorted(f.variants, key=lambda v: -len(v.agents))
        if not variants:
            continue
        out.append({
            "id": f.id, "source": "model",
            "majority": {"text": variants[0].text, "agents": variants[0].agents},
            "outliers": [{"text": v.text, "agents": v.agents} for v in variants[1:]],
            "minority": sum(len(v.agents) for v in variants[1:]),
            "words": {}, "decision": proposal.decisions.get(f.id) or f.resolved and ("keep" if f.resolved == "keep" else "all"),
        })
    return sorted(out, key=lambda d: (d["source"] != "overlap", d["minority"], d["id"]))


def variables(proposal: Proposal, overlap: dict[str, Any]) -> list[dict[str, Any]]:
    """Every variable × agent: the proposal's (named by the model), then overlap's that the proposal doesn't have
    (named by sop's heuristic, a default to rename)."""
    names = list(dict.fromkeys([*proposal.settings.variables, *(n for a in proposal.agents for n in a.variables)]))
    rows = [{"name": n, "values": {a.id: a.variables.get(n, proposal.settings.variables.get(n)) for a in proposal.agents}, "source": "proposal", "id": None} for n in names]
    have = {tuple(sorted((k, tx.norm(v or "")) for k, v in r["values"].items() if v)) for r in rows}
    for v in overlap.get("variables", []):
        key = tuple(sorted((k, tx.norm(x)) for k, x in v["values"].items()))
        if key in have:
            continue
        rows.append({"name": v["suggested_name"], "values": dict(v["values"]), "source": "overlap", "id": v["id"]})
    return rows


def grid(proposal: Proposal, overlap: dict[str, Any]) -> list[dict[str, Any]]:
    """Each proposed block with the agents that use it and the agents that miss text most agents have in it (from
    overlap's `missing`: text present in most prompts, mapped to the block that holds it)."""
    blocks = [("instruction", i.id, i.title or i.id, i.text, i.used_by) for i in proposal.instructions]
    blocks += [("procedure", p.id, p.name, p.source or "", p.used_by) for p in proposal.procedures]
    missing: dict[str, set[str]] = {}
    for m in overlap.get("missing", []):
        needle = tx.norm(m["text"])
        owner = next((bid for _, bid, _, text, _ in blocks if needle and needle in tx.norm(text)), None)
        if owner:
            missing.setdefault(owner, set()).update(m["missing_in"])
    return [{"kind": k, "id": bid, "title": title, "agents": list(used), "missing": sorted(missing.get(bid, set()) - set(used))} for k, bid, title, _, used in blocks]


def unique(proposal: Proposal, overlap: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for u in overlap.get("unique", []):
        for span in u["spans"]:
            text = span["text"].strip()
            if not text:
                continue
            sid = unique_id(u["agent"], text)
            out.append({"id": sid, "agent": u["agent"], "text": text, "decision": proposal.decisions.get(sid, "context")})
    return out


def summary(proposal: Proposal, overlap: dict[str, Any]) -> dict[str, Any]:
    dis = disagreements(proposal, overlap)
    var = variables(proposal, overlap)
    blocks = grid(proposal, overlap)
    uniq = unique(proposal, overlap)
    fidelity = [f.model_dump(exclude={"compiled"}) for f in proposal.fidelity]
    counts = {
        "agents": len(proposal.agents),
        "shared_blocks": sum(1 for b in blocks if len(b["agents"]) > 1),
        "variables": len(var),
        "disagreements": len(dis),
        "undecided": sum(1 for d in dis if not d["decision"]),
        "agents_with_unique": len({u["agent"] for u in uniq}),
        "below_full_fidelity": sum(1 for f in proposal.fidelity if not f.ok or f.score < 1.0),
    }
    return {"counts": counts, "disagreements": dis, "variables": var, "blocks": blocks, "unique": uniq, "fidelity": fidelity}


# --- decisions ------------------------------------------------------------------------------------------------------


def decide(proposal: Proposal, overlap: dict[str, Any], group: str, choice: str) -> Proposal:
    """Record and apply a decision on a group by its id.

    - A disagreement: `keep` (each agent keeps its wording) or `all:<agent>` (every agent gets that agent's wording).
      When the extraction made a drift finding for it, the finding is resolved the same way.
    - Unique text: `context` (stays in the agent's own text) or `block` (a new shared instruction the agent lists).
    """
    out = proposal.model_copy(deep=True)
    dis = {d["id"]: d for d in disagreements(out, overlap)}
    uniq = {u["id"]: u for u in unique(out, overlap)}
    if group in dis:
        d = dis[group]
        kind, _, agent = choice.partition(":")
        if kind not in DECIDE or (kind == "all" and agent not in [a for v in [d["majority"], *d["outliers"]] for a in v["agents"]]):
            raise ReviewError("choose keep, or all:<agent> for one of this disagreement's agents")
        finding = _finding_for(out, d)
        if finding is not None:
            out = resolve(out, finding, "keep" if kind == "keep" else agent)
        out.decisions[group] = choice
        return out
    if group in uniq:
        if choice not in UNIQUE:
            raise ReviewError("choose context or block")
        u = uniq[group]
        if choice == "block" and out.decisions.get(group) != "block":
            _make_block(out, u["agent"], u["text"], group)
        out.decisions[group] = choice
        return out
    raise ReviewError(f"no group '{group}' in this import's review")


def _finding_for(proposal: Proposal, d: dict[str, Any]) -> str | None:
    if d["source"] == "model":
        return d["id"]
    texts = {tx.norm(v["text"]) for v in [d["majority"], *d["outliers"]]}
    for f in proposal.findings:
        if f.kind == "drift" and any(tx.norm(v.text) in texts or any(t in tx.norm(v.text) for t in texts) for v in f.variants):
            return f.id
    return None


def _make_block(proposal: Proposal, agent: str, text: str, group: str) -> None:
    ids = {i.id for i in proposal.instructions} | {p.id for p in proposal.procedures} | {g.id for g in proposal.groups}
    base = tx.slug(" ".join(text.split()[:5]), "instruction")
    bid = tx.unique(base, ids)
    proposal.instructions.append(ProposedInstruction(id=bid, title=" ".join(text.split()[:5]), text=text, used_by=[agent]))
    for a in proposal.agents:
        if a.id == agent:
            a.context = re.sub(r"\n{3,}", "\n\n", a.context.replace(text, "")).strip()
            a.blocks = [*a.blocks, bid]
    refresh_used_by(proposal)


def _rename(text: str, old: str, new: str) -> str:
    return re.sub(r"\{\{\s*" + re.escape(old) + r"\s*\}\}", "{{" + new + "}}", text)


def edit_variable(proposal: Proposal, name: str, new_name: str | None = None, values: dict[str, str] | None = None) -> Proposal:
    """Rename a variable everywhere it's used, and/or set agents' values for it."""
    out = proposal.model_copy(deep=True)
    if new_name and new_name != name:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", new_name):
            raise ReviewError("a variable name is snake_case: lowercase letters, digits and underscores, starting with a letter")
        for i in out.instructions:
            i.text = _rename(i.text, name, new_name)
        for p in out.procedures:
            for f in ("name", "goal", "when", "guidance", "source"):
                setattr(p, f, _rename(getattr(p, f), name, new_name))
            for st in [*p.steps, *p.never, *p.warning_signs]:
                st.text = _rename(st.text, name, new_name)
        for a in out.agents:
            a.context = _rename(a.context, name, new_name)
            if name in a.variables:
                a.variables[new_name] = a.variables.pop(name)
        if name in out.settings.variables:
            out.settings.variables[new_name] = out.settings.variables.pop(name)
        name = new_name
    agents = {a.id: a for a in out.agents}
    for agent, value in (values or {}).items():
        if agent not in agents:
            raise ReviewError(f"no agent '{agent}' in this import")
        agents[agent].variables[name] = value
    return out
