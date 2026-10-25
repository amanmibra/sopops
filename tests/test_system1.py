"""Decisions through the model gateway's system-1 endpoint, against a fake gateway (respx): the same decisions and
calibrated thresholds as a System One client called directly, spend recorded under the backend model, the decision
model answering when the gateway's classifier is unavailable, and which classifier wins when several are set."""

import asyncio
import json

import dspy
import httpx
import pytest
import respx
from conftest import SOPC_BIN, WS, fresh_db_url
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_checks import ALLERGEN, checks_by_id, rules_by_key, session
from test_decisions import Decisions, Generative

from sopops_server import Sopc, Store, create_app
from sopops_server.ai import decide, lm, spend, system1
from sopops_server.ai.fakes import FakeEngine, FakeModels, FakeSystemOne
from sopops_server.ai.signatures import KeyCheck, decision_predict
from sopops_server.jobs import Jobs
from sopops_server.orm import ModelCall

GW = "http://gw.test:4001/v1"
ENDPOINT = f"{GW}/system1"


def gateway_env(monkeypatch, **extra):
    monkeypatch.setenv(system1.URL_ENV, GW)
    monkeypatch.setenv("SOPOPS_LM_KEY", "sk-gateway-key")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


class FakeGateway:
    """The gateway's system-1 endpoint: answers like a System One backend (`FakeSystemOne`'s shapes), as `backend`."""

    def __init__(self, responder, backend="jev", backend_model="jev-1.13.0", calibrated=True, status=200):
        self.one = FakeSystemOne(responder, model=backend_model)
        self.backend, self.backend_model, self.calibrated, self.status = backend, backend_model, calibrated, status
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "auth": request.headers.get("authorization")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "classifier down"}})
        answers = self.one.responder(body["state"], body["questions"])
        out = {}
        for name, q in body["questions"].items():
            v = answers[name]
            if q["type"] == "noul":
                out[name] = {"noul": float(v)}
            else:
                probs = {str(v): 1.0} if isinstance(v, str) else {str(k): float(p) for k, p in v.items()}
                full = {label: probs.get(label, 0.0) for label in q["criteria"]}
                out[name] = {"choice": max(full, key=full.get), "probabilities": full, "confidence": max(full.values())}
        return httpx.Response(200, json={"model": body["model"], "backend": self.backend, "backend_model": self.backend_model,
                                         "calibrated": self.calibrated, "answers": out, "usage": {"prompt_tokens": 1000, "completion_tokens": 0}})


def gateway_models(models=None, **kw):
    return lm.Models(models or {"fast": "fake/fast", "reasoning": "fake/reasoning"}, cache=False, **kw)


# --- the client ------------------------------------------------------------------------------------------------------


def test_without_the_url_nothing_changes(monkeypatch):
    assert system1.gateway({}) is None
    assert gateway_models().model(lm.DECIDE) == "fake/fast" and not gateway_models().decision_native()
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-deployment")
    assert gateway_models().model(lm.DECIDE) == lm.JEV_MODEL  # Jev directly, as before


def test_the_gateway_is_read_from_the_environment():
    gw = system1.gateway({system1.URL_ENV: "127.0.0.1:4001", "SOPOPS_LM_KEY": "k"})
    assert gw == system1.Gateway("http://127.0.0.1:4001/v1", "k", "sopops-system1") and gw.endpoint == "http://127.0.0.1:4001/v1/system1"
    assert system1.gateway({system1.URL_ENV: GW + "/", system1.KEY_ENV: "own", system1.MODEL_ENV: "sopops-system1-route", "SOPOPS_LM_KEY": "k"}) == \
        system1.Gateway(GW, "own", "sopops-system1-route")
    assert system1.gateway({system1.URL_ENV: GW, system1.FLAG_ENV: "off"}) is None


def test_the_gateway_wins_over_jev_directly_except_for_a_teams_own_key(monkeypatch):
    gateway_env(monkeypatch, TYPESAFE_API_KEY="ts-deployment")
    ai = gateway_models()
    assert ai.model(lm.DECIDE) == "sopops-system1" and ai.decision_native() and not ai.jev
    assert isinstance(ai.client(lm.DECIDE), system1.GatewaySystemOne)
    team = gateway_models(jev=True, jev_api_key="ts-team", system1_gateway=False)
    assert team.model(lm.DECIDE) == lm.JEV_MODEL and isinstance(team.client(lm.DECIDE), dspy.experimental.TypeSafe)


@respx.mock
def test_a_decision_is_decoded_with_the_same_thresholds(monkeypatch):
    gateway_env(monkeypatch)
    fake = FakeGateway(lambda state, qs: {k: 0.95 for k in qs})
    respx.post(ENDPOINT).mock(side_effect=fake)
    ai = gateway_models()
    q = {"instructions": "Is the sentence about the weather?", "threshold": 0.5}
    assert ai.predict(lm.DECIDE, decision_predict(KeyCheck, {"answer": q}), sentence="It's raining.").answer.value is True
    calibrated = decision_predict(KeyCheck, {"answer": q}, {"answer": {"threshold": 0.99}})
    got = ai.predict(lm.DECIDE, calibrated, sentence="It's raining.").answer
    assert got.value is False and got.probability == pytest.approx(0.95)  # 0.95 < the calibrated 0.99
    body = fake.requests[0]
    assert body["auth"] == "Bearer sk-gateway-key" and body["body"]["model"] == "sopops-system1"
    assert body["body"]["questions"]["answer"]["type"] == "noul" and "threshold" not in json.dumps(body["body"])  # thresholds stay here
    assert body["body"]["state"]["inputs"]["sentence"] == "It's raining."
    assert ai.usage.by_role["jev"]["calls"] == 2 and ai.usage.by_role["jev"]["input_tokens"] == 2000


@respx.mock
def test_choices_keep_their_probabilities(monkeypatch):
    gateway_env(monkeypatch)
    respx.post(ENDPOINT).mock(side_effect=FakeGateway(lambda s, qs: {k: {"m1": 0.7, "m2": 0.2, "other": 0.1} if k == "f1" else {"m2": 0.5, "other": 0.5} for k in qs}))
    group = decide.AssignGroup(rule="Step 1: ask", modes={"m1": "A", "m2": "B"}, findings={"f1": {"reason": "x"}, "f2": {"reason": "y"}})
    assert decide.assign(gateway_models(), [group]) == {"f1": "m1", "f2": "m2"}


@respx.mock
def test_spend_is_recorded_under_the_backend_model(tmp_path, monkeypatch):
    gateway_env(monkeypatch)
    respx.post(ENDPOINT).mock(side_effect=FakeGateway(lambda s, qs: {k: 0.9 for k in qs}))
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    q = {"instructions": "Is the sentence about the weather?", "threshold": 0.5}
    with spend.scope(store.engine, "demo", "check", "s-1"):
        gateway_models().predict(lm.DECIDE, decision_predict(KeyCheck, {"answer": q}), sentence="Sunny.")
    with Session(store.engine) as db:
        rows = db.exec(select(ModelCall)).all()
    assert [(r.model, r.purpose, r.input_tokens, r.output_tokens) for r in rows] == [("jev-1.13.0", "check", 1000, 0)]
    assert rows[0].cost_usd == pytest.approx(spend.cost("jev-1.13.0", 1000, 0))  # priced as Jev directly


def fallback_lm(probability):
    engine = FakeEngine(lambda outputs, text: {n: {"noul": probability} for n in outputs})
    return dspy.LM("fake/decision", engine=engine, cache=False), engine


@pytest.mark.parametrize("failure", [httpx.Response(503, json={"error": "down"}), httpx.Response(429), httpx.ConnectError("refused"),
                                     httpx.ReadTimeout("slow"), httpx.Response(200, json={"answers": {}})])
def test_an_unavailable_classifier_falls_through_to_the_decision_model(monkeypatch, failure):
    gateway_env(monkeypatch)
    ai = gateway_models({"fast": "fake/fast", "reasoning": "fake/reasoning", lm.DECIDE: "fake/decision"})
    ai._clients["fake/decision"], engine = fallback_lm(0.8)
    q = {"instructions": "Is the sentence about the weather?", "threshold": 0.5}
    with respx.mock:
        route = respx.post(ENDPOINT)
        route.mock(side_effect=failure) if isinstance(failure, Exception) else route.mock(return_value=failure)
        got = ai.predict(lm.DECIDE, decision_predict(KeyCheck, {"answer": q}, {"answer": {"threshold": 0.7}}), sentence="Sunny.").answer
    assert got.value is True and got.probability == pytest.approx(0.8) and engine.requests  # the decision model, same threshold
    assert ai.usage.by_role["decide"]["calls"] == 1 and ai.usage.by_role["jev"]["calls"] == 1  # the failed try, then the model


def test_without_a_decision_model_an_unavailable_classifier_is_an_error(monkeypatch):
    gateway_env(monkeypatch)
    ai = lm.Models({}, cache=False)
    with respx.mock:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(502))
        with pytest.raises(lm.AIError, match="Unavailable"):
            ai.predict(lm.DECIDE, decision_predict(KeyCheck, {"answer": {"instructions": "Weather?", "threshold": 0.5}}), sentence="x")


def test_a_bad_request_is_an_error_not_a_fall_through(monkeypatch):
    gateway_env(monkeypatch)
    ai = gateway_models()
    ai._clients["fake/fast"], engine = fallback_lm(0.8)
    with respx.mock:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(400, json={"error": "no questions"}))
        with pytest.raises(lm.AIError, match="refused"):
            ai.predict(lm.DECIDE, decision_predict(KeyCheck, {"answer": {"instructions": "Weather?", "threshold": 0.5}}), sentence="x")
    assert not engine.requests


def test_an_uncalibrated_answer_is_used_but_never_cached(monkeypatch):
    gateway_env(monkeypatch)
    fake = FakeGateway(lambda s, qs: {k: 0.9 for k in qs}, backend="llm", backend_model="sopops-decision", calibrated=False)
    client = system1.GatewaySystemOne(system1.gateway(), cache=True)
    q = {"answer": {"instructions": "Weather?", "type": "noul"}}
    state = {"instructions": "x", "input_fields": "", "inputs": {"sentence": f"uncached {id(fake)}"}}
    with respx.mock:
        respx.post(ENDPOINT).mock(side_effect=fake)
        assert client(state, q) == {"answer": {"noul": 0.9}}
        client(state, q)
    assert len(fake.requests) == 2 and client.history[-1]["model"] == "sopops-decision"


def test_the_async_path_answers_too(monkeypatch):
    gateway_env(monkeypatch)
    client = system1.GatewaySystemOne(system1.gateway(), cache=False)
    with respx.mock:
        respx.post(ENDPOINT).mock(side_effect=FakeGateway(lambda s, qs: {k: 0.3 for k in qs}))
        got = asyncio.run(client.acall({"inputs": {}}, {"a": {"instructions": "?", "type": "noul"}}))
        respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
        with pytest.raises(system1.Unavailable):
            asyncio.run(client.acall({"inputs": {"n": 2}}, {"a": {"instructions": "?", "type": "noul"}}))
    assert got == {"a": {"noul": 0.3}}


# --- whole checks -----------------------------------------------------------------------------------------------------


def run_check(tmp_path, files, decide_with):
    """One session through the app: decisions answered by `decide_with` (an AI's predict), the rest by the fakes."""
    tmp_path.mkdir(exist_ok=True)
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    fake = FakeModels(Generative(), FakeSystemOne(Decisions({})))
    fake.model = lambda role: decide_with.model(role) if role == lm.DECIDE else FakeModels.model(fake, role)

    def predict(role, predictor, **inputs):
        return decide_with.predict(role, predictor, **inputs) if role == lm.DECIDE else FakeModels.predict(fake, role, predictor, **inputs)

    fake.predict = predict
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]
    sid = client.post(f"{WS}/sessions", json=session(), headers={"Authorization": f"Bearer {token}"}).json()["id"]
    return checks_by_id(client.get(f"{WS}/sessions/{sid}").json())[ALLERGEN]


def test_a_check_through_the_gateway_matches_one_on_jev_directly(tmp_path, files, monkeypatch):
    answers = Decisions({ALLERGEN: 0.95}, {"allergen-free": 0.5})  # one never rule unsure: it goes to review

    class Direct:
        def __init__(self):
            self.inner = FakeModels(None, FakeSystemOne(answers))

        def model(self, role):
            return "jev-1.13.0"

        def predict(self, role, predictor, **inputs):
            return self.inner.predict(role, predictor, **inputs)

    direct = run_check(tmp_path / "a", files, Direct())
    gateway_env(monkeypatch)
    with respx.mock:
        fake = FakeGateway(answers)
        respx.post(ENDPOINT).mock(side_effect=fake)
        via = run_check(tmp_path / "b", files, gateway_models())
    status = lambda a: {k: r["status"] for k, r in rules_by_key(a).items()}  # noqa: E731
    assert status(via) == status(direct) and via["applicable"] == direct["applicable"]
    assert status(via)["never_1"] == "ok" and "reviewed" in rules_by_key(via)["never_1"]["reason"]  # unsure: reviewed, as on Jev
    assert via["model"].startswith("sopops-system1") and fake.requests


# --- settings -----------------------------------------------------------------------------------------------------------


def test_the_gateways_classifier_counts_as_the_deployments_jev(client, monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SOPOPS_MODEL_FAST", "anthropic/claude-haiku-4-5")
    monkeypatch.setenv("SOPOPS_MODEL_REASONING", "anthropic/claude-sonnet-5-5")
    got = client.get("/v1/workspaces/demo/ai").json()
    assert got["typesafe"]["source"] is None
    gateway_env(monkeypatch)
    got = client.get("/v1/workspaces/demo/ai").json()
    assert got["typesafe"]["source"] == "default" and got["ready"]
    est = client.get("/v1/workspaces/demo/ai/estimate", params={"fast": "anthropic/claude-haiku-4-5", "reasoning": "anthropic/claude-sonnet-5-5"}).json()
    assert {r["role"]: r["model"] for r in est["roles"]}["decision"].startswith("jev")


def test_a_teams_own_typesafe_key_still_goes_to_typesafe_directly(client, monkeypatch):
    from sopops_server.runtime import crypto

    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SOPOPS_MODEL_FAST", "anthropic/claude-haiku-4-5")
    gateway_env(monkeypatch)
    keys = client.app.state.store.ai_keys
    keys._save("demo", fast_model="anthropic/claude-haiku-4-5")
    assert keys.ai("demo").model(lm.DECIDE) == "sopops-system1"  # the deployment's classifier, through the gateway
    keys._save("demo", typesafe_ciphertext=crypto.encrypt("ts-team-key"), typesafe_hint="…key")
    ai = keys.ai("demo")
    assert ai.system1 is None and ai.jev and ai.jev_api_key == "ts-team-key" and ai.model(lm.DECIDE) == lm.JEV_MODEL
    assert client.get("/v1/workspaces/demo/ai").json()["typesafe"]["source"] == "team"
