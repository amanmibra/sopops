import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from sopops_server.ai import lm
from sopops_server.ai.fakes import FakeModels
from sopops_server.ai.keys import PROVIDERS

AI = "/v1/workspaces/demo/ai"
ANTHROPIC_MODELS = "https://api.anthropic.com/v1/models"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    for p in PROVIDERS.values():
        if p.env:
            monkeypatch.delenv(p.env, raising=False)


@pytest.fixture
def jev_ok(monkeypatch):
    """A TypeSafe key passes if it's `ts-good...`; model test calls pass unless the model's name says `broken`."""
    tried: dict[str, list] = {"jev": [], "models": []}

    def predict(self, role, predictor, **inputs):
        tried["jev"].append(self.jev_api_key)
        if not (self.jev_api_key or "").startswith("ts-good"):
            raise lm.AIError("jev-1.13.0: AuthenticationError: invalid key")
        return None

    def client(self, role):
        model = self.model(role)

        def call(**kwargs):
            tried["models"].append((model, self.api_key, self.api_base))
            if "broken" in model:
                raise RuntimeError("NotFoundError: model not found")
            return ["ok"]

        return call

    monkeypatch.setattr(lm.Models, "predict", predict)
    monkeypatch.setattr(lm.Models, "client", client)
    return tried


def test_nothing_set_is_not_ready(client):
    got = client.get(AI).json()
    assert got["typesafe"] == {"source": None, "hint": None}
    assert got["fast"] == {"model": None, "source": None} and not got["ready"]
    assert [p["id"] for p in got["providers"]] == ["anthropic", "openai", "gemini", "other"]


def test_the_deployment_defaults_apply_until_a_team_sets_its_own(client, monkeypatch, jev_ok):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-deployment-key-0000")
    monkeypatch.setenv("SOPOPS_MODEL_FAST", "anthropic/claude-haiku-4-5")
    monkeypatch.setenv("SOPOPS_MODEL_REASONING", "anthropic/claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-deployment-1111")
    got = client.get(AI).json()
    assert got["ready"] and got["typesafe"]["source"] == "default"
    assert got["provider"] == {"id": "anthropic", "source": "default", "hint": None, "api_base": None}
    assert got["reasoning"] == {"model": "anthropic/claude-sonnet-5-5", "source": "default"}

    # the team's own key and models win, and the defaults stay for anything it didn't set
    assert client.put(f"{AI}/typesafe", json={"key": "ts-good-team-key-2222"}).json()["typesafe"] == {"source": "team", "hint": "…2222"}
    got = client.put(f"{AI}/models", json={"fast": "anthropic/claude-haiku-4-5", "reasoning": "anthropic/claude-opus-5-5"}).json()
    assert got["reasoning"] == {"model": "anthropic/claude-opus-5-5", "source": "team"}
    assert got["provider"]["source"] == "default"  # still the deployment's Anthropic key

    store = client.app.state.store
    ai = store.ai_keys.ai("demo")
    assert ai.model("reasoning") == "anthropic/claude-opus-5-5" and ai.jev_api_key == "ts-good-team-key-2222" and ai.api_key is None
    assert store.ai_keys.ai("other-team") is lm.get_ai()  # a team with no settings uses the defaults

    # back to the default
    got = client.delete(f"{AI}/typesafe").json()
    assert got["typesafe"]["source"] == "default" and store.ai_keys.ai("demo").jev_api_key == "ts-deployment-key-0000"


@respx.mock
def test_a_provider_key_lists_its_models_and_suggests_a_pair(client, jev_ok):
    route = respx.get(ANTHROPIC_MODELS).mock(return_value=httpx.Response(200, json={"data": [
        {"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-4-5"}, {"id": "claude-opus-5-5"}]}))
    got = client.put(f"{AI}/provider", json={"provider": "anthropic", "key": "sk-ant-team-key-3333"}).json()
    assert route.calls.last.request.headers["x-api-key"] == "sk-ant-team-key-3333"
    assert got["models"] == ["anthropic/claude-haiku-4-5", "anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5"]
    assert got["suggested"] == {"fast": "anthropic/claude-haiku-4-5", "reasoning": "anthropic/claude-sonnet-5-5"}

    client.put(f"{AI}/typesafe", json={"key": "ts-good-team-key-2222"})
    got = client.put(f"{AI}/models", json=got["suggested"]).json()
    assert got["ready"] and got["provider"] == {"id": "anthropic", "source": "team", "hint": "…3333", "api_base": None}
    assert ("anthropic/claude-haiku-4-5", "sk-ant-team-key-3333", None) in jev_ok["models"]  # tried with the team's key
    assert client.get(f"{AI}/models").json()["suggested"]["reasoning"] == "anthropic/claude-sonnet-5-5"
    assert "sk-ant" not in client.get(AI).text  # keys never come back


@respx.mock
def test_keys_and_models_that_dont_work_are_refused(client, jev_ok):
    respx.get(ANTHROPIC_MODELS).mock(return_value=httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    res = client.put(f"{AI}/provider", json={"provider": "anthropic", "key": "sk-ant-wrong-4444"})
    assert res.status_code == 422 and res.json()["detail"] == "Anthropic didn't accept this key."

    res = client.put(f"{AI}/typesafe", json={"key": "ts-bad-key-5555"})
    assert res.status_code == 422 and "TypeSafe didn't accept this key" in res.json()["detail"]

    res = client.put(f"{AI}/provider", json={"provider": "openai"})
    assert res.status_code == 422 and res.json()["detail"] == "Paste your OpenAI API key."

    res = client.put(f"{AI}/models", json={"fast": "anthropic/broken-model", "reasoning": "anthropic/claude-sonnet-5-5"})
    assert res.status_code == 422 and res.json()["detail"].startswith("anthropic/broken-model didn't answer")
    assert client.get(AI).json()["typesafe"]["source"] is None  # nothing was saved


def test_other_takes_any_litellm_model_with_a_base_url(client, jev_ok):
    got = client.put(f"{AI}/provider", json={"provider": "other", "key": "gsk-team-key-6666", "api_base": "https://llm.example.com/v1"}).json()
    assert got == {"models": [], "suggested": None}
    client.put(f"{AI}/models", json={"fast": "groq/llama-4-scout", "reasoning": "groq/llama-4-maverick"})
    assert ("groq/llama-4-scout", "gsk-team-key-6666", "https://llm.example.com/v1") in jev_ok["models"]
    ai = client.app.state.store.ai_keys.ai("demo")
    assert (ai.model("fast"), ai.api_key, ai.api_base) == ("groq/llama-4-scout", "gsk-team-key-6666", "https://llm.example.com/v1")


@respx.mock
def test_switching_providers_clears_the_teams_models(client, jev_ok):
    respx.get(ANTHROPIC_MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "claude-haiku-4-5"}]}))
    respx.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "gpt-4.1-mini"}, {"id": "gpt-4.1"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"}]}))
    client.put(f"{AI}/provider", json={"provider": "anthropic", "key": "sk-ant-team-key-3333"})
    client.put(f"{AI}/models", json={"fast": "anthropic/claude-haiku-4-5", "reasoning": "anthropic/claude-haiku-4-5"})
    got = client.put(f"{AI}/provider", json={"provider": "openai", "key": "sk-openai-team-7777"}).json()
    assert got == {"models": ["openai/gpt-4.1", "openai/gpt-4.1-mini"], "suggested": {"fast": "openai/gpt-4.1-mini", "reasoning": "openai/gpt-4.1"}}
    assert client.get(AI).json()["fast"] == {"model": None, "source": None}


def test_a_replaced_implementation_serves_every_team(client, jev_ok):
    client.put(f"{AI}/typesafe", json={"key": "ts-good-team-key-2222"})
    fake = FakeModels()
    lm.set_ai(fake)
    assert client.app.state.store.ai_keys.ai("demo") is fake


def test_without_a_secret_key_nothing_is_stored(client, monkeypatch, jev_ok):
    monkeypatch.delenv("SOPOPS_SECRET_KEY")
    res = client.put(f"{AI}/typesafe", json={"key": "ts-good-team-key-2222"})
    assert res.status_code == 503 and "SOPOPS_SECRET_KEY" in res.json()["detail"]
