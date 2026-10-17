"""A proposal as sopc files, and the fidelity check: compile every proposed agent with sopc and compare its prompt with
the original, sentence by sentence (`sopc compare`), so dropped or changed text is flagged before anything is applied."""

from __future__ import annotations

import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from .. import forms
from ..sopc import Files, Invalid, Issue, Sopc, SopcUnavailable
from . import text as tx
from .models import AgentFidelity, Changed, Proposal, ProposalContent, ProposalIssue

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
    """The proposal with `issues` (sopc's checks) and `fidelity` (each agent against its original) filled in."""
    files, issues = files_of(proposal, sopc.export)
    out = proposal.model_copy(deep=True)
    out.fidelity = []
    names = {p.id: p.name for p in proposal.procedures}
    if any(i.severity == "error" for i in issues):
        out.issues = [ProposalIssue(**i.to_dict()) for i in issues]
        return out
    compiled = sopc.compile(files)
    issues += [forms.locate(i, names) for i in compiled.issues]
    out.issues = [ProposalIssue(**i.to_dict()) for i in issues]
    if not compiled.valid:
        return out
    prompts = {a: c["prompt"] for a, c in compiled.agents.items()}
    originals = {a: t for a, t in proposal.originals.items() if a in prompts}
    report = compare(sopc, files, originals)
    for agent in [a.id for a in proposal.agents if a.id in originals]:
        r = report.get(agent) or {"total": 0, "missing": [], "changed": [], "reworded": [], "added": []}
        total = r["total"]
        lost = len(r["missing"]) + len(r["changed"])
        out.fidelity.append(AgentFidelity(
            agent=agent, ok=lost == 0, score=round((total - lost) / total, 4) if total else 1.0,
            similarity=round(similarity(originals[agent], prompts[agent]), 4), sentences=total,
            missing=r["missing"], changed=[Changed(original=o, now=n) for o, n in r["changed"]], reworded=r["reworded"], added=r["added"],
            compiled=prompts[agent],
        ))
    return out


def similarity(a: str, b: str) -> float:
    wa, wb = tx.words(a), tx.words(b)
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio() if wa or wb else 1.0


_HEAD = re.compile(r"^(\S+): \d+% of (\d+) sentences kept")
_NOW = re.compile(r"^\s+now: (.*?)(?:   \(.*\))?$")


def compare(sopc: Sopc, files: Files, originals: dict[str, str]) -> dict[str, dict]:
    """`sopc compare --originals` on the files: {agent: {total, missing, changed: [(original, now)], reworded, added}}."""
    if not originals:
        return {}
    with sopc._written(files) as (root, bad), tempfile.TemporaryDirectory(prefix="sopops-originals-") as tmp:
        if bad:
            raise Invalid(bad)
        for agent, text in originals.items():
            (Path(tmp) / f"{agent}.md").write_text(text)
        proc = sopc._run(root, "compare", "--originals", tmp, check=False)
    if proc.returncode not in (0, 1) or not _HEAD.search(proc.stdout.splitlines()[0] if proc.stdout else ""):
        raise SopcUnavailable(f"sopc compare failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:500]}")
    return parse_compare(proc.stdout)


def parse_compare(out: str) -> dict[str, dict]:
    report: dict[str, dict] = {}
    cur: dict | None = None
    pending: str | None = None
    for line in out.splitlines():
        head = _HEAD.match(line)
        if head:
            cur = report[head.group(1)] = {"total": int(head.group(2)), "missing": [], "changed": [], "reworded": [], "added": []}
            continue
        if cur is None:
            continue
        for marker, key in (("  - missing:  ", "missing"), ("  ~ reworded: ", "reworded"), ("  + added:    ", "added")):
            if line.startswith(marker):
                cur[key].append(line[len(marker):])
                break
        else:
            if line.startswith("  ! changed:  "):
                pending = line[len("  ! changed:  "):]
            elif pending is not None and (m := _NOW.match(line)):
                cur["changed"].append((pending, m.group(1)))
                pending = None
    return report
