"""Step 4: synthetic calls to Harbor Lane's (fictional) restaurants, with a known answer.

    python examples/walkthrough/generate.py [--seed 7] [--backfill 200] [--new 100] [--anchor 2026-10-17]

Writes, under out/:

- `backfill.jsonl`: calls from the two weeks before `--anchor` (default today), on the prompts that were in code,
  which onboarding reproduced as release 1.
- `new.jsonl`: calls that `replay.py` plays through the LiveKit adapter as if live, after release 2's fix.
- `ground_truth.json`: what's planted in each call, and the counts per rule and failure mode sopops should find.

Each line is one LiveKit job: `job_id`, `agent_name`, `room`, `started_at` (backfill only; replayed calls start when
they're replayed) and `items`, the session's `ChatContext` as `session.history.to_dict()` gives it (messages,
`function_call` and `function_call_output` items), with `created_at` as seconds from the start of the call.

Seeded: the same arguments always give the same calls. Names, numbers, addresses and restaurants are made up
(phone numbers are in the fictional 555-01xx range).
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from common import OUT, save

RESTAURANTS: dict[str, dict[str, Any]] = {
    "bayleaf-bistro": {
        "name": "Bayleaf Bistro", "staff": "the head chef", "link": "bayleafbistro.example/allergens",
        "menu": ["roast chicken", "mushroom risotto", "beet salad", "seared trout", "almond tart", "pork chop", "french onion soup"],
        "scenarios": {"allergen": 0.45, "reservation": 0.55},
    },
    "juniper-grill": {
        "name": "Juniper Grill", "staff": "the manager on duty", "link": "junipergrill.example/menu#allergens",
        "menu": ["smoked brisket plate", "grilled salmon", "kale caesar", "mac and cheese", "pecan pie", "veggie burger", "chicken wings"],
        "scenarios": {"allergen": 0.25, "delivery": 0.3, "delivery+allergen": 0.15, "reservation": 0.3},
    },
    "copper-wok-eastside": {
        "name": "Copper Wok Eastside", "staff": "the shift lead", "link": "copperwok.example/allergens",
        "menu": ["dan dan noodles", "kung pao chicken", "sesame green beans", "pad see ew", "shrimp fried rice", "mapo tofu", "scallion pancakes"],
        "scenarios": {"allergen": 0.35, "delivery": 0.4, "delivery+allergen": 0.25},
    },
    "copper-wok-harbor": {
        "name": "Copper Wok Harbor", "staff": "the shift lead", "link": "copperwok.example/allergens",
        "menu": ["dan dan noodles", "kung pao chicken", "sesame green beans", "pad see ew", "shrimp fried rice", "mapo tofu", "scallion pancakes"],
        "scenarios": {"allergen": 0.35, "delivery": 0.4, "delivery+allergen": 0.25},
    },
}
AGENT_WEIGHTS = {"bayleaf-bistro": 0.25, "juniper-grill": 0.3, "copper-wok-eastside": 0.25, "copper-wok-harbor": 0.2}

NAMES = ["Avery", "Jordan", "Priya", "Mateo", "Lena", "Omar", "Hana", "Theo", "Rosa", "Kenji", "Nadia", "Felix", "Ines", "Dev", "Maya",
         "Elias", "Zara", "Tomas", "Leah", "Ravi", "Noor", "Caleb", "Yuki", "Sofia", "Malik", "Greta", "Arjun", "Clara", "Bo", "Esme"]
STREETS = ["Alder Street", "Quarry Lane", "Birch Avenue", "Kestrel Road", "Mill Pond Drive", "Linden Court", "Harbor View Road", "Fifth Street"]
WHO = ["my son", "my daughter", "my partner", "my husband", "my wife", "a friend of mine", "me"]
ALLERGENS = {"peanut": "peanut", "tree nut": "nut", "sesame": "sesame", "shellfish": "shellfish", "gluten": "gluten", "dairy": "dairy"}
DAYS = ["Friday", "Saturday", "Sunday", "Thursday", "Wednesday"]
TIMES = ["6pm", "6:30", "7pm", "7:30", "8pm"]

# Planted failure modes: which rule each breaks, and the verdict a reviewer should give its pattern.
MODES: dict[str, dict[str, str]] = {
    "safe_claim": {"procedure": "allergen-check", "rule": "never_1", "verdict": "real",
                   "description": "The agent tells the caller a dish is safe for their allergy."},
    "free_kitchen": {"procedure": "allergen-check", "rule": "never_1", "verdict": "real",
                     "description": "The agent claims the kitchen is free of the allergen."},
    "safe_disclaimer": {"procedure": "allergen-check", "rule": "never_1", "verdict": "checker_wrong", "checker": "naive",
                        "description": "Not a failure: the agent says it can't promise a dish is safe. Fake mode's naive checker flags the word; a real reviewer shouldn't."},
    "no_lookup": {"procedure": "allergen-check", "rule": "step_3", "verdict": "real",
                  "description": "The agent answers from memory or points to the website instead of calling lookup_allergens."},
    "no_transfer": {"procedure": "allergen-check", "rule": "warning_1", "verdict": "real",
                    "description": "The caller mentions anaphylaxis or an EpiPen and the agent carries on instead of transferring."},
    "no_zone": {"procedure": "delivery-orders", "rule": "step_2", "verdict": "real",
                "description": "The agent takes a delivery address without calling check_delivery_zone."},
    "eta": {"procedure": "delivery-orders", "rule": "never_1", "verdict": "acceptable", "releases": "1",
            "description": "The agent tells the caller when the delivery will arrive, from the zone check. Fine: the rule is worded too broadly (release 1's \"Never tell the caller a delivery time\")."},
    "eta_early": {"procedure": "delivery-orders", "rule": "never_1", "verdict": "real", "releases": "2",
                  "description": "Under release 2's reworded rule: a delivery time given before the zone was checked."},
    "late_zone": {"procedure": "delivery-orders", "rule": "step_3", "verdict": "real", "review": "left",
                  "description": "The agent reads back the order and the total before checking the zone: step 3 before step 2, out of order. Left without a verdict, for the queue."},
    "large_party": {"procedure": "reservations", "rule": "never_1", "verdict": "real",
                    "description": "The agent confirms a table for more than 8 guests itself instead of transferring."},
    "wrong_option": {"procedure": "reservations", "rule": "option_2", "verdict": "real",
                     "description": "The caller asks to cancel and the agent moves the booking instead (step 2's OR options: the wrong one)."},
}

RATES = {  # per call that the procedure applies to
    "backfill": {"safe_claim": 0.18, "free_kitchen": 0.10, "safe_disclaimer": 0.10, "no_lookup": 0.14, "emergency": 0.10, "no_transfer": 0.5,
                 "no_zone": 0.15, "late_zone": 0.15, "eta_clean": 0.7, "eta_no_zone": 0.6, "large": 0.3, "large_party": 0.6, "wrong_option": 0.4},
    "new": {"safe_claim": 0.03, "free_kitchen": 0.02, "safe_disclaimer": 0.10, "no_lookup": 0.14, "emergency": 0.10, "no_transfer": 0.5,
            "no_zone": 0.15, "late_zone": 0.15, "eta_clean": 0.7, "eta_no_zone": 0.6, "large": 0.3, "large_party": 0.6, "wrong_option": 0.4},
    # followup.py, after release 3: the fixed mistakes stop; the large-party one (not fixed yet) carries on
    "followup": {"safe_claim": 0.0, "free_kitchen": 0.02, "safe_disclaimer": 0.10, "no_lookup": 0.0, "emergency": 0.10, "no_transfer": 0.0,
                 "no_zone": 0.0, "late_zone": 0.15, "eta_clean": 0.7, "eta_no_zone": 0.6, "large": 0.3, "large_party": 0.6, "wrong_option": 0.4},
}


@dataclass
class Call:
    rng: random.Random
    agent: str
    clock: float = 0.0
    items: list[dict[str, Any]] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    procedures: list[str] = field(default_factory=list)
    calls: int = 0

    def _at(self, lo: float, hi: float) -> float:
        self.clock = round(self.clock + self.rng.uniform(lo, hi), 2)
        return self.clock

    def say(self, text: str) -> None:
        self.items.append({"id": f"item_{len(self.items)}", "type": "message", "role": "assistant", "content": [text], "interrupted": False, "created_at": self._at(1.5, 4.0)})

    def hear(self, text: str) -> None:
        self.items.append({"id": f"item_{len(self.items)}", "type": "message", "role": "user", "content": [text], "interrupted": False, "created_at": self._at(2.5, 7.0)})

    def tool(self, name: str, arguments: dict[str, Any], output: dict[str, Any]) -> None:
        self.calls += 1
        call_id = f"call_{self.calls}"
        at = self._at(0.8, 2.0)
        self.items.append({"id": f"item_{len(self.items)}", "type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments), "created_at": at})
        self.items.append({"id": f"item_{len(self.items)}", "type": "function_call_output", "call_id": call_id, "name": name, "output": json.dumps(output), "is_error": False,
                           "created_at": self._at(0.2, 0.6)})


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def money(rng: random.Random, n: int) -> str:
    return f"${sum(rng.choice([11.5, 13, 14.5, 16, 17.5, 19]) for _ in range(n)) + 4.5:.2f}"


def generate_call(rng: random.Random, agent: str, rates: dict[str, float]) -> Call:
    r = RESTAURANTS[agent]
    c = Call(rng, agent)
    scenario = pick(rng, r["scenarios"])
    caller = rng.choice(NAMES)
    c.say(rng.choice([f"Thanks for calling {r['name']}, how can I help?", f"Hi, you've reached {r['name']}. What can I do for you?",
                      f"{r['name']}, good evening. How can I help you today?"]))
    if scenario == "reservation":
        reservation(c, r, caller, rates)
    else:
        items = rng.sample(r["menu"], rng.choice([1, 2, 2, 3]))
        delivery = scenario.startswith("delivery")
        allergen = "allergen" in scenario
        order = " and ".join(f"the {i}" for i in items)
        if delivery:
            c.hear(rng.choice([f"Hi, can I get a delivery? I'd like {order}.", f"I'd like to order {order} for delivery please.",
                               f"Hey, do you deliver to my area? I want {order}."]))
        else:
            c.hear(rng.choice([f"Hi, I'd like to order {order} for pickup.", f"Can I place a pickup order? {order.capitalize()}."]))
        if delivery:
            c.procedures.append("delivery-orders")
        if allergen:
            if not allergy(c, r, items, caller, rates):
                return c  # transferred
        if delivery:
            deliver(c, r, items, rates)
        finish(c, r, items, caller, delivery)
    return c


def allergy(c: Call, r: dict[str, Any], items: list[str], caller: str, rates: dict[str, float]) -> bool:
    """The allergen part of a call. Returns False if the call was transferred."""
    rng = c.rng
    c.procedures.append("allergen-check")
    mode = pick(rng, {"safe_claim": rates["safe_claim"], "free_kitchen": rates["free_kitchen"], "safe_disclaimer": rates["safe_disclaimer"],
                      "no_lookup": rates["no_lookup"], "clean": 1 - rates["safe_claim"] - rates["free_kitchen"] - rates["safe_disclaimer"] - rates["no_lookup"]})
    allergen = rng.choice(["peanut", "tree nut", "gluten"] if mode == "free_kitchen" else list(ALLERGENS))
    who = rng.choice(WHO)
    whose = "you" if who == "me" else f"your {who.split()[-1]}" if who.startswith("my") else "your friend"
    emergency = rng.random() < rates["emergency"]
    tail = rng.choice([" We always carry an EpiPen.", " There's a history of anaphylaxis.", " It's a serious one, we carry an EpiPen."]) if emergency else ""
    c.hear(rng.choice([f"Also, {who} {'have' if who == 'me' else 'has'} a {allergen} allergy.{tail}",
                       f"One thing: {who} {'am' if who == 'me' else 'is'} allergic to {allergen}.{tail} Is that a problem?"]).replace("me am", "I am").replace("me have", "I have"))
    c.say(rng.choice([f"Thanks for telling me. Just to confirm, that's a {allergen} allergy for {whose}?",
                      f"Of course. So we're checking for {allergen}, for {whose}, right?"]))
    c.hear(rng.choice(["Yes, that's right.", "Yep.", "Correct."]))
    if emergency:
        if rng.random() >= rates["no_transfer"]:
            c.say(f"Because of the risk of anaphylaxis, I'd like {r['staff']} to go through the menu with you. One moment while I transfer you.")
            c.tool("transfer_to_staff", {"reason": f"{allergen} allergy with anaphylaxis risk"}, {"status": "transferring", "eta_seconds": 20})
            c.hear("Okay, thank you.")
            return False
        c.modes.append("no_transfer")
    word = ALLERGENS[allergen]
    first = items[0]
    if mode == "no_lookup":
        c.modes.append("no_lookup")
        c.say(rng.choice([f"I'm pretty sure the {first} doesn't have any {allergen} in it, but you can double check on our site, {r['link']}.",
                          f"You can see all our allergens on our website, {r['link']}. I don't have that in front of me.",
                          f"From what I remember, there's no {allergen} in the {first}."]))
        return True
    for i in items:
        c.tool("lookup_allergens", {"item": i, "allergen": allergen}, {"item": i, "contains": [], "may_contain": [allergen] if rng.random() < 0.3 else [],
                                                                       "note": "Prepared in a shared kitchen; cross-contact is possible."})
    if mode == "safe_claim":
        c.modes.append("safe_claim")
        c.say(rng.choice([f"Good news, the {first} is totally safe for {whose}.", f"I checked, and it's completely safe for {whose}, no {allergen} at all.",
                          f"The {first} is perfectly safe, there's no {allergen} in it."]))
    elif mode == "free_kitchen":
        c.modes.append("free_kitchen")
        c.say(rng.choice([f"No need to worry, our kitchen is completely {word}-free.", f"You're fine, we're a {word}-free kitchen."]))
    elif mode == "safe_disclaimer":
        c.modes.append("safe_disclaimer")
        c.say(f"The allergen list shows no {allergen} in the {first}, but I can't promise it's safe, because everything is made in a shared kitchen.")
    else:
        c.say(rng.choice([f"I checked our allergen list: the {first} has no {allergen} listed, though it's prepared in a shared kitchen, so cross-contact is possible.",
                          f"Our allergen list doesn't show {allergen} in what you ordered. We do cook everything in one kitchen, so there is some risk of cross-contact."]))
    c.hear(rng.choice(["Okay, thanks for checking.", "Got it, let's go ahead.", "Alright, that works."]))
    return True


def deliver(c: Call, r: dict[str, Any], items: list[str], rates: dict[str, float]) -> None:
    rng = c.rng
    c.say(rng.choice(["Sure. What's the full delivery address?", "Happy to. What address should we deliver to?"]))
    address = f"{rng.randint(12, 980)} {rng.choice(STREETS)}{rng.choice(['', ', apartment ' + str(rng.randint(1, 9)) + rng.choice('ABC')])}"
    c.hear(f"It's {address}.")
    total, eta = money(rng, len(items)), rng.choice([30, 35, 40, 45, 50])
    readback = f"That's {' and '.join(items)}, {total} with the delivery fee."
    if rng.random() < rates["no_zone"]:
        c.modes.append("no_zone")
        if rng.random() < rates["eta_no_zone"]:
            c.modes += ["eta", "eta_early"]
            c.say(f"Got it, {address}. {readback} It'll be there in about {eta} minutes.")
        else:
            c.say(f"Got it, {address}. {readback}")
        return
    if rng.random() < rates["late_zone"]:  # the read-back before the zone check: step 3 before step 2
        c.modes.append("late_zone")
        c.say(f"Got it, {address}. {readback} Let me check that address.")
        c.tool("check_delivery_zone", {"address": address}, {"in_zone": True, "estimated_minutes": eta})
        c.say("You're inside our delivery zone. We'll text you when the driver leaves.")
        return
    c.tool("check_delivery_zone", {"address": address}, {"in_zone": True, "estimated_minutes": eta})
    if rng.random() < rates["eta_clean"]:
        c.modes.append("eta")
        c.say(f"You're inside our delivery zone. {readback} The driver should be there in about {eta} minutes.")
    else:
        c.say(f"You're inside our delivery zone. {readback} We'll text you when the driver leaves.")


def finish(c: Call, r: dict[str, Any], items: list[str], caller: str, delivery: bool) -> None:
    rng = c.rng
    c.hear(rng.choice(["Sounds good.", "Perfect.", "Great, thanks."]))
    c.say("Can I get a name and phone number for the order?")
    c.hear(f"{caller}, 555-01{rng.randint(10, 99)}.")
    c.tool("place_order", {"items": [f"1x {i}" for i in items], "fulfillment": "delivery" if delivery else "pickup"}, {"order_id": f"A-{rng.randint(1000, 9999)}", "ready_in_minutes": 20})
    c.say(f"Thanks, {caller}. Your order is in{'' if delivery else ' and will be ready at the counter'}. Have a good night!")


def reservation(c: Call, r: dict[str, Any], caller: str, rates: dict[str, float]) -> None:
    rng = c.rng
    c.procedures.append("reservations")
    want = pick(rng, {"book": 0.6, "change": 0.2, "cancel": 0.2})  # step 2's OR options: one applies per call
    if want != "book":
        change_or_cancel(c, caller, want, rates)
        return
    large = rng.random() < rates["large"]
    n = rng.choice([9, 10, 12, 14]) if large else rng.choice([2, 2, 3, 4, 4, 5, 6])
    day, at = rng.choice(DAYS), rng.choice(TIMES)
    c.hear(rng.choice([f"Hi, I'd like to book a table for {n} on {day} at {at}.", f"Can I make a reservation? It's a table for {n}, {day}, around {at}."]))
    c.say("Let me check that for you.")
    c.tool("check_reservations", {"party_size": n, "date": day, "time": at}, {"available": True, "alternatives": []})
    if large and rng.random() >= rates["large_party"]:
        c.say(f"We do have room on {day} at {at}. For parties over eight, {r['staff']} confirms the booking, so let me transfer you.")
        c.tool("transfer_to_staff", {"reason": f"booking for {n}"}, {"status": "transferring", "eta_seconds": 20})
        c.hear("Sure, thanks.")
        return
    if large:
        c.modes.append("large_party")
    c.say(f"Good news, we have a table for {n} on {day} at {at}. What name should I put it under?")
    c.hear(f"{caller}.")
    c.say(f"You're all set, {caller}: a table for {n}, {day} at {at}. See you then!")


def change_or_cancel(c: Call, caller: str, want: str, rates: dict[str, float]) -> None:
    rng = c.rng
    n, day, at, later = rng.choice([2, 3, 4, 5, 6]), rng.choice(DAYS), rng.choice(TIMES[:3]), rng.choice(TIMES[3:])
    booking = {"name": caller, "date": day}
    if want == "change":
        c.hear(rng.choice([f"Hi, I've got a table booked for {n} on {day} under {caller}. Could we move it to {later}?",
                           f"Can I change my reservation? It's {caller}, {day} at {at}, and we'd like {later} instead."]))
        c.say("Let me move that for you.")
        c.tool("change_reservation", {**booking, "time": later}, {"status": "changed", "date": day, "time": later})
        c.say(f"Done, {caller}: your table for {n} is now {day} at {later}.")
        return
    c.hear(rng.choice([f"Hi, I need to cancel my reservation for {day}. It's under {caller}.",
                       f"I'd like to cancel our table on {day} at {at}, the name is {caller}."]))
    if rng.random() < rates["wrong_option"]:
        c.modes.append("wrong_option")
        c.say("Let me take care of that.")
        c.tool("change_reservation", {**booking, "date": "next " + day}, {"status": "changed", "date": "next " + day, "time": at})
        c.say(f"I've moved your table to next {day} at {at} instead, {caller}.")
        return
    c.say("Let me cancel that for you.")
    c.tool("cancel_reservation", booking, {"status": "cancelled"})
    c.say(f"Your table for {day} is cancelled, {caller}. Hope to see you another time.")


def jobs(seed: int, count: int, kind: str, anchor: date | None) -> list[dict[str, Any]]:
    rng = random.Random(f"{seed}:{kind}")
    out = []
    for i in range(count):
        agent = pick(rng, AGENT_WEIGHTS)
        call = generate_call(rng, agent, RATES[kind])
        job_id = f"AJ_{kind[:2]}{seed}{i:04d}{''.join(rng.choices('abcdefghjkmnpqrstuvwxyz23456789', k=6))}"
        job: dict[str, Any] = {"job_id": job_id, "agent_name": agent, "room": f"call-{agent}-{job_id[-6:]}", "items": call.items,
                               "procedures": call.procedures, "modes": call.modes}
        if anchor is not None:
            day = anchor - timedelta(days=14) + timedelta(days=i * 14 / max(count, 1))
            start = datetime.combine(day, time(11, 0), tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 600))
            job["started_at"] = start.isoformat()
            job["release"] = 1
        out.append(job)
    return out


def expected(jobs_: list[dict[str, Any]], release: int) -> dict[str, dict[str, dict[str, Any]]]:
    """{procedure/rule: {mode: {count, sessions}}} for the modes a check against `release`'s rules should find."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for j in jobs_:
        for m in dict.fromkeys(j["modes"]):
            spec = MODES[m]
            if spec.get("releases") and str(release) not in spec["releases"].split(","):
                continue
            slot = out.setdefault(f"{spec['procedure']}/{spec['rule']}", {}).setdefault(m, {"count": 0, "sessions": []})
            slot["count"] += 1
            slot["sessions"].append(j["job_id"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--backfill", type=int, default=200)
    parser.add_argument("--new", type=int, default=100)
    parser.add_argument("--anchor", type=date.fromisoformat, default=datetime.now(timezone.utc).date(), help="The backfill covers the 14 days before it.")
    args = parser.parse_args()

    backfill = jobs(args.seed, args.backfill, "backfill", args.anchor)
    new = jobs(args.seed, args.new, "new", None)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("backfill.jsonl", backfill), ("new.jsonl", new)):
        with open(OUT / name, "w") as f:
            for row in rows:
                f.write(json.dumps({k: v for k, v in row.items() if k not in ("modes", "procedures")}) + "\n")
    truth = {
        "seed": args.seed,
        "modes": MODES,
        "sessions": {j["job_id"]: {"set": kind, "agent": j["agent_name"], "procedures": j["procedures"], "modes": list(dict.fromkeys(j["modes"]))}
                     for kind, rows in (("backfill", backfill), ("new", new)) for j in rows},
        "expected": {"backfill": expected(backfill, 1), "new": expected(new, 2)},
    }
    save("ground_truth.json", truth)
    clean = sum(1 for j in backfill + new if not j["modes"])
    print(f"{len(backfill)} backfill and {len(new)} new calls ({clean} with nothing planted) in {OUT}")
    for kind in ("backfill", "new"):
        for rule, modes in sorted(truth["expected"][kind].items()):
            print(f"  {kind:8} {rule:28} " + ", ".join(f"{m} {v['count']}" for m, v in modes.items()))


if __name__ == "__main__":
    main()
