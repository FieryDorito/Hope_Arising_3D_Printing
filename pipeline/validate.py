"""Safety checks between the AI (or operator) and the CAD/slicer stages.

Nothing reaches the printer unless it passes here. Errors block the job;
warnings are shown but don't block. All messages are written for operators.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import settings
from .library import Part


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)  # cleaned values

    @property
    def ok(self) -> bool:
        return not self.errors


def check_request(part: Part | None, params: dict, material: str, strength: str) -> Check:
    s = settings()
    c = Check()
    if part is None:
        c.errors.append("No part selected.")
        return c
    if not part.commercial_ok:
        c.errors.append(f"{part.name} cannot be sold — its license does not allow commercial use.")
    if part.model_file is None:
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
        if spec.kind != "bool":
            if spec.kind == "int":
                val = int(round(val))
            if not (spec.min <= val <= spec.max):
                c.errors.append(f"{spec.label} must be between {spec.min:g} and {spec.max:g} {spec.unit}.")
        c.params[spec.name] = val

    unknown = set(params) - {p.name for p in part.params}
    if unknown:
        c.warnings.append(f"Ignored settings this part doesn't have: {', '.join(sorted(unknown))}.")

    # Size check before CAD, for mesh parts where we already know the size.
    if part.kind == "mesh" and part.base_size_mm and "scale_percent" in c.params:
        k = c.params["scale_percent"] / 100
        c.errors += check_fits([d * k for d in part.base_size_mm])
    return c


def check_fits(size_mm) -> list[str]:
    p = settings()["printer"]
    x, y, z = size_mm
    m = 2 * p["margin_mm"]
    fx, fy = p["bed_x_mm"] - m, p["bed_y_mm"] - m
    fits_flat = (x <= fx and y <= fy) or (y <= fx and x <= fy)
    errs = []
    if not fits_flat:
        errs.append(f"Part is too big for the printer bed ({x:.0f} x {y:.0f} mm; "
                    f"max {fx:.0f} x {fy:.0f} mm). Make it smaller.")
    if z > p["max_z_mm"]:
        errs.append(f"Part is too tall ({z:.0f} mm; max {p['max_z_mm']} mm). Make it smaller.")
    return errs
