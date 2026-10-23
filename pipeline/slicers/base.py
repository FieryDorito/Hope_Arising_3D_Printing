"""Common interface every slicer backend follows.

To add a slicer: subclass Slicer, implement slice(), register it in slicers/__init__.py.
Nothing else in the project needs to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class SlicerError(Exception):
    """Slicing failed. Message is shown to the operator."""


@dataclass
class SliceResult:
    gcode: Path
    print_time_s: int | None
    filament_g: float | None
    filament_m: float | None


@dataclass
class SliceProfiles:
    printer: Path
    material: Path
    strength: Path
    material_name: str = ""
    strength_name: str = ""


class Slicer:
    name = "base"

    def slice(self, stl: Path, out_gcode: Path, profiles: SliceProfiles,
              bed_center: tuple[float, float]) -> SliceResult:
        raise NotImplementedError


# ------------------------------------------------------------------ helpers

_TIME_RE = re.compile(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")


def parse_duration(text: str) -> int | None:
    m = _TIME_RE.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def read_stats(gcode: Path) -> tuple[int | None, float | None, float | None]:
    """Pull print time / filament use from comments PrusaSlicer & OrcaSlicer write."""
    t = g = mm = None
    with open(gcode, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    for line in lines[:400] + lines[-400:]:
        low = line.lower()
        if "estimated printing time" in low and "normal" in low and "=" in line:
            t = parse_duration(line.split("=", 1)[1])
        elif low.startswith("; total estimated time") or low.startswith("; model printing time"):
            t = t or parse_duration(line.split(":", 1)[-1].split(";")[0])
        elif "filament used [g]" in low and "=" in line:
            g = _num(line)
        elif "filament used [mm]" in low and "=" in line:
            v = _num(line)
            mm = v / 1000 if v is not None else None
    return t, g, mm


def _num(line: str) -> float | None:
    try:
        return float(line.split("=", 1)[1].split(",")[0].strip())
    except (ValueError, IndexError):
        return None


def prepend_header(gcode: Path, lines: list[str]) -> None:
    body = gcode.read_text(encoding="utf-8", errors="ignore")
    head = "".join(f"; {ln}\n" for ln in lines)
    gcode.write_text(head + body, encoding="utf-8")
