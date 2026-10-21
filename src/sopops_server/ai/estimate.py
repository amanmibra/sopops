"""What a team's own models would cost to check its calls, before it picks them.

Tokens per checked call, per role, times each model's list price. The tokens are the team's own average over its last
30 days once it has `MIN_CALLS` checked calls (`model_calls`), else a typical call: measured on sopops's demo calls and
scaled to real-call length, with half the calls getting the reviewer. Either way the answer is a range, because cost
moves with call length and with how many calls the reviewer looks at.

Prices come from sopops's own table and `SOPOPS_MODEL_PRICES` (`spend.price_of`), then LiteLLM's bundled price list,
read from its JSON file without importing LiteLLM (importing it can fetch a newer list over the network). A model with
no price is reported and left out of the total. Everything is list price, before caching or batch discounts.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ..errors import workspace_id
from ..orm.model_calls import ModelCall
from . import spend

ROLES = ("decision", "fast", "reasoning")
# A typical checked call: (input, output) tokens per role.
TYPICAL: dict[str, tuple[int, int]] = {"decision": (6000, 300), "fast": (500, 50), "reasoning": (3000, 200)}
# The range around it: short calls with few reviews, to long calls with many.
SPREAD_TYPICAL: dict[str, tuple[float, float]] = {"decision": (0.7, 1.5), "fast": (0.7, 1.5), "reasoning": (0.4, 1.6)}
SPREAD_MEASURED = (0.8, 1.25)
MIN_CALLS = 30
WINDOW = timedelta(days=30)
ROLE_OF = {"route": "decision", "check": "decision", "cluster": "fast", "review": "reasoning", "theme": "reasoning",
           "suggestion": "reasoning"}


@lru_cache(maxsize=1)
def _litellm_prices() -> dict[str, tuple[float, float]]:
    spec = importlib.util.find_spec("litellm")
    if spec is None or not spec.origin:
        return {}
    path = Path(spec.origin).parent / "model_prices_and_context_window_backup.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for name, info in raw.items():
        if isinstance(info, Mapping) and info.get("input_cost_per_token") is not None:
            out[name.lower()] = (float(info["input_cost_per_token"]) * 1e6, float(info.get("output_cost_per_token") or 0) * 1e6)
    return out


def price(model: str) -> tuple[float, float] | None:
    """(input $/M, output $/M): sopops's table first, then LiteLLM's list, by the full string or without its provider."""
    own = spend.price_of(model)
    if own:
        return own
    table = _litellm_prices()
    full = model.strip().lower()
    for name in (full, full.split("/", 1)[-1]):
        if name in table:
            return table[name]
    return None


def measured(engine: Engine, workspace: str, now: datetime | None = None) -> tuple[dict[str, tuple[float, float]], int] | None:
    """This team's average tokens per checked call by role over the window, and the number of calls, or None when it
    has fewer than MIN_CALLS."""
    since = (now or datetime.now(timezone.utc)) - WINDOW
    ws = workspace_id(workspace)
    with Session(engine) as db:
        calls = db.exec(select(func.count(func.distinct(ModelCall.session_id))).where(
            ModelCall.workspace_id == ws, ModelCall.created_at >= since, ModelCall.purpose.in_(("route", "check")))).one()
        if calls < MIN_CALLS:
            return None
        rows = db.exec(select(ModelCall.purpose, func.sum(ModelCall.input_tokens), func.sum(ModelCall.output_tokens)).where(
            ModelCall.workspace_id == ws, ModelCall.created_at >= since).group_by(ModelCall.purpose)).all()
    per = {r: [0.0, 0.0] for r in ROLES}
    for purpose, tin, tout in rows:
        role = ROLE_OF.get(purpose)
        if role:
            per[role][0] += (tin or 0) / calls
            per[role][1] += (tout or 0) / calls
    return {r: (v[0], v[1]) for r, v in per.items()}, calls


def estimate(models: Mapping[str, str], calls_per_month: int, profile: tuple[dict[str, tuple[float, float]], int] | None = None) -> dict[str, Any]:
    """The cost of checking calls with these models (`decision`, `fast`, `reasoning`), per call and per month."""
    tokens = profile[0] if profile else {r: (float(i), float(o)) for r, (i, o) in TYPICAL.items()}
    roles, unpriced = [], []
    low = high = 0.0
    for role in ROLES:
        model = models.get(role)
        tin, tout = tokens.get(role, (0.0, 0.0))
        p = price(model) if model else None
        row: dict[str, Any] = {"role": role, "model": model, "input_tokens": round(tin), "output_tokens": round(tout),
                               "input_price": p[0] if p else None, "output_price": p[1] if p else None, "low": None, "high": None}
        if model and p is None:
            unpriced.append(model)
        if p:
            mid = (tin * p[0] + tout * p[1]) / 1e6
            lo, hi = SPREAD_MEASURED if profile else SPREAD_TYPICAL[role]
            row["low"], row["high"] = mid * lo, mid * hi
            low += row["low"]
            high += row["high"]
        roles.append(row)
    calls_per_month = max(0, int(calls_per_month))
    return {
        "basis": "yours" if profile else "typical",
        "basis_calls": profile[1] if profile else None,
        "calls_per_month": calls_per_month,
        "per_call": {"low": low, "high": high},
        "per_month": {"low": low * calls_per_month, "high": high * calls_per_month},
        "roles": roles,
        "unpriced": unpriced,
    }
