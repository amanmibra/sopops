"""Extraction: existing agent prompts in, a proposal (blocks, groups, agents, findings, fidelity) out.

1. **Split** (LLM, per agent): the prompt is cut into numbered units (paragraphs) here; the model groups consecutive
   units into segments and labels each one context, instruction, procedure or heading. It never rewrites text, and
   any unit it leaves out is kept as agent context, so nothing is dropped.
2. **Dedupe** (no LLM): segments are matched across agents by word similarity. Identical copies become one block.
   Near-copies that differ only by short values become one block with `{{variables}}` and each agent's value; a
   difference in a rule word ("once" vs "twice") is reported as drift. Copies that differ by more (a step added)
   stay one block per version and are reported as drift.
3. **Structure** (LLM, once per procedure): the procedure's text becomes goal, when, steps (with tools), never and
   warning signs, keeping the source wording and every `{{variable}}`.
4. **Name** (LLM, fast): each variable gets a name; a heuristic name is used if the call fails.
5. **Propose**: agents (context, blocks in prompt order, variables), a group for blocks every agent starts with,
   and a fidelity check: each agent compiled with sopc and compared with its original (`fidelity.check`).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..ai import spend
from ..ai.lm import AI, AIError
from ..ai.signatures import NameVariables, SegmentPrompt, Slot, StructureProcedure, Unit
from ..sopc import Sopc
from . import fidelity
from . import text as tx
from .types import (
    Finding,
    ProcedureDraft,
    Proposal,
    ProposalContent,
    ProposedAgent,
    ProposedGroup,
    ProposedInstruction,
    ProposedProcedure,
    ProposedSettings,
    ProposedStep,
    SourcePrompt,
    Variant,
)

GUIDE = Path(__file__).parent / "guide.md"
Progress = Callable[[str, str], None]
MATCH = 0.6  # word similarity at which two segments are copies of one passage


def guide_text() -> str:
    """How a coding agent extracts prompts itself (sopops-mcp's guide)."""
    return GUIDE.read_text()


@dataclass
class Seg:
    agent: str
    kind: str
    title: str
    text: str
    order: int

    @property
    def family(self) -> str:
        return "procedure" if self.kind == "procedure" else "heading" if self.kind == "heading" else "text"

    @property
    def key(self) -> str:
        return tx.norm(tx.strip_list_marks(self.text) if self.kind == "procedure" else self.text)


@dataclass
class Cluster:
    family: str
    members: list[Seg] = field(default_factory=list)

    @property
    def agents(self) -> list[str]:
        return [m.agent for m in self.members]

    @property
    def title(self) -> str:
        return Counter(m.title for m in self.members).most_common(1)[0][0]


@dataclass
class VarSlot:
    slot: tx.Slot
    values: dict[str, str]  # agent -> value
    context: str  # the template around it, for naming
    policy: bool


def host_extraction(sources: list[SourcePrompt]) -> dict:
    """What a coding agent needs to extract prompts itself (sopops-mcp's `propose_extraction`): the instructions, and each
    prompt split into numbered units for it to classify and structure."""
    ids = agent_ids(sources)
    return {
        "mode": "host",
        "instructions": guide_text() + "\n\n## How to classify\n\n" + SegmentPrompt.instructions + "\n\n## Procedures\n\n" + StructureProcedure.instructions,
        "inputs": [
            {"id": i, "name": s.name, "platform": s.platform, "platform_id": s.platform_id or i, "tools": s.tools,
             "units": [{"n": n + 1, "text": u} for n, u in enumerate(tx.split_units(s.prompt))]}
            for i, s in zip(ids, sources)
        ],
        "originals": {i: s.prompt for i, s in zip(ids, sources)},
        "proposal_schema": ProposalContent.model_json_schema(),
        "next": "Build the proposal from these units, then call check_fidelity(proposal, originals).",
    }


def agent_ids(sources: list[SourcePrompt]) -> list[str]:
    taken: set[str] = set()
    return [tx.unique(tx.slug(s.name, "agent"), taken) for s in sources]


# --- 1. split -------------------------------------------------------------------------------------


def split(ai: AI, source: SourcePrompt, agent: str, notes: list[str], guidance: str = "") -> list[Seg]:
    units = tx.split_units(source.prompt)
    result = ai.predict("reasoning", SegmentPrompt, agent=source.name, platform=source.platform, tools=source.tools,
                        units=[Unit(n=i + 1, text=u) for i, u in enumerate(units)], guidance=guidance)
    owner: dict[int, int] = {}
    for si, seg in enumerate(result.segments):
        for n in seg.units:
            if 1 <= n <= len(units) and n not in owner:
                owner[n] = si
    segs: list[Seg] = []
    run: list[int] = []

    def flush() -> None:
        if not run:
            return
        si = owner.get(run[0])
        if si is None:
            kind, title = "context", "Unplaced text"
            notes.append(f"{agent}: {len(run)} paragraph(s) weren't placed by the model; kept in the agent's own text.")
        else:
            kind, title = result.segments[si].kind, result.segments[si].title.strip() or "Untitled"
        segs.append(Seg(agent, kind, title, "\n\n".join(units[n - 1] for n in run), len(segs)))
        run.clear()

    for n in range(1, len(units) + 1):
        if run and owner.get(n) != owner.get(run[-1]):
            flush()
        run.append(n)
    flush()
    return segs


# --- 2. dedupe --------------------------------------------------------------------------------------


def cluster(segs: list[Seg]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for s in segs:
        if s.family == "heading":
            clusters.append(Cluster("heading", [s]))
            continue
        best, score = None, MATCH
        for c in clusters:
            if c.family != s.family or s.agent in c.agents:
                continue
            sim = max(tx.similarity(s.key, m.key) for m in c.members)
            if sim >= score:
                best, score = c, sim
        if best:
            best.members.append(s)
        else:
            clusters.append(Cluster(s.family, [s]))
    return clusters


@dataclass
class Block:
    id: str
    kind: str  # instruction | procedure
    title: str
    text: str  # with {{slot names}} once named
    agents: list[str]
    template: tx.Template | None = None


class Builder:
    def __init__(self, sources: list[SourcePrompt], ids: list[str]):
        self.sources = dict(zip(ids, sources))
        self.ids = ids
        self.blocks: list[Block] = []
        self.taken: set[str] = set()
        self.agent_blocks: dict[str, list[tuple[int, str]]] = {a: [] for a in ids}  # (order, block id)
        self.context: dict[str, list[tuple[int, str]]] = {a: [] for a in ids}
        self.slots: list[VarSlot] = []
        self.slot_owner: dict[int, Block] = {}
        self.findings: list[Finding] = []
        self.headings: dict[str, list[str]] = {a: [] for a in ids}
        self.notes: list[str] = []

    def add(self, c: Cluster) -> None:
        if c.family == "heading":
            for m in c.members:
                self.headings[m.agent].append(m.text.strip())
            return
        kinds = {m.kind for m in c.members}
        if c.family == "text" and kinds == {"context"} and len(c.members) == 1:
            m = c.members[0]
            self.context[m.agent].append((m.order, m.text))
            return
        kind = "procedure" if c.family == "procedure" else "instruction"
        if kinds == {"context"}:
            self.notes.append(f"'{c.title}' was marked as one agent's own text, but {len(c.members)} agents have it; it's a shared instruction.")
        text_of = (lambda m: tx.strip_list_marks(m.text)) if kind == "procedure" else (lambda m: m.text)
        groups: dict[str, list[Seg]] = {}
        for m in c.members:
            groups.setdefault(tx.norm(text_of(m)), []).append(m)
        rank = {a: i for i, a in enumerate(self.ids)}
        variants = sorted(groups.values(), key=lambda g: (-len(g), min(rank[x.agent] for x in g)))
        base_id = tx.slug(c.title, kind)
        if len(variants) == 1:
            block = self._block(base_id, kind, c.title, text_of(variants[0][0]), [m.agent for m in c.members], c.members)
            if len(c.members) > 1:
                self.findings.append(Finding(
                    id=f"dup-{block.id}", kind="duplicate", title=c.title, block=block.id,
                    message=f"{len(c.members)} agents had the same {_what(kind)} '{c.title}'; it's one block now.",
                    variants=[Variant(agents=[m.agent for m in c.members], text=block.text, block=block.id)],
                ))
            return
        t = tx.template([text_of(g[0]) for g in variants])
        if t is not None:
            block = self._block(base_id, kind, c.title, "", [m.agent for m in c.members], c.members, t)
            for slot in t.slots:
                values = {m.agent: slot.values[i] for i, g in enumerate(variants) for m in g}
                excerpt = "".join(f"[[{p.values[0]}]]" if p is slot else (p.values[0] if isinstance(p, tx.Slot) else p) for p in t.parts)
                vs = VarSlot(slot, values, excerpt[:400], slot.is_policy)
                self.slots.append(vs)
                self.slot_owner[id(slot)] = block
            return
        # Too different for one template: a block per version, and a decision for the reviewer.
        vs = []
        for g in variants:
            suffix = g[0].agent if len(variants) > 1 and g is not variants[0] else ""
            block = self._block(f"{base_id}-{suffix}" if suffix else base_id, kind, c.title, text_of(g[0]), [m.agent for m in g], g)
            vs.append(Variant(agents=[m.agent for m in g], text=block.text, block=block.id))
        self.findings.append(Finding(
            id=f"drift-{vs[0].block}", kind="drift", title=c.title,
            message=f"{_count(c.members)} agents differ on '{c.title}': {len(vs)} versions. Pick one, or keep each agent's version.",
            variants=vs,
        ))

    def _block(self, base: str, kind: str, title: str, text: str, agents: list[str], members: list[Seg], t: tx.Template | None = None) -> Block:
        block = Block(tx.unique(base, self.taken), kind, title, text, agents, t)
        self.blocks.append(block)
        for m in members:
            self.agent_blocks[m.agent].append((m.order, block.id))
        return block

    def name_variables(self, ai: AI) -> None:
        """One variable per distinct per-agent mapping, named by the model (or a heuristic), and findings for them."""
        distinct: list[VarSlot] = []
        for vs in self.slots:
            same = next((d for d in distinct if d.values == vs.values), None)
            if same:
                vs.slot.name = "\0" + str(distinct.index(same))  # resolved below
            else:
                vs.slot.name = "\0" + str(len(distinct))
                distinct.append(vs)
        names = heuristic_names([(d.context, d.values) for d in distinct], self.sources)
        if distinct:
            try:
                got = ai.predict("fast", NameVariables, slots=[Slot(n=i + 1, text=d.context, values=d.values) for i, d in enumerate(distinct)]).names
                if len(got) == len(distinct):
                    names = [tx.var_name(n) if n.strip() else names[i] for i, n in enumerate(got)]
            except (AIError, ValueError) as e:
                self.notes.append(f"Variables were named by rule, not by the model ({str(e)[:120]}).")
        taken: set[str] = set()
        final = [tx.unique(n, taken, "_") for n in names]
        for vs in self.slots:
            vs.slot.name = final[int(vs.slot.name[1:])]
        for b in self.blocks:
            if b.template:
                b.text = b.template.render()
        # Findings: drift per policy slot, one 'values' finding per block for the rest.
        by_block: dict[str, list[VarSlot]] = {}
        for vs in self.slots:
            by_block.setdefault(self.slot_owner[id(vs.slot)].id, []).append(vs)
        seen_vars: set[str] = set()
        for block_id, slots in by_block.items():
            block = next(b for b in self.blocks if b.id == block_id)
            values = [vs for vs in slots if not vs.policy]
            if values:
                names_ = ", ".join(f"{{{{{vs.slot.name}}}}}" for vs in values)
                self.findings.append(Finding(
                    id=f"values-{block.id}", kind="values", title=block.title, block=block.id,
                    message=f"{_count_ids(block.agents)} agents had '{block.title}' with different values; it's one block with {names_}.",
                    variants=_by_value(values[0], block),
                ))
            for vs in slots:
                if not vs.policy or vs.slot.name in seen_vars:
                    continue
                seen_vars.add(vs.slot.name)
                counts = Counter(vs.values[a] for a in block.agents)
                parts = [f"{n} say{'s' if n == 1 else ''} \"{v.strip() or '(nothing)'}\"" for v, n in counts.most_common()]
                self.findings.append(Finding(
                    id=f"drift-{vs.slot.name}", kind="drift", title=block.title, block=block.id, variable=vs.slot.name,
                    message=f"{_count_ids(block.agents)} agents differ on '{block.title}': {', '.join(parts)}. Pick one, or keep both (as {{{{{vs.slot.name}}}}}).",
                    variants=[Variant(agents=[a for a in block.agents if vs.values[a] == v], text=_around(block.text, vs.slot.name, v), block=block.id) for v, _ in counts.most_common()],
                ))

    def variables(self, agent: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for vs in self.slots:
            if agent in vs.values:
                out[vs.slot.name] = vs.values[agent]
        return out


def _by_value(vs: VarSlot, block: Block) -> list[Variant]:
    agents = list(dict.fromkeys(block.agents))
    return [Variant(agents=[a for a in agents if vs.values[a] == v], text=v, block=block.id) for v in dict.fromkeys(vs.values[a] for a in agents)]


def _what(kind: str) -> str:
    return "procedure" if kind == "procedure" else "text"


def _count(members: list[Seg]) -> int:
    return len({m.agent for m in members})


def _count_ids(agents: list[str]) -> int:
    return len(set(agents))


def _around(text: str, name: str, value: str) -> str:
    """The sentence holding {{name}}, with the value filled in."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if f"{{{{{name}}}}}" in sentence:
            return sentence.replace(f"{{{{{name}}}}}", value).strip()
    return value


def heuristic_names(slots: list[tuple[str, dict[str, str]]], sources: dict[str, SourcePrompt]) -> list[str]:
    """Names from the values alone: the agent's own name, links, emails, phone numbers, times; else the words."""
    out = []
    for i, (context, values) in enumerate(slots):
        vals = [v.strip() for v in values.values()]
        names = {a: tx.norm(sources[a].name).lower() for a in values if a in sources}
        if values and all(tx.norm(v).lower() == names.get(a) or tx.slug(v.replace("'", "").replace("’", "")) == a for a, v in values.items()):
            out.append("name")
        elif all(re.search(r"@\w", v) for v in vals if v):
            out.append("email")
        elif all(tx.URLISH.search(v) for v in vals if v):
            out.append("link")
        elif all(re.fullmatch(r"[\d\s()+.-]{7,}", v) for v in vals if v):
            out.append("phone")
        elif all(re.search(r"\d\s*(am|pm)\b|\d:\d\d", v, re.I) for v in vals if v):
            out.append("hours")
        else:
            before = [w for w in tx.words(context.split("[[")[0])[-5:] if w not in NAME_STOPWORDS]
            first = next((v for v in vals if v), "")
            words = before[-2:] or first.split()[:3]
            out.append(tx.var_name(" ".join(words), f"value_{i + 1}") if words else f"value_{i + 1}")
    return out


NAME_STOPWORDS = {"the", "a", "an", "to", "of", "for", "with", "than", "more", "less", "and", "or", "is", "are", "be", "in", "on", "at", "by", "your", "our", "if", "when"}


# --- 3. structure -------------------------------------------------------------------------------------


def structure(ai: AI, block: Block, tools: list[str], guidance: str = "") -> ProcedureDraft:
    return ai.predict("reasoning", StructureProcedure, title=block.title, tools=tools, passage=block.text, guidance=guidance).procedure


def to_procedure(block: Block, d: ProcedureDraft, tools: list[str], notes: list[str]) -> ProposedProcedure:
    known = set(tools)

    def steps(rows) -> list[ProposedStep]:
        out = []
        for r in rows:
            if not r.text.strip():
                continue
            tool = (r.tool or "").strip() or None
            if tool and known and tool not in known:
                notes.append(f"{block.id}: '{tool}' isn't a tool of the agents that use it, so the step names no tool.")
                tool = None
            if tool and not re.fullmatch(r"\S+", tool):
                tool = None
            out.append(ProposedStep(text=" ".join(r.text.split()), tool=tool, required=bool(r.required and tool)))
        return out

    p = ProposedProcedure(
        id=block.id, name=" ".join(d.name.split()) or block.title, goal=" ".join(d.goal.split()), goal_inferred=d.goal_inferred and bool(d.goal.strip()),
        when=" ".join(d.when.split()), guidance=d.guidance.strip(), steps=steps(d.steps), never=steps(d.never),
        warning_signs=steps(d.warning_signs), used_by=list(dict.fromkeys(block.agents)), source=block.text,
    )
    wanted = set(tx.PLACEHOLDER.findall(block.text))
    fields = " ".join([p.name, p.goal, p.when, p.guidance, *[s.text for s in p.steps + p.never + p.warning_signs]])
    lost = wanted - set(tx.PLACEHOLDER.findall(fields))
    if lost:
        notes.append(f"{block.id}: the model dropped {', '.join('{{' + v + '}}' for v in sorted(lost))}; check the fidelity diff.")
    return p


# --- 4. groups -----------------------------------------------------------------------------------------


def common_group(agents: list[ProposedAgent], taken: set[str]) -> ProposedGroup | None:
    """A group for the blocks every agent starts with, when there are at least 3 agents and 2 such blocks."""
    lists = [a.blocks for a in agents]
    if len(lists) < 3:
        return None
    prefix: list[str] = []
    for column in zip(*lists):
        if len(set(column)) != 1:
            break
        prefix.append(column[0])
    if len(prefix) < 2:
        return None
    group = ProposedGroup(id=tx.unique("core", taken), blocks=prefix)
    for a in agents:
        a.blocks = [group.id, *a.blocks[len(prefix):]]
    return group


# --- the whole thing -------------------------------------------------------------------------------------


def extract(sources: list[SourcePrompt], ai: AI, sopc: Sopc | None = None, progress: Progress | None = None, workers: int = 4,
            guidance: str = "") -> Proposal:
    """`guidance`: how the user wants their instructions organized, passed to the splitting and structuring calls; empty
    leaves the model's default choices."""
    say = progress or (lambda step, message: None)
    guidance = (guidance or "").strip()
    ids = agent_ids(sources)
    notes: list[str] = []
    say("split", f"Splitting {len(sources)} agents' instructions into agent context, shared instructions and procedures" if len(sources) != 1 else "Splitting one agent's instructions into agent context, shared instructions and procedures")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        per_agent = list(pool.map(spend.carry(lambda pair: split(ai, pair[0], pair[1], notes, guidance)), zip(sources, ids)))
    for agent, segs in zip(ids, per_agent):
        counts = Counter(s.kind for s in segs)
        say("split", f"{agent}: {counts['instruction']} instruction(s), {counts['procedure']} procedure(s), {counts['context']} context passage(s)")

    say("dedupe", "Matching passages across agents")
    b = Builder(sources, ids)
    for c in cluster([s for segs in per_agent for s in segs]):
        b.add(c)
    b.name_variables(ai)
    b.notes = notes + b.notes
    shared = sum(1 for blk in b.blocks if len(set(blk.agents)) > 1)
    say("dedupe", f"{len(b.blocks)} blocks, {shared} shared by several agents; {len([f for f in b.findings if f.kind == 'drift'])} difference(s) to decide")

    procs = [blk for blk in b.blocks if blk.kind == "procedure"]
    tools_of = {blk.id: sorted({t for a in set(blk.agents) for t in b.sources[a].tools}) for blk in procs}
    if procs:
        say("structure", f"Structuring {len(procs)} procedure{'s' if len(procs) != 1 else ''}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        drafts = list(pool.map(spend.carry(lambda blk: structure(ai, blk, tools_of[blk.id], guidance)), procs))
    procedures: list[ProposedProcedure] = []
    demoted: set[str] = set()
    for blk, d in zip(procs, drafts):
        p = to_procedure(blk, d, tools_of[blk.id], b.notes)
        if not p.steps:
            b.notes.append(f"'{blk.title}' has no steps, so it's a shared instruction rather than a procedure.")
            blk.kind = "instruction"
            demoted.add(blk.id)
            continue
        procedures.append(p)
        say("structure", f"{p.name}: {len(p.steps)} step(s), {len(p.never)} never, {len(p.warning_signs)} warning sign(s)")
    instructions = [ProposedInstruction(id=blk.id, title=blk.title, text=blk.text.strip(), used_by=list(dict.fromkeys(blk.agents))) for blk in b.blocks if blk.kind == "instruction"]

    agents = []
    for agent_id, src in zip(ids, sources):
        agents.append(ProposedAgent(
            id=agent_id, name=src.name, platform=src.platform, platform_id=(src.platform_id or agent_id).strip(),
            context="\n\n".join(t for _, t in sorted(b.context[agent_id])).strip(),
            blocks=list(dict.fromkeys(bid for _, bid in sorted(b.agent_blocks[agent_id]))),
            variables=b.variables(agent_id), tools=list(src.tools),
        ))
    groups = [g] if (g := common_group(agents, b.taken)) else []

    settings = ProposedSettings()
    headings = [h for hs in b.headings.values() for h in hs]
    if headings:
        if len(set(headings)) == 1 and all(len(hs) == 1 for hs in b.headings.values() if hs) and headings[0].startswith("#"):
            settings.procedures_heading = headings[0]
        else:
            b.notes.append(f"Section headings left out (sopc adds its own heading before the procedures): {', '.join(sorted(set(headings)))}")

    proposal = Proposal(
        agents=agents, instructions=instructions, procedures=procedures, groups=groups, settings=settings,
        findings=b.findings, notes=list(dict.fromkeys(b.notes)), originals={a: s.prompt for a, s in zip(ids, sources)},
    )
    if sopc is not None:
        say("fidelity", "Compiling each agent with sopc and comparing it with its original")
        proposal = fidelity.check(proposal, sopc)
        kept = [f for f in proposal.fidelity]
        if kept:
            say("fidelity", "; ".join(f"{f.agent} {round(f.score * 100)}%" for f in kept))
    say("done", "Proposal ready for review")
    return proposal
