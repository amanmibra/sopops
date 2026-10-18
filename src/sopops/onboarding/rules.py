"""Onboarding's extraction steps answered with plain rules instead of a model: an `AI` like `sopops.ai.lm.Models`.

For tests and for trying onboarding without an LLM key (`SOPOPS_MODEL_REASONING=rules`). It handles prompts written
the usual way (a heading per procedure, a "When ...:" line, numbered steps, "Never ..." and "If ..., transfer ..."
lines) and is much weaker than a model on anything else.
"""

from __future__ import annotations

import re
from typing import Any

import dspy

from ..ai.lm import RULES, Usage, as_predict
from ..ai.signatures import NameVariables, SegmentPrompt, StructureProcedure
from . import text as tx
from .pipeline import heuristic_names
from .types import ProcedureDraft, Segment, Segmentation, StepDraft

TRIGGER = re.compile(r"^(when|if|whenever)\b.*:\s*$", re.I)
NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
NEVER = re.compile(r"^\s*(?:[-*]\s+)?(never|don't|do not)\b", re.I)
ESCALATE = re.compile(r"\b(transfer|escalate|hand off|put .* through)\b", re.I)


class RulesModels:
    """Answers `SegmentPrompt`, `StructureProcedure` and `NameVariables` by rule; nothing else."""

    def __init__(self) -> None:
        self.usage = Usage()

    def model(self, role: str) -> str | None:
        return RULES if role in ("fast", "reasoning") else None

    def decision_native(self) -> bool:
        return False

    def predict(self, role: str, predictor: dspy.Predict | type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        sig = as_predict(predictor).signature
        data = {k: [x.model_dump() if hasattr(x, "model_dump") else x for x in v] if isinstance(v, list) else v for k, v in inputs.items()}
        if sig is SegmentPrompt:
            return dspy.Prediction(segments=segment(data).segments)
        if sig is StructureProcedure:
            return dspy.Prediction(procedure=procedure(data))
        if sig is NameVariables:
            return dspy.Prediction(names=heuristic_names([(s["text"], s["values"]) for s in data["slots"]], {}))
        raise ValueError(f"the rules mode can't answer {sig.__name__}")


def _procedural(text: str) -> bool:
    lines = [l for l in text.split("\n") if l.strip()]
    body = [l for l in lines if not tx.HEADING.match(l)]
    return bool(body) and (TRIGGER.match(body[0]) is not None or sum(bool(NUMBERED.match(l)) for l in body) >= 2)


def segment(data: dict) -> Segmentation:
    name = re.split(r"[^a-z0-9]+", data.get("agent", "").lower().replace("'", ""))
    first_word = name[0] if name else ""
    segs: list[Segment] = []
    for u in data["units"]:
        text: str = u["text"]
        first = text.strip().split("\n")[0]
        heading = tx.HEADING.match(first)
        title = re.sub(r"^#+\s*", "", first).strip() if heading else " ".join(first.split()[:3]).rstrip(".:,")
        if heading and not text.strip().count("\n"):
            kind = "heading"
        elif _procedural(text):
            kind = "procedure"
        elif first_word and first_word in text.lower().replace("'", "").replace("’", "") and not text.lower().startswith("you "):
            kind = "context"
        else:
            kind = "instruction"
        segs.append(Segment(units=[u["n"]], kind=kind, title=title or "Untitled"))
    return Segmentation(segments=segs)


def procedure(data: dict) -> ProcedureDraft:
    tools: list[str] = data.get("tools", [])
    name, when, guidance = data.get("title", "Procedure"), "", []
    steps, never, warnings = [], [], []

    def tool_of(line: str) -> str | None:
        return next((t for t in tools if re.search(rf"\b{re.escape(t)}\b", line)), None)

    for raw in data["passage"].split("\n"):
        line = raw.strip()
        if not line:
            continue
        if tx.HEADING.match(line):
            name = re.sub(r"^#+\s*", "", line)
        elif TRIGGER.match(line) and not when:
            when = re.sub(r"^(when|if|whenever)\s+", "", line, flags=re.I).rstrip(": ").strip()
            when = when[:1].upper() + when[1:] + ("" if when.endswith(".") else ".")
        elif NEVER.match(line):
            never.append(StepDraft(text=re.sub(r"^[-*]\s+", "", line), tool=tool_of(line)))
        elif line.lower().startswith("if ") and ESCALATE.search(line):
            warnings.append(StepDraft(text=line, tool=tool_of(line)))
        elif when or NUMBERED.match(raw):
            text = NUMBERED.sub("", raw).strip()
            text = re.sub(r"^[-*]\s+", "", text)
            tool = tool_of(text)
            steps.append(StepDraft(text=text, tool=tool, required=bool(tool and re.search(r"\b(always|before you confirm|every)\b", text, re.I))))
        else:
            guidance.append(line)
    return ProcedureDraft(name=name, goal="", when=when, guidance="\n".join(guidance), steps=steps, never=never, warning_signs=warnings)
