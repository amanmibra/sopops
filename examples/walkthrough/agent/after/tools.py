"""Mock versions of the tools the instructions name.

A real deployment would call the POS, menu and reservation systems. These return canned answers so the example
runs end to end, and log every call so you can see which SOP step triggered it.
"""

import json
import logging
from typing import Any, Literal

from livekit.agents import function_tool

log = logging.getLogger("harbor-lane.tools")

# Pretend allergen data, keyed by words that appear in item names.
ALLERGENS = {
    "pizza": {"contains": ["wheat", "dairy"], "may_contain": ["tree nuts"]},
    "margherita": {"contains": ["wheat", "dairy"], "may_contain": ["tree nuts"]},
    "sushi": {"contains": ["fish", "soy", "sesame"], "may_contain": ["shellfish"]},
    "roll": {"contains": ["fish", "soy", "sesame"], "may_contain": ["shellfish"]},
    "pasta": {"contains": ["wheat", "egg"], "may_contain": ["dairy"]},
}


def _logged(name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    log.info("[tool] %s %s -> %s", name, json.dumps(args), json.dumps(result))
    return result


@function_tool
async def lookup_allergens(item: str, allergen: str | None = None) -> dict[str, Any]:
    """Look up the allergens for a menu item.

    Args:
        item: The menu item, e.g. "margherita pizza".
        allergen: The allergen the caller asked about, if any.
    """
    key = next((k for k in ALLERGENS if k in item.lower()), None)
    if key is None:
        result: dict[str, Any] = {"item": item, "note": "No allergen data for this item. Staff must confirm."}
    else:
        result = {"item": item, **ALLERGENS[key], "note": "Prepared in a shared kitchen; cross-contact is possible."}
    return _logged("lookup_allergens", {"item": item, "allergen": allergen}, result)


@function_tool
async def transfer_to_staff(reason: str) -> dict[str, Any]:
    """Transfer the call to a staff member.

    Args:
        reason: Why the call is being transferred.
    """
    return _logged("transfer_to_staff", {"reason": reason}, {"status": "transferring", "eta_seconds": 20})


@function_tool
async def place_order(items: list[str], fulfillment: Literal["pickup", "delivery"]) -> dict[str, Any]:
    """Place the order once everything is confirmed.

    Args:
        items: Items with quantities, e.g. ["1x large pepperoni", "1x garlic knots"].
        fulfillment: Pickup or delivery.
    """
    return _logged("place_order", {"items": items, "fulfillment": fulfillment}, {"order_id": "A-1042", "ready_in_minutes": 20})


@function_tool
async def check_delivery_zone(address: str) -> dict[str, Any]:
    """Check whether an address is inside the delivery zone.

    Args:
        address: The full delivery address.
    """
    return _logged("check_delivery_zone", {"address": address}, {"in_zone": "staten island" not in address.lower(), "estimated_minutes": 40})


@function_tool
async def check_reservations(party_size: int, date: str, time: str) -> dict[str, Any]:
    """Check table availability.

    Args:
        party_size: How many people.
        date: The date, e.g. "Saturday" or "2026-10-24".
        time: The time, e.g. "7pm".
    """
    args = {"party_size": party_size, "date": date, "time": time}
    return _logged("check_reservations", args, {"available": party_size <= 6, "alternatives": ["7:30pm", "9:00pm"]})


TOOLS = [lookup_allergens, transfer_to_staff, place_order, check_delivery_zone, check_reservations]
