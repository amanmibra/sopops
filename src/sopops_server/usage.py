"""Model spend over a window: what `GET …/usage` and a session's cost read from `model_calls` (`sopops_server.ai.spend`).

- **Totals**: calls, input and output tokens, and estimated cost; `unpriced_calls` had no price (their tokens count,
  their cost doesn't).
- **By purpose** and **by model**, largest cost first.
- **Per session**: the cost of calls about a session (routing, checks, the reviewer, assigning its findings) divided
  by the sessions they were about. Work over many sessions (clustering, themes, suggestions, calibration) isn't in it.
- **By day**: UTC days from the window's start to its end, empty days included.

And calls over a period (`GET …/usage/calls`): sessions sopops received in full (posted whole, or streamed and closed;
open ones aren't, yet), by when they started, in `[start, end)`, with a count per UTC day.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, func, select

from .ai import lm as ai_lm
from .ai import spend
from .orm import ModelCall, SessionRecord

if TYPE_CHECKING:
    from .insights import Filters
    from .store import Store


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _row(calls: int, tin: int, tout: int, cost: float, unpriced: int) -> dict[str, Any]:
    return {"calls": calls, "input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost, 6), "unpriced_calls": unpriced}


class Usage:
    def __init__(self, store: Store):
        self.store = store

    def window(self, f: Filters) -> dict[str, Any]:
        M = ModelCall
        with Session(self.store.engine) as db:
            rows = db.exec(select(M.purpose, M.model, M.session_id, M.input_tokens, M.output_tokens, M.cost_usd, M.created_at)
                           .where(M.workspace_id == f.workspace, M.created_at >= f.start, M.created_at <= f.end)).all()
        total = [0, 0, 0, 0.0, 0]
        groups: dict[str, dict[str, list]] = {"purpose": defaultdict(lambda: [0, 0, 0, 0.0, 0]), "model": defaultdict(lambda: [0, 0, 0, 0.0, 0])}
        days: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0.0, 0])
        per_session: dict[str, float] = defaultdict(float)
        for purpose, model, sid, tin, tout, cost, at in rows:
            for acc in (total, groups["purpose"][purpose], groups["model"][model], days[_utc(at).date().isoformat()]):
                acc[0] += 1
                acc[1] += tin
                acc[2] += tout
                acc[3] += cost or 0.0
                acc[4] += cost is None
            if sid:
                per_session[sid] += cost or 0.0
        day, last = _utc(f.start).date(), _utc(f.end).date()
        series = []
        while day <= last:
            series.append({"day": day.isoformat(), **_row(*days.get(day.isoformat(), [0, 0, 0, 0.0, 0]))})
            day += timedelta(days=1)
        by = {k: sorted(({k: name, **_row(*acc)} for name, acc in g.items()), key=lambda x: (-x["cost_usd"], -x["calls"], x[k])) for k, g in groups.items()}
        n = len(per_session)
        return {"start": f.start, "end": f.end, "total": _row(*total), "by_purpose": by["purpose"], "by_model": by["model"],
                "sessions": n, "per_session_usd": round(sum(per_session.values()) / n, 6) if n else None, "daily": series}

    def calls(self, workspace: str, start: datetime, end: datetime) -> dict[str, Any]:
        """Sessions received in full that started in [start, end), in total and per UTC day (empty days included)."""
        S = SessionRecord
        start, end = _utc(start), _utc(end)
        with Session(self.store.engine) as db:
            starts = db.exec(select(S.started_at).where(S.workspace_id == workspace, S.status != "open", S.started_at >= start, S.started_at < end)).all()
        days: dict[str, int] = defaultdict(int)
        for at in starts:
            days[_utc(at).date().isoformat()] += 1
        day, last = start.date(), (end - timedelta(microseconds=1)).date()
        series = []
        while day <= last:
            series.append({"day": day, "calls": days.get(day.isoformat(), 0)})
            day += timedelta(days=1)
        return {"start": start, "end": end, "calls": len(starts), "daily": series}

    def session(self, workspace: str, session_id: str) -> dict[str, Any]:
        """{model_calls, cost_usd} of the calls about one session."""
        M = ModelCall
        with Session(self.store.engine) as db:
            calls, cost, unpriced = db.exec(select(func.count(M.id), func.sum(M.cost_usd), func.count(M.id) - func.count(M.cost_usd))
                                            .where(M.workspace_id == workspace, M.session_id == session_id)).one()
        return {"model_calls": calls or 0, "cost_usd": round(cost, 6) if cost is not None else (0.0 if calls else None)}


def price_table(ai: ai_lm.AI | None = None) -> dict[str, Any]:
    """The prices in use, and each configured role's model with the price it gets."""
    ai = ai or ai_lm.get_ai()
    rows = [{"model": m, "input_per_m": i, "output_per_m": o, "source": src} for m, (i, o, src) in sorted(spend.prices().items())]
    roles = []
    for role in (*ai_lm.ROLES, ai_lm.DECIDE):
        model = ai.model(role)
        p = spend.price_of(model)
        roles.append({"role": role, "model": model, "input_per_m": p[0] if p else None, "output_per_m": p[1] if p else None})
    return {"prices": rows, "roles": roles, "env": spend.PRICES_ENV}
