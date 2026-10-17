"""The LLM layer: structured output natively, else JSON mode plus validation and one retry. No network."""

from types import SimpleNamespace

import litellm
import pytest
from pydantic import BaseModel

from sopops import llm


class Verdict(BaseModel):
    passed: bool
    reasons: list[str]


def reply(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    replies: list[str] = []

    def fake_completion(**kw):
        seen.append(kw)
        return reply(replies.pop(0))

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return SimpleNamespace(seen=seen, replies=replies)


def test_model_for_roles():
    assert llm.model_for("fast", {}) is None
    assert llm.model_for("fast", {"SOPOPS_MODEL_REASONING": "a/b"}) == "a/b"
    env = {"SOPOPS_MODEL_REASONING": "a/big", "SOPOPS_MODEL_FAST": "a/small"}
    assert llm.model_for("fast", env) == "a/small" and llm.model_for("reasoning", env) == "a/big"
    assert not llm.configured({}) and llm.configured({"SOPOPS_MODEL_FAST": "x/y"})


def test_native_schema(monkeypatch, calls):
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls.replies.append('{"passed": true, "reasons": ["ok"]}')
    out = llm.LiteLLM({"reasoning": "prov/model"}).complete("reasoning", [{"role": "user", "content": "hi"}], Verdict)
    assert out == Verdict(passed=True, reasons=["ok"])
    assert calls.seen[0]["response_format"] is Verdict
    assert calls.seen[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_json_mode_validates_and_retries_once(monkeypatch, calls):
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda model: ["response_format"])
    calls.replies += ['{"passed": "maybe"}', 'Sure:\n```json\n{"passed": false, "reasons": []}\n```']
    out = llm.LiteLLM({"fast": "prov/m"}).complete("fast", [{"role": "system", "content": "Judge."}, {"role": "user", "content": "x"}], Verdict)
    assert out == Verdict(passed=False, reasons=[])
    first, second = calls.seen
    assert first["response_format"] == {"type": "json_object"}
    assert "JSON schema" in first["messages"][0]["content"] and first["messages"][0]["content"].startswith("Judge.")
    assert second["messages"][-1]["role"] == "user" and "didn't match" in second["messages"][-1]["content"]


def test_gives_up_after_one_retry(monkeypatch, calls):
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: (_ for _ in ()).throw(Exception("unknown model")))
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda model: [])
    calls.replies += ["no json here", "still none"]
    with pytest.raises(llm.LLMError, match="after a retry"):
        llm.LiteLLM({"reasoning": "x/y"}).complete("reasoning", [{"role": "user", "content": "x"}], Verdict)
    assert "response_format" not in calls.seen[0]
    assert len(calls.seen) == 2


def test_not_configured(monkeypatch):
    monkeypatch.delenv("SOPOPS_MODEL_FAST", raising=False)
    monkeypatch.delenv("SOPOPS_MODEL_REASONING", raising=False)
    with pytest.raises(llm.NotConfigured):
        llm.LiteLLM().complete("fast", [], Verdict)


def test_complete_uses_injected_llm():
    class Fake:
        def complete(self, role, messages, schema):
            return schema(passed=role == "fast", reasons=[messages[0]["content"]])

    assert llm.complete("fast", [{"role": "user", "content": "q"}], Verdict, llm=Fake()) == Verdict(passed=True, reasons=["q"])
