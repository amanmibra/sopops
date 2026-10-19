"""A proposal as sopc files, and the fidelity check: compile every proposed agent with sopc and compare its prompt with
the original, sentence by sentence (`sopc compare`), so dropped or changed text is flagged before anything is applied."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable

from .. import forms
from ..sopc import Files, Invalid, Issue, Sopc
from . import text as tx
from .types import AgentFidelity, Changed, Proposal, ProposalContent, ProposalIssue

Export = Callable[[Files], dict]


def item_data(content: ProposalContent) -> list[tuple[str, str, dict]]:
    """(kind, id, the fields the forms and `forms.write` use) for every block and agent."""
    out: list[tuple[str, str, dict]] = []
    for i in content.instructions:
        out.append(("instruction", i.id, {"text": i.text}))
    for p in content.procedures:
        out.append(("procedure", p.id, {
            "name": p.name, "goal": p.goal, "when": p.when, "guidance": p.guidance, "delivery": p.delivery,
            "steps": [s.model_dump() for s in p.steps], "never": [s.model_dump() for s in p.never],
            "warning_signs": [s.model_dump() for s in p.warning_signs],
        }))
    for a in content.agents:
        out.append(("agent", a.id, {"platform": a.platform, "platform_id": a.platform_id or a.id, "context": a.context, "blocks": list(a.blocks), "variables": dict(a.variables)}))
    return out


def config_data(content: ProposalContent) -> dict:
    return {
        "variables": dict(content.settings.variables),
        "procedures_heading": content.settings.procedures_heading or forms.DEFAULT_HEADING,
        "groups": {g.id: list(g.blocks) for g in content.groups},
    }


def files_of(content: ProposalContent, export: Export | None = None) -> tuple[Files, list[Issue]]:
    """The sopc files for a proposal, and the problems that kept some items from being written.

    With `export` (sopc's), each file is read back to check it holds exactly the values asked for, as the forms do.
    Without it (no sopc binary, e.g. the MCP server on a laptop), the files are written directly, every string quoted."""
    files: Files = {}
    issues: list[Issue] = []
    for kind, id, data in item_data(content):
        problems = forms.check(kind, id, data)
        if problems:
            issues += problems
            continue
        if export is None:
            files[forms.paths_of(kind, id)[0] if kind != "procedure" else f"procedures/{id}.yaml"] = _direct(kind, data)
            continue
        try:
            path, text, _ = forms.write(kind, id, data, None, export)
            files[path] = text
        except Invalid as e:
            issues += [forms.locate(i) for i in e.issues]
    cfg = config_data(content)
    for g in content.groups:
        issues += forms.check("group", g.id, {"blocks": g.blocks})
    issues += forms.check("settings", "settings", cfg)
    if export is None:
        files[forms.CONFIG] = forms.config_yaml(cfg, forms.Yaml(quoted=True))
    else:
        try:
            files[forms.CONFIG] = forms.write("config", "config", cfg, None, export)[1]
        except Invalid as e:
            issues += [forms.locate(i) for i in e.issues]
    return files, issues


def _direct(kind: str, data: dict) -> str:
    y = forms.Yaml(quoted=True)
    if kind == "instruction":
        return forms.instruction_md(forms.normalized("instruction", data), y)
    if kind == "procedure":
        return forms.procedure_yaml(data, y)
    return forms.agent_yaml(data, y)


def check(proposal: Proposal, sopc: Sopc) -> Proposal:
    """The proposal with `issues` (sopc's checks) and `fidelity` (each agent against its original) filled in. A proposal
    that is an imported sopc project (`files`) is checked as those files."""
    if proposal.files:
        return check_files(proposal, sopc)
    files, issues = files_of(proposal, sopc.export)
    out = proposal.model_copy(deep=True)
    out.fidelity = []
    names = {p.id: p.name for p in proposal.procedures}
    if any(i.severity == "error" for i in issues):
        out.issues = [ProposalIssue(**i.to_dict()) for i in issues]
        return out
    return _compare(out, files, issues, names, sopc)


def check_files(proposal: Proposal, sopc: Sopc) -> Proposal:
    """`check` for an imported sopc project: its own files, compiled as they are."""
    out = proposal.model_copy(deep=True)
    out.fidelity = []
    return _compare(out, dict(proposal.files), [], {p.id: p.name for p in proposal.procedures}, sopc)


def _compare(out: Proposal, files: Files, issues: list[Issue], names: dict[str, str], sopc: Sopc) -> Proposal:
    compiled = sopc.compile(files)
    issues = issues + [forms.locate(i, names) for i in compiled.issues]
    out.issues = [ProposalIssue(**i.to_dict()) for i in issues]
    if not compiled.valid:
        return out
    prompts = {a: c["prompt"] for a, c in compiled.agents.items()}
    originals = {a: t for a, t in out.originals.items() if a in prompts}
    report = {r["agent"]: r for r in sopc.compare(files, originals)["agents"]} if originals else {}
    for agent in [a.id for a in out.agents if a.id in originals]:
        r = report.get(agent) or {"ok": True, "score": 1.0, "sentences_total": 0, "lost": [], "changed": [], "reworded": [], "added": []}
        variables = {**out.settings.variables, **next((a.variables for a in out.agents if a.id == agent), {})}
        rules = [t for t in rule_texts(compiled.agents[agent], variables) if verbatim(t, prompts[agent])]  # the rules its prompt states (a procedure served by a tool isn't in it)
        loose = [t for t in rules if not verbatim(t, originals[agent])]
        out.fidelity.append(AgentFidelity(
            agent=agent, ok=r["ok"], score=round(float(r["score"]), 4), similarity=round(similarity(originals[agent], prompts[agent]), 4),
            sentences=r["sentences_total"], missing=[x["text"] for x in r["lost"]],
            changed=[Changed(original=x["original"], now=x["compiled"]) for x in r["changed"]],
            reworded=[x["original"] for x in r["reworded"]], added=[x["text"] for x in r["added"]],
            rules=len(rules), rules_verbatim=len(rules) - len(loose), not_verbatim=loose, compiled=prompts[agent],
        ))
    return out


def rule_texts(agent: dict, variables: dict[str, str]) -> list[str]:
    """Every procedure rule of a compiled agent (sopc's `blocks`), with its variables filled, in order."""
    def fill(text: str) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: variables.get(m.group(1), m.group(0)), text)

    return [fill(str(r.get("text") or "")).strip() for b in agent.get("blocks") or [] if b.get("kind") == "sop" for r in b.get("rules") or [] if str(r.get("text") or "").strip()]


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def verbatim(rule: str, original: str) -> bool:
    """The rule's text is in the original word for word (spacing and case aside; a closing period optional)."""
    return _flat(rule).rstrip(".") in _flat(original)


def similarity(a: str, b: str) -> float:
    wa, wb = tx.words(a), tx.words(b)
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio() if wa or wb else 1.0
