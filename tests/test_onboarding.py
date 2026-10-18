"""Onboarding: the extraction pipeline (with a fake LLM, no network), review, the import API, onboarding tokens."""

import json
import time
from datetime import timedelta
from pathlib import Path

import dspy
import httpx
import pytest
from conftest import SOPC_BIN, WS
from sqlmodel import Session, select

from sopops import Sopc
from sopops.ai.lm import Usage
from sopops.ai.signatures import NameVariables, SegmentPrompt, StructureProcedure
from sopops.onboarding import kickoff, platforms
from sopops.onboarding import text as tx
from sopops.onboarding.fidelity import check, parse_compare
from sopops.onboarding.pipeline import extract
from sopops.onboarding.review import resolve
from sopops.onboarding.rules import RulesModels
from sopops.onboarding.types import (
    ProcedureDraft,
    Segment,
    Segmentation,
    SourcePrompt,
    StepDraft,
)
from sopops.orm import ImportRecord, Token

FIXTURES = Path(__file__).parent / "fixtures" / "onboarding"
IMPORTS = f"{WS}/imports"


def sources() -> list[SourcePrompt]:
    meta = json.loads((FIXTURES / "sources.json").read_text())
    return [SourcePrompt(name=m["name"], platform=m["platform"], platform_id=m["platform_id"], tools=m["tools"], prompt=(FIXTURES / m["file"]).read_text()) for m in meta]


@pytest.fixture(scope="module")
def proposal():
    return extract(sources(), RulesModels(), Sopc(SOPC_BIN))


# --- text --------------------------------------------------------------------------------


def test_units_keep_every_paragraph():
    prompt = "Intro line.\nSecond line.\n\n## Allergies\nWhen X:\n1. Do Y\n\nNotes here\n## Inline heading\nbody\n"
    units = tx.split_units(prompt)
    assert units == ["Intro line.\nSecond line.", "## Allergies\nWhen X:\n1. Do Y", "Notes here", "## Inline heading\nbody"]


def test_templates_values_drift_and_too_different():
    t = tx.template(["You are the host for Mario's Pizzeria. Be kind.", "You are the host for Golden Wok. Be kind."])
    assert t is not None and len(t.slots) == 1 and t.slots[0].values == ["Mario's Pizzeria", "Golden Wok"]
    assert not t.slots[0].is_policy
    t.slots[0].name = "name"
    assert t.render() == "You are the host for {{name}}. Be kind."
    upsell = tx.template(["Never upsell more than once per call.", "Never upsell more than twice per call."])
    assert upsell and upsell.slots[0].is_policy
    # A step inserted in one copy is not a value: no template.
    assert tx.template(["Ask for the address.\nCheck the zone.", "Ask for the address.\nAsk for a gate code.\nCheck the zone."]) is None


def test_parse_compare():
    out = (
        "a: 96% of 27 sentences kept (LOST OR CHANGED TEXT)\n"
        "  - missing:  Gone sentence.\n"
        "  ! changed:  Never upsell more than twice per call.\n"
        "              now: Never upsell more than once per call.   ('twice' → 'once')\n"
        "  ~ reworded: Split one.\n"
        "  + added:    New line.\n"
        "b: 100% of 3 sentences kept (ok)\n"
    )
    r = parse_compare(out)
    assert r["a"]["total"] == 27 and r["a"]["missing"] == ["Gone sentence."]
    assert r["a"]["changed"] == [("Never upsell more than twice per call.", "Never upsell more than once per call.")]
    assert r["a"]["reworded"] == ["Split one."] and r["a"]["added"] == ["New line."]
    assert r["b"] == {"total": 3, "missing": [], "changed": [], "reworded": [], "added": []}


# --- the pipeline ---------------------------------------------------------------------------


def test_extracts_shared_blocks_variables_and_drift(proposal):
    agents = {a.id: a for a in proposal.agents}
    assert list(agents) == ["marios-pizzeria", "bella-pasta", "golden-wok", "harbor-grill"]
    assert agents["bella-pasta"].platform == "elevenlabs" and agents["bella-pasta"].platform_id == "agent_fake_bella_0001"
    # Each agent's own facts stay in its context.
    assert agents["golden-wok"].context.startswith("Golden Wok is a Cantonese kitchen")
    # Shared by everyone, identical: one block.
    closing = next(i for i in proposal.instructions if "Before hanging up" in i.text)
    assert sorted(closing.used_by) == sorted(agents)
    assert any(f.kind == "duplicate" and f.block == closing.id for f in proposal.findings)
    # The name differs: one block with a variable, each agent's value.
    host = next(i for i in proposal.instructions if i.text.startswith("You are the phone host for {{"))
    var = host.text.split("{{")[1].split("}}")[0]
    assert agents["golden-wok"].variables[var] == "Golden Wok" and agents["marios-pizzeria"].variables[var] == "Mario's Pizzeria"
    assert any(f.kind == "values" and f.block == host.id for f in proposal.findings)
    # The upsell rule drifted: kept as a variable, reported as drift.
    upsell = next(f for f in proposal.findings if f.kind == "drift" and f.variable and "upsell" in " ".join(v.text for v in f.variants))
    assert [v.agents for v in upsell.variants] == [["marios-pizzeria", "bella-pasta", "harbor-grill"], ["golden-wok"]]
    assert agents["golden-wok"].variables[upsell.variable] == "twice"
    # Delivery differs by a whole step: one block per version, reported as drift.
    delivery = next(f for f in proposal.findings if f.kind == "drift" and not f.variable and f.title == "Delivery")
    assert [v.agents for v in delivery.variants] == [["marios-pizzeria"], ["golden-wok"]]
    assert {v.block for v in delivery.variants} == {"delivery", "delivery-golden-wok"}
    # Procedures are structured, with tool names.
    allergies = next(p for p in proposal.procedures if p.name == "Allergies")
    assert [s.tool for s in allergies.steps] == [None, None, "lookup_allergens"]
    assert allergies.never[0].text == 'Never say an item is "allergen-free" or "safe".'
    assert allergies.warning_signs[0].tool == "transfer_to_staff" and "{{" in allergies.warning_signs[0].text
    assert sorted(allergies.used_by) == sorted(agents)
    # Every agent starts with the same blocks: a group.
    assert proposal.groups and all(a.blocks[0] == proposal.groups[0].id for a in proposal.agents)


def test_fidelity_is_complete_and_flags_dropped_text(proposal):
    assert [f.agent for f in proposal.fidelity] == [a.id for a in proposal.agents]
    assert all(f.ok and f.score == 1.0 and f.sentences > 15 for f in proposal.fidelity), [(f.agent, f.missing, f.changed) for f in proposal.fidelity]
    assert not [i for i in proposal.issues if i.severity == "error"]
    # Drop a sentence from a shared block: every agent that uses it loses it.
    broken = proposal.model_copy(deep=True)
    closing = next(i for i in broken.instructions if "Before hanging up" in i.text)
    closing.text = "Thank the caller."
    checked = check(broken, Sopc(SOPC_BIN))
    lost = {f.agent: f for f in checked.fidelity}
    assert all(not f.ok for f in lost.values())
    assert any("Before hanging up" in m for m in lost["bella-pasta"].missing + [c.original for c in lost["bella-pasta"].changed])
    assert lost["bella-pasta"].score < 1.0


class Scripted:
    """Fake models: answer each step from a script, and record what was asked."""

    def __init__(self, segments=None, procedure=None, names=None):
        self.segments, self.procedure, self.names, self.calls = segments, procedure, names, []
        self.usage = Usage()

    def model(self, role):
        return "fake"

    def decision_native(self):
        return False

    def predict(self, role, signature, **inputs):
        self.calls.append((role, signature.__name__))
        if signature is SegmentPrompt:
            return dspy.Prediction(segments=self.segments.segments)
        if signature is StructureProcedure:
            return dspy.Prediction(procedure=self.procedure)
        assert signature is NameVariables
        return dspy.Prediction(names=self.names or [])


def test_units_the_model_leaves_out_are_kept_as_context():
    src = SourcePrompt(name="Solo", prompt="Solo Diner is on Main Street.\n\nBe brief.\n\nWe close at 9pm.")
    fake = Scripted(segments=Segmentation(segments=[Segment(units=[2], kind="instruction", title="Voice")]))
    p = extract([src], fake, Sopc(SOPC_BIN))
    assert p.agents[0].context == "Solo Diner is on Main Street.\n\nWe close at 9pm."
    assert [i.text for i in p.instructions] == ["Be brief."]
    assert any("weren't placed" in n for n in p.notes)
    assert p.fidelity[0].ok
    assert fake.calls == [("reasoning", "SegmentPrompt")]


def test_procedures_keep_tools_from_the_agent_and_report_dropped_variables():
    a = SourcePrompt(name="A", tools=["book_table"], prompt="## Booking\nWhen someone books:\n1. Ask the size.\n2. Book it for Alpha Cafe with book_table.")
    b = SourcePrompt(name="B", tools=["book_table"], prompt="## Booking\nWhen someone books:\n1. Ask the size.\n2. Book it for Beta Bistro with book_table.")
    segs = Segmentation(segments=[Segment(units=[1], kind="procedure", title="Booking")])
    draft = ProcedureDraft(name="Booking", goal="The table is booked.", goal_inferred=True, when="Someone books.", steps=[
        StepDraft(text="Ask the size."), StepDraft(text="Book it.", tool="made_up_tool"),
    ])
    p = extract([a, b], Scripted(segments=segs, procedure=draft, names=["cafe_name"]), Sopc(SOPC_BIN))
    proc = p.procedures[0]
    assert proc.goal_inferred and proc.steps[1].tool is None
    assert "{{cafe_name}}" in proc.source and p.agents[1].variables == {"cafe_name": "Beta Bistro"}
    assert any("made_up_tool" in n for n in p.notes) and any("dropped {{cafe_name}}" in n for n in p.notes)
    assert not all(f.ok for f in p.fidelity)  # the model dropped text: the check says so


def test_resolving_drift(proposal):
    upsell = next(f for f in proposal.findings if f.kind == "drift" and f.variable)
    one = resolve(proposal, upsell.id, "marios-pizzeria")
    assert all(upsell.variable not in a.variables for a in one.agents)
    assert any("more than once per call" in i.text for i in one.instructions)
    assert next(f for f in one.findings if f.id == upsell.id).resolved == "marios-pizzeria"
    delivery = next(f for f in proposal.findings if f.kind == "drift" and f.title == "Delivery")
    two = resolve(proposal, delivery.id, "marios-pizzeria")
    wok = next(a for a in two.agents if a.id == "golden-wok")
    assert "delivery" in wok.blocks and "delivery-golden-wok" not in wok.blocks
    assert not any(p.id == "delivery-golden-wok" for p in two.procedures)
    assert next(p for p in two.procedures if p.id == "delivery").used_by == ["marios-pizzeria", "golden-wok"]
    assert resolve(proposal, delivery.id, "keep").findings[[f.id for f in proposal.findings].index(delivery.id)].resolved == "keep"


# --- the API ---------------------------------------------------------------------------------


def wait_ready(client, id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"{IMPORTS}/{id}").json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("import didn't finish")


def payload():
    return {"sources": [s.model_dump() for s in sources()], "author": "tester"}


def test_import_without_a_model_says_how(client, monkeypatch):
    client.app.state.onboarding.ai = None
    res = client.post(IMPORTS, json=payload())
    assert res.status_code == 503 and "SOPOPS_MODEL_REASONING" in res.json()["detail"]
    assert client.get("/v1/onboarding/capabilities").json()["llm"] is False


def test_import_review_and_apply_creates_drafts_only(client):
    client.app.state.onboarding.ai = RulesModels()
    created = client.post(IMPORTS, json=payload())
    assert created.status_code == 201 and created.json()["status"] == "running"
    body = wait_ready(client, created.json()["id"])
    assert body["status"] == "ready", body["error"]
    id, p = body["id"], body["proposal"]
    events = client.get(f"{IMPORTS}/{id}/events").json()
    assert [e["step"] for e in events][0] == "start" and events[-1]["step"] == "done"
    assert client.get(f"{IMPORTS}/{id}/events", params={"after": events[-2]["id"]}).json() == events[-1:]

    # Edit a block in the review, then decide the upsell drift.
    closing = next(i for i in p["instructions"] if "Before hanging up" in i["text"])
    closing["text"] += " Thank the caller."
    edited = client.put(f"{IMPORTS}/{id}/proposal", json={k: p[k] for k in ("agents", "instructions", "procedures", "groups", "settings")}).json()
    assert any("Thank the caller." in (f["compiled"] or "") for f in edited["proposal"]["fidelity"])
    upsell = next(f for f in p["findings"] if f["kind"] == "drift" and f["variable"])
    decided = client.post(f"{IMPORTS}/{id}/findings/{upsell['id']}", json={"choice": "golden-wok"}).json()
    assert next(f for f in decided["proposal"]["findings"] if f["id"] == upsell["id"])["resolved"] == "golden-wok"
    assert client.post(f"{IMPORTS}/{id}/findings/{upsell['id']}", json={"choice": "nobody"}).status_code == 400

    applied = client.post(f"{IMPORTS}/{id}/apply", json={"author": "tester"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["import"]["status"] == "applied" and applied.json()["changed"]
    cfg = client.get(f"{WS}/config").json()
    assert sorted(a["id"] for a in cfg["agents"]) == ["bella-pasta", "golden-wok", "harbor-grill", "marios-pizzeria"]
    assert any("Thank the caller." in i["text"] for i in cfg["instructions"])
    assert {g["id"] for g in cfg["groups"]} == {g["id"] for g in p["groups"]}
    draft = client.get(f"{WS}/draft").json()
    assert draft["release"] is None and {a["agent"] for a in draft["agents"] if a["status"] == "added"} == {a["id"] for a in cfg["agents"]}
    assert client.get(f"{WS}/releases").json() == []  # nothing published
    assert "twice per call" in client.post(f"{WS}/config/agents/bella-pasta/preview", json={k: v for k, v in next(a for a in cfg["agents"] if a["id"] == "bella-pasta").items() if k in ("platform", "platform_id", "context", "blocks", "variables")}).json()["prompt"]
    assert client.post(f"{IMPORTS}/{id}/apply", json={}).status_code == 409


def test_platform_import_uses_the_key_only_in_the_request(client, monkeypatch):
    client.app.state.onboarding.ai = RulesModels()
    seen = []

    def fake_get_agent(platform, api_key, agent_id, transport=None):
        seen.append((platform, api_key, agent_id))
        src = sources()[1]
        return {"name": src.name, "platform": platform, "platform_id": agent_id, "prompt": src.prompt, "tools": src.tools, "notes": []}

    monkeypatch.setattr(platforms, "get_agent", fake_get_agent)
    res = client.post(IMPORTS, json={"platform": {"platform": "elevenlabs", "api_key": "sk-fake-123", "agent_ids": ["agent_fake_1"]}})
    assert res.status_code == 201 and seen == [("elevenlabs", "sk-fake-123", "agent_fake_1")]
    body = wait_ready(client, res.json()["id"])
    assert body["proposal"]["agents"][0]["platform_id"] == "agent_fake_1"
    with Session(client.app.state.store.engine) as db:
        assert "sk-fake-123" not in json.dumps(db.get(ImportRecord, body["id"]).model_dump(), default=str)


def test_platform_clients_read_prompts_and_tools():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.elevenlabs.io":
            assert request.headers["xi-api-key"] == "k1"
            if request.url.path == "/v1/convai/agents":
                return httpx.Response(200, json={"agents": [{"agent_id": "ag1", "name": "Bella"}], "has_more": False})
            if request.url.path == "/v1/convai/agents/ag1":
                return httpx.Response(200, json={"name": "Bella", "conversation_config": {"agent": {"prompt": {
                    "prompt": "Be kind.", "tools": [{"name": "lookup_allergens"}], "built_in_tools": {"end_call": {"name": "end_call"}, "skip_turn": None}, "tool_ids": ["t1"]}}}})
            if request.url.path == "/v1/convai/tools/t1":
                return httpx.Response(200, json={"tool_config": {"name": "check_reservations"}})
        if request.url.host == "api.vapi.ai":
            assert request.headers["authorization"] == "Bearer k2"
            if request.url.path == "/assistant":
                return httpx.Response(200, json=[{"id": "as1", "name": "Wok"}])
            if request.url.path == "/assistant/as1":
                return httpx.Response(200, json={"name": "Wok", "endCallFunctionEnabled": True, "model": {
                    "messages": [{"role": "system", "content": "Be brief."}], "tools": [{"type": "function", "function": {"name": "check_delivery_zone"}}, {"type": "transferCall"}], "toolIds": ["t2"]}})
            if request.url.path == "/tool/t2":
                return httpx.Response(200, json={"type": "function", "function": {"name": "place_order"}})
            if request.url.path == "/assistant/nope":
                return httpx.Response(401, json={"message": "bad key k2"})
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    assert platforms.list_agents("elevenlabs", "k1", t) == [{"id": "ag1", "name": "Bella"}]
    el = platforms.get_agent("elevenlabs", "k1", "ag1", t)
    assert el["prompt"] == "Be kind." and el["tools"] == ["lookup_allergens", "end_call", "check_reservations"]
    assert platforms.list_agents("vapi", "k2", t) == [{"id": "as1", "name": "Wok"}]
    va = platforms.get_agent("vapi", "k2", "as1", t)
    assert va["prompt"] == "Be brief." and va["tools"] == ["check_delivery_zone", "transferCall", "place_order", "endCall"]
    with pytest.raises(platforms.PlatformError) as e:
        platforms.get_agent("vapi", "k2", "nope", t)
    assert "k2" not in str(e.value)


# --- onboarding tokens: Claude Code / Codex ------------------------------------------------------


def start_agent(client, **kw):
    res = client.post(f"{IMPORTS}/agent", json={"mode": "repo", "repo": "github.com/example/agents", "server_url": "http://sopops.test", **kw})
    assert res.status_code == 201, res.text
    return res.json()


def push_body(p, **extra):
    return {"proposal": {k: p[k] for k in ("agents", "instructions", "procedures", "groups", "settings")}, "originals": p["originals"], **extra}


def test_onboarding_token_flow(client, proposal):
    started = start_agent(client)
    token, id = started["token"], started["import"]["id"]
    assert token.startswith("sopops_onb_") and started["import"]["status"] == "waiting"
    assert started["import"]["token"]["active"] and started["import"]["token"]["scopes"] == ["onboarding:write"]
    with Session(client.app.state.store.engine) as db:
        row = db.exec(select(Token).where(Token.import_id == id)).one()
        assert row.token_hash and token not in json.dumps(row.model_dump(), default=str)  # stored hashed
        assert token not in json.dumps(db.get(ImportRecord, id).model_dump(), default=str)
    auth = {"Authorization": f"Bearer {token}"}
    session = client.get("/v1/onboarding/session", headers=auth).json()
    assert session["workspace"] == "demo" and session["mode"] == "repo" and session["repo"] == "github.com/example/agents"

    assert client.post("/v1/onboarding/progress", headers=auth, json={"step": "find", "message": "Found 4 agents in agents/*.py"}).status_code == 200
    assert client.get(f"{IMPORTS}/{id}").json()["status"] == "running"
    p = json.loads(proposal.model_dump_json())
    first = client.put("/v1/onboarding/proposal", headers=auth, json=push_body(p, findings=p["findings"]))
    assert first.status_code == 200 and first.json()["pushes"] == 1
    assert all(f["ok"] for f in first.json()["fidelity"])
    # Pushing again replaces the proposal.
    p2 = json.loads(json.dumps(p))
    p2["agents"] = p2["agents"][:2]
    second = client.put("/v1/onboarding/proposal", headers=auth, json=push_body(p2)).json()
    assert second["pushes"] == 2
    got = client.get(f"{IMPORTS}/{id}").json()
    assert got["status"] == "ready" and [a["id"] for a in got["proposal"]["agents"]] == ["marios-pizzeria", "bella-pasta"]
    assert [e["by"] for e in client.get(f"{IMPORTS}/{id}/events").json()] == ["sopops", "agent", "agent", "agent"]
    checked = client.post("/v1/onboarding/fidelity", headers=auth, json={"proposal": push_body(p)["proposal"], "originals": p["originals"]}).json()
    assert len(checked["fidelity"]) == 4

    # The token can't do anything else: it isn't accepted elsewhere, and other workspaces' imports are separate.
    assert client.post("/v1/workspaces/demo/sessions", headers=auth, json={"platform": "api", "external_id": "x"}).status_code == 403
    assert client.get(f"/v1/workspaces/other/imports/{id}").status_code == 404
    # Nor does a workspace token with other scopes work here.
    user = client.post("/v1/workspaces/demo/tokens").json()["token"]
    assert client.get("/v1/onboarding/session", headers={"Authorization": f"Bearer {user}"}).status_code == 403
    assert started["import"]["id"] not in [t["import_id"] for t in client.get("/v1/workspaces/demo/tokens").json()]
    assert id in [t["import_id"] for t in client.get("/v1/workspaces/demo/tokens", params={"imports": True}).json()]
    # Applying ends the token.
    assert client.post(f"{IMPORTS}/{id}/apply", json={}).status_code == 200
    assert client.post("/v1/onboarding/progress", headers=auth, json={"step": "x", "message": "y"}).status_code == 401


def test_onboarding_tokens_expire_revoke_and_need_the_scope(client):
    no = client.get("/v1/onboarding/session")
    assert no.status_code == 401 and no.headers["www-authenticate"] == "Bearer"
    assert client.get("/v1/onboarding/session", headers={"Authorization": "Bearer sopops_onb_nope"}).status_code == 401

    onboarding = client.app.state.onboarding
    expired = onboarding.start_agent("demo", "http://x", ttl=timedelta(seconds=-1))
    res = client.get("/v1/onboarding/session", headers={"Authorization": f"Bearer {expired['token']}"})
    assert res.status_code == 401 and "expired" in res.json()["detail"]

    started = start_agent(client, mode="hosted")
    auth = {"Authorization": f"Bearer {started['token']}"}
    assert client.get("/v1/onboarding/session", headers=auth).status_code == 200
    with Session(client.app.state.store.engine) as db:
        row = db.exec(select(Token).where(Token.import_id == started["import"]["id"])).one()
        row.scopes = ["ingest"]
        db.add(row)
        db.commit()
    assert client.post("/v1/onboarding/progress", headers=auth, json={"step": "a", "message": "b"}).status_code == 403
    revoked = client.post(f"{IMPORTS}/{started['import']['id']}/revoke").json()
    assert revoked["token"]["active"] is False and revoked["token"]["revoked_at"]
    res = client.get("/v1/onboarding/session", headers=auth)
    assert res.status_code == 401 and "revoked" in res.json()["detail"]

    third = start_agent(client)
    assert client.delete(f"{IMPORTS}/{third['import']['id']}").json()["status"] == "discarded"
    assert client.get("/v1/onboarding/session", headers={"Authorization": f"Bearer {third['token']}"}).status_code == 401


def test_stream_sends_progress_then_ends(client):
    started = start_agent(client)
    auth = {"Authorization": f"Bearer {started['token']}"}
    client.post("/v1/onboarding/progress", headers=auth, json={"step": "find", "message": "Reading agents/"})
    client.delete(f"{IMPORTS}/{started['import']['id']}")
    body = client.get(f"{IMPORTS}/{started['import']['id']}/stream").text
    assert "event: progress" in body and "Reading agents/" in body and '"status": "discarded"' in body


def test_kickoff_is_one_command_per_agent_and_the_skill_is_the_instructions(monkeypatch, tmp_path):
    monkeypatch.delenv("SOPOPS_MCP_SOURCE", raising=False)
    k = kickoff.kickoff("prod", "https://sopops.example/", "sopops_onb_abc", mode="repo", repo="github.com/example/agents")
    assert k["claude_code"] == (
        "claude mcp add sopops --env SOPOPS_URL=https://sopops.example --env SOPOPS_TOKEN=sopops_onb_abc "
        "-- uvx --from git+https://github.com/amanmibra/sopops@v0 sopops-mcp "
        "&& claude 'Onboard this project'\"'\"'s agents into sopops: call the sopops get_kickoff tool and follow it.'"
    )
    assert k["codex"].startswith("codex mcp add sopops --env SOPOPS_URL=https://sopops.example ") and "&& codex " in k["codex"]
    assert 'SOPOPS_TOKEN = "sopops_onb_abc"' in k["codex_toml"] and "[mcp_servers.sopops]" in k["codex_toml"]
    monkeypatch.setenv("SOPOPS_MCP_SOURCE", "sopops==0.2.0")
    assert "-- uvx --from sopops==0.2.0 sopops-mcp &&" in kickoff.kickoff("p", "http://x", "t")["claude_code"]

    skill = kickoff.skill_text()
    assert skill.startswith("---\nname: sopops-onboard") and "get_kickoff" in skill
    repo = kickoff.instructions({"workspace": "prod", "mode": "repo", "repo": "github.com/example/agents", "server_url": "https://sopops.example"})
    assert repo.startswith("# Onboard voice agents into sopops") and "Workspace: `prod`" in repo and "github.com/example/agents" in repo and "pull request" in repo
    assert "push_to_sopops" in repo and "check_fidelity" in repo and "report_progress" in repo
    assert "pull request" not in kickoff.instructions({"workspace": "prod", "mode": "hosted"}) and "Not connected" in kickoff.instructions(None)
    written = kickoff.install_skill(tmp_path, ["claude", "codex"])
    assert [w.relative_to(tmp_path).as_posix() for w in written] == [".claude/skills/sopops-onboard/SKILL.md", ".agents/skills/sopops-onboard/SKILL.md"]
    assert written[0].read_text() == skill
