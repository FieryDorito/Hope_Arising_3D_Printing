"""Safety checks between the AI (or operator) and the CAD/slicer stages.

Nothing reaches the printer unless it passes here. Errors block the job;
warnings are shown but don't block. All messages are written for operators.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import settings
from .library import Part

MAX_COPIES = 50


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # cleaned values
    files: dict = field(default_factory=dict)   # rel path -> how many

    @property
    def ok(self) -> bool:
        return not self.errors


def check_request(part: Part | None, params: dict, material: str, strength: str,
                  files: dict[str, int] | None = None) -> Check:
    s = settings()
    c = Check()
    if part is None:
        c.errors.append("No part selected.")
        return c
    if not part.commercial_ok:
        c.errors.append(f"{part.name} cannot be sold — its license does not allow commercial use.")
    if not part.files:
        c.errors.append(f"{part.name} has no 3D model file yet. Ask a maintainer to add it.")
    if material not in s["materials"]:
        c.errors.append(f"Unknown material '{material}'.")
    if strength not in s["strength"]:
        c.errors.append(f"Unknown strength '{strength}'.")

    for spec in part.params:
        raw = params.get(spec.name, spec.default)
        try:
            val = bool(raw) if spec.kind == "bool" else float(raw)
        except (TypeError, ValueError):
            c.errors.append(f"{spec.label} must be a number.")
            continue
        if spec.kind == "choice":
            if not any(float(v) == val for v, _ in (spec.choices or [])):
                c.errors.append(f"{spec.label}: choose one of the listed options.")
        elif spec.kind != "bool":
            if spec.kind == "int":
                val = int(round(val))
            if not (spec.min <= val <= spec.max):
                c.errors.append(f"{spec.label} must be between {spec.min:g} and {spec.max:g} {spec.unit}.")
        c.params[spec.name] = val

    unknown = set(params) - {p.name for p in part.params}
    if unknown:
        c.warnings.append(f"Ignored settings this part doesn't have: {', '.join(sorted(unknown))}.")

    # Which files (pieces/options) and how many of each.
    chosen = files if files else part.default_selection()
    for rel, n in chosen.items():
        mf = part.file(rel)
        if mf is None:
            c.errors.append(f"'{rel}' is not one of this part's files.")
            continue
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        if n < 0 or n > MAX_COPIES:
            c.errors.append(f"How many '{mf.label}': choose 0 to {MAX_COPIES}.")
        elif n > 0:
            c.files[rel] = n
    if part.files and not c.files and not c.errors:
        c.errors.append("Choose at least one piece to print.")
    if part.choose == "one" and len(c.files) > 1:
        c.errors.append("This part's options are alternatives — choose only one.")

    # Size check before CAD, for mesh files where we already know the size.
    k = c.params.get("scale_percent", 100) / 100
    for rel in c.files:
        mf = part.file(rel)
        if mf and mf.kind == "mesh" and mf.size_mm:
            c.errors += [f"{mf.label}: {e}" if len(c.files) > 1 else e
                         for e in check_fits([d * k for d in mf.size_mm])]
    return c


def bed_area() -> tuple[float, float]:
    p = settings()["printer"]
    m = 2 * p["margin_mm"]
    return p["bed_x_mm"] - m, p["bed_y_mm"] - m


def fit_angle(x: float, y: float) -> float | None:
    """Angle (degrees) at which an x-by-y footprint fits the bed; 0 if it fits as is; None if never."""
    W, D = bed_area()
    if (x <= W and y <= D) or (y <= W and x <= D):
        return 0.0
    for deg in range(5, 90, 5):  # turn it diagonally (a box's corners are the worst case)
        a = math.radians(deg)
        if x * math.cos(a) + y * math.sin(a) <= W and x * math.sin(a) + y * math.cos(a) <= D:
            return float(deg)
    return None


def check_fits(size_mm) -> list[str]:
    p = settings()["printer"]
    x, y, z = size_mm
    W, D = bed_area()
    errs = []
    if fit_angle(x, y) is None:
        errs.append(f"Part is too big for the printer bed ({x:.0f} x {y:.0f} mm; "
                    f"max {W:.0f} x {D:.0f} mm). Make it smaller.")
    if z > p["max_z_mm"]:
        errs.append(f"Part is too tall ({z:.0f} mm; max {p['max_z_mm']} mm). Make it smaller.")
    return errs
