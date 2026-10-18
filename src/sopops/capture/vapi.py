"""Vapi server messages to sessions: `end-of-call-report` (a finished session), and the live `conversation-update`
(the whole conversation so far, each time) and `transcript` (one final utterance) messages, which stream into it.

Vapi keeps a call's messages as `{role, message, time, secondsFromStart}`, with roles `bot`, `user`, `system`,
`tool_calls` (`toolCalls: [{id, function: {name, arguments}}]`) and `tool_call_result` (`toolCallId`, `name`,
`result`). The system message is the prompt, not a turn, so it's left out. The call id is the session's id.
"""

from __future__ import annotations

from typing import Any

from ..schemas import SessionIn, SessionOpen

# What to turn on under the assistant's (or phone number's) Server Messages on Vapi.
SERVER_MESSAGES = ("assistant-request", "end-of-call-report", "conversation-update", "transcript")


def call_of(message: dict) -> dict:
    call = message.get("call")
    return call if isinstance(call, dict) else {}


def _tags(message: dict) -> dict:
    """The `sopops_release` and `sopops_agent` our assistant-request put on the call, wherever Vapi echoes them."""
    call = call_of(message)
    out: dict = {}
    for src in (
        (call.get("assistantOverrides") or {}).get("variableValues"),
        (call.get("assistantOverrides") or {}).get("metadata"),
        (message.get("assistant") or {}).get("metadata") if isinstance(message.get("assistant"), dict) else None,
        call.get("metadata"),
    ):
        if isinstance(src, dict):
            for k in ("sopops_release", "sopops_agent"):
                if src.get(k) is not None and k not in out:
                    out[k] = src[k]
    return out


def _agent(message: dict) -> str | None:
    tags = _tags(message)
    if isinstance(tags.get("sopops_agent"), str):
        return tags["sopops_agent"]
    call = call_of(message)
    assistant = message.get("assistant") if isinstance(message.get("assistant"), dict) else {}
    return call.get("assistantId") or assistant.get("id") or None


def _release(message: dict) -> int | None:
    try:
        return int(_tags(message)["sopops_release"])
    except (KeyError, TypeError, ValueError):
        return None


def convert(messages: list[Any]) -> tuple[list[dict], list[dict]]:
    """Vapi messages to sopops (turns, tool_calls). A tool call is also a `tool` turn, where it happened."""
    turns: list[dict] = []
    calls: dict[str, dict] = {}
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role, offset = m.get("role"), m.get("secondsFromStart")
        if role in ("bot", "assistant", "user"):
            text = m.get("message") or m.get("content") or ""
            if isinstance(text, str) and text.strip():
                turns.append({"role": role, "text": text, "offset_s": offset})
        elif role == "tool_calls":
            for i, tc in enumerate(m.get("toolCalls") or []):
                fn = tc.get("function") or {}
                name, args = fn.get("name") or tc.get("name") or "tool", fn.get("arguments")
                cid = tc.get("id") or f"{offset}:{i}"
                turns.append({"role": "tool", "text": f"{name}({args if args is not None else ''})", "name": name, "offset_s": offset})
                calls[cid] = {"name": name, "arguments": args, "call_id": cid, "offset_s": offset}
        elif role == "tool_call_result":
            cid = m.get("toolCallId")
            if cid in calls:
                calls[cid]["result"] = m.get("result")
            elif m.get("name"):
                calls[cid or f"result:{len(calls)}"] = {"name": m["name"], "result": m.get("result"), "call_id": cid, "offset_s": offset}
    return turns, list(calls.values())


def _messages(message: dict) -> list:
    artifact = message.get("artifact") if isinstance(message.get("artifact"), dict) else {}
    return artifact.get("messages") or message.get("messages") or []


def end_of_call(message: dict) -> SessionIn | None:
    """An `end-of-call-report` as a finished session, or None without a call id."""
    call = call_of(message)
    if not call.get("id"):
        return None
    turns, tool_calls = convert(_messages(message))
    meta = {k: v for k, v in {"ended_reason": message.get("endedReason"), "call_type": call.get("type")}.items() if v}
    return SessionIn(
        platform="vapi", external_id=str(call["id"]), channel="text" if call.get("type") == "webChat" else "voice", agent=_agent(message),
        release=_release(message), started_at=message.get("startedAt") or call.get("startedAt"), ended_at=message.get("endedAt") or call.get("endedAt"),
        turns=turns, tool_calls=tool_calls, metadata=meta,
    )


def opening(message: dict) -> SessionOpen | None:
    """The streamed session a live message belongs to."""
    call = call_of(message)
    if not call.get("id"):
        return None
    return SessionOpen(platform="vapi", external_id=str(call["id"]), agent=_agent(message), release=_release(message),
                       started_at=call.get("startedAt") or None, metadata={"source": "vapi-server-url"})


def conversation(message: dict) -> tuple[list[dict], list[dict]]:
    """A `conversation-update`: the whole conversation so far."""
    return convert(_messages(message))


def transcript_turn(message: dict) -> dict | None:
    """A final `transcript` message as one turn (partial ones are skipped)."""
    if message.get("transcriptType", "final") != "final" and "final" not in str(message.get("type")):
        return None
    text = message.get("transcript")
    if not isinstance(text, str) or not text.strip():
        return None
    return {"role": message.get("role") or "user", "text": text, "at": message.get("timestamp")}
