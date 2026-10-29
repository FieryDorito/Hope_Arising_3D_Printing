"""Read the adjustable settings out of an OpenSCAD file (the "Customizer" format).

Designers mark settings like this at the top of a .scad file:

    /* [Hinge Parameters] */
    // Leaf thickness. Values greater than 3mm recommended.
    leaf_gauge = 5.0;
    knuckle_count = 7;            // [3:2:31]        -> slider min:step:max
    enable_pin = 1;               // [0:No, 1:Yes]   -> drop-down
    fastener_column_count = 2;    // [1,2]           -> drop-down

Only plain numbers are offered to operators (no text, no formulas). Settings in a
"Hidden" group and quality settings like resolution/$fn are left alone.
Settings in part.yaml (params:) always win over what's read here.
"""
from __future__ import annotations

import re
from pathlib import Path

ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*;\s*(?://\s*(.*))?$")
GROUP = re.compile(r"/\*\s*\[([^\]]+)\]\s*\*/")
STOP = re.compile(r"^\s*(module|function)\s+\w+")
SKIP_NAMES = {"resolution", "fn", "fa", "fs", "quality", "detail", "facets", "segments"}
SIZE_WORDS = ("width", "height", "length", "diameter", "radius", "thick", "gauge", "size", "wall")
NOT_BASIC = ("counter", "sink", "margin", "clearance", "offset", "depth", "fillet", "chamfer")


def _label(name: str) -> str:
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _unit(name: str, is_choice: bool) -> str:
    n = name.lower()
    words = set(n.split("_"))
    if is_choice or words & {"count", "number", "type", "enable", "shape", "flip", "mode"}:
        return ""
    if "angle" in n or "deg" in n:
        return "°"
    return "mm"


def _choices(ann: str):
    """'0:No, 1:Yes' -> [(0,'No'),(1,'Yes')];  '1,2' -> [(1,'1'),(2,'2')]."""
    out = []
    for item in ann.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            v, lab = item.split(":", 1)
        else:
            v, lab = item, item
        try:
            out.append((float(v.strip()), lab.strip()))
        except ValueError:
            return None
    return out or None


def read_params(scad_file: Path) -> list[dict]:
    """Return ParamSpec-style dicts for the file's customizer settings."""
    try:
        lines = scad_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    params, group, comment = [], "", ""
    in_block_comment = False
    for line in lines:
        if STOP.match(line):
            break
        g = GROUP.search(line)
        if g:
            group, comment = g.group(1).strip(), ""
            continue
        stripped = line.strip()
        if in_block_comment:
            in_block_comment = "*/" not in stripped
            continue
        if stripped.startswith("/*"):
            in_block_comment = "*/" not in stripped
            continue
        if stripped.startswith("//"):
            comment = stripped.lstrip("/").strip()
            continue
        m = ASSIGN.match(line)
        if not m:
            if stripped:
                comment = ""
            continue
        name, value, ann = m.group(1), float(m.group(2)), (m.group(3) or "").strip()
        help_text, comment = comment, ""
        if group.lower() == "hidden" or name.lower().lstrip("$") in SKIP_NAMES:
            continue

        spec = {"name": name, "label": _label(name), "default": value, "help": help_text,
                "group": group, "kind": "number"}
        rng = re.fullmatch(r"\[\s*(-?[\d.]+)\s*:\s*(-?[\d.]+)\s*(?::\s*(-?[\d.]+)\s*)?\]", ann.split("//")[0].strip()) if ann else None
        if rng:
            a, b, c = rng.group(1), rng.group(2), rng.group(3)
            lo, hi, step = (float(a), float(c), float(b)) if c else (float(a), float(b), 1.0)
            spec.update(min=lo, max=hi, step=step, kind="int" if step.is_integer() and value.is_integer() else "number")
        elif ann.startswith("["):
            ch = _choices(ann.strip("[] ").split("]")[0])
            if not ch:
                continue
            spec.update(kind="choice", choices=[[v, lab] for v, lab in ch],
                        min=min(v for v, _ in ch), max=max(v for v, _ in ch), step=1.0)
        else:
            # No range given: allow 10% .. 5x of the default (the bed-size check still applies).
            if value == 0:
                continue
            lo, hi = sorted((round(value * 0.1, 3), round(value * 5, 3)))
            spec.update(min=lo, max=hi, step=1.0 if value.is_integer() and abs(value) >= 5 else 0.1)
        spec["unit"] = _unit(name, spec["kind"] == "choice")
        spec["basic"] = spec["kind"] in ("number", "int") and spec["unit"] == "mm" and \
            any(w in name.lower() for w in SIZE_WORDS) and not any(w in name.lower() for w in NOT_BASIC)
        params.append(spec)
    return params
