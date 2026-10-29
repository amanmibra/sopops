"""An existing sop project as an import: its files (uploaded, or read from the workspace's GitHub repository) become a
proposal that shows what's in it, checked like any proposal (compiled with sop, each agent against its original prompt
when one is given), and applied as exactly those files."""

from __future__ import annotations

from .. import forms
from ..sop import Files, Sop
from .review import refresh_used_by
from .types import (
    Proposal,
    ProposedAgent,
    ProposedGroup,
    ProposedInstruction,
    ProposedProcedure,
    ProposedSettings,
    ProposedStep,
)

SKIP = ("build/",)  # sop regenerates it


def clean(files: Files) -> Files:
    """The project's files, without build output, with paths relative to the project (no leading ./ or /)."""
    out: Files = {}
    for path, text in files.items():
        p = path.strip().lstrip("./").lstrip("/")
        if p and not p.startswith(SKIP):
            out[p] = text
    return out


def steps(rows: list[dict]) -> list[ProposedStep]:
    def one(r: dict | str) -> dict:
        return {"text": r, "tool": None, "required": False} if isinstance(r, str) else {"text": r["text"], "tool": r.get("tool"), "required": bool(r.get("required"))}

    return [ProposedStep(**one(r), or_=[one(o) for o in (r.get("or") or [])] if isinstance(r, dict) else []) for r in rows]


def proposal_of(files: Files, sop: Sop, originals: dict[str, str] | None = None) -> Proposal:
    """What the project holds, as a proposal carrying the files themselves. Raises Invalid when there's no sop.yaml or
    sop can't read the project (sop's Invalid); ValueError without a sop.yaml."""
    files = clean(files)
    config = forms.config_path(files)
    if config is None:
        raise ValueError(f"no {forms.CONFIG}: upload the project's folder (the one with {forms.CONFIG} in it)")
    items = forms.from_export(sop.export(files), config)
    p = Proposal(
        instructions=[ProposedInstruction(id=i["id"], title=i["id"], text=i["text"]) for i in items["instructions"]],
        procedures=[ProposedProcedure(id=x["id"], name=x["name"], goal=x.get("goal") or "", when=x.get("when") or "", guidance=x.get("guidance") or "",
                                      steps=steps(x.get("steps") or []), never=steps(x.get("never") or []), warning_signs=steps(x.get("warning_signs") or []),
                                      delivery=x.get("delivery") or "prompt") for x in items["procedures"]],
        groups=[ProposedGroup(id=g["id"], blocks=list(g["blocks"])) for g in items["groups"]],
        agents=[ProposedAgent(id=a["id"], name=a["id"], platform=a["platform"], platform_id=a["platform_id"], context=a.get("context") or "",
                              blocks=list(a["blocks"]), variables=dict(a.get("variables") or {})) for a in items["agents"]],
        settings=ProposedSettings(variables=dict(items["settings"].get("variables") or {}), procedures_heading=items["settings"].get("procedures_heading") or forms.DEFAULT_HEADING),
        originals={k: v for k, v in (originals or {}).items() if v.strip()},
        files=files,
    )
    refresh_used_by(p)
    return p
