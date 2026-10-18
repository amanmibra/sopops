"""Calibrated decision thresholds, per workspace and program, so one customer's calibration never ships with sopops.

A program is `route` or `check:<procedure id>`; its params map a decision output to `{"threshold": x}` (what
`dspy.experimental.ReAnchor` fits, see `sopops.ai.programs`). Each param is stored with a hash of its question, and
`load` keeps only those whose question is unchanged, so an edited rule falls back to the defaults until it's
recalibrated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ..orm import Calibration


def question_hash(question: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(question, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def load(engine: Engine, workspace: str, program: str, current: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """The program's params; with `current` ({output: question hash}), only those whose question is unchanged."""
    with Session(engine) as db:
        row = db.exec(select(Calibration).where(Calibration.workspace_id == workspace, Calibration.program == program)).first()
    if row is None:
        return {}
    return {k: dict(v) for k, v in row.params.items() if current is None or row.questions.get(k) == current.get(k)}


def save(engine: Engine, workspace: str, program: str, params: Mapping[str, Mapping[str, Any]], questions: Mapping[str, str], model: str | None,
         report: Mapping[str, Any] | None = None) -> None:
    """Replace the program's params (only `threshold`, `cuts` and `weights` are kept)."""
    kept = {k: {p: v for p, v in cfg.items() if p in ("threshold", "cuts", "weights")} for k, cfg in params.items()}
    kept = {k: v for k, v in kept.items() if v}
    with Session(engine) as db:
        db.merge(Calibration(workspace_id=workspace, program=program, params=kept, questions={k: questions[k] for k in kept if k in questions},
                             model=model, report=dict(report or {}), created_at=datetime.now(timezone.utc)))
        db.commit()
