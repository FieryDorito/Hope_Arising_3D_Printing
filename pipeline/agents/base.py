"""Common interface for AI agents.

An agent ONLY chooses a part and fills in numbers. It never creates geometry, and
everything it returns goes through validate.check_request() before use.
To add a provider: subclass Agent, implement suggest(), register it in agents/__init__.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..config import settings
from ..library import Part


class AgentError(Exception):
    """AI step unavailable or failed. Message is shown to the operator."""


@dataclass
class Suggestion:
    part_id: str | None              # None = nothing in the library fits
    params: dict = field(default_factory=dict)
    material: str | None = None
    strength: str | None = None
    explanation: str = ""            # one or two plain sentences for the operator
    question: str = ""               # what to ask the customer, if the request is unclear
    options: list[str] = field(default_factory=list)  # labels of the files/pieces to print
    raw: dict = field(default_factory=dict)


class Agent:
    name = "base"

    def suggest(self, request: str, parts: list[Part]) -> Suggestion:
        raise NotImplementedError


SYSTEM_PROMPT = """You help a small 3D-printing shop in Addis Ababa, Ethiopia.
A shop worker types what a customer needs. You choose the single best part from the
shop's library and fill in its settings. You never invent parts that are not in the list.

Rules:
- Only use part_id values from the library list.
- Only use the settings listed for that part, inside their min-max range.
- For parts with a "Size %" setting, compute it from the part's base size (mm) and the
  size the customer asked for. 100 = original size.
- If nothing in the library fits, set part_id to null and explain briefly.
- If the request is too unclear to choose sizes, still pick the closest part, keep
  default sizes, and write one short question the worker should ask the customer.
- Choose material and strength using the notes given.
- Some parts have several files ("options"): pieces of one product or different versions.
  Return the labels of the ones to print in "options" (leave empty to use the defaults).
- Write the explanation in short, simple English (one or two sentences)."""


def library_brief(parts: list[Part]) -> str:
    """Compact JSON description of printable parts for the prompt."""
    s = settings()
    items = []
    for p in parts:
        if not p.printable:
            continue
        item = {"part_id": p.part_id, "name": p.name, "category": p.category}
        if p.description:
            item["description"] = p.description
        if p.base_size_mm:
            item["base_size_mm_xyz"] = p.base_size_mm
        if len(p.files) > 1:
            item["options"] = {
                "choose": "exactly one" if p.choose == "one" else "any combination",
                "list": [{"label": f.label, **({"size_mm_xyz": f.size_mm} if f.size_mm else {}),
                          **({"default": True} if f.default else {})} for f in p.files],
            }
        item["settings"] = [
            {k: v for k, v in sp.to_dict().items() if k in ("name", "label", "unit", "min", "max", "default", "help") and v != ""}
            for sp in p.params
        ]
        items.append(item)
    return json.dumps({
        "library": items,
        "materials": {k: v.get("note", "") for k, v in s["materials"].items()},
        "strength": {k: v.get("note", "") for k, v in s["strength"].items()},
        "printer_bed_mm": [s["printer"]["bed_x_mm"], s["printer"]["bed_y_mm"], s["printer"]["max_z_mm"]],
    }, indent=1)
