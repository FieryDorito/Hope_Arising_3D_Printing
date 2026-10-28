"""PrusaSlicer command-line backend (tested with PrusaSlicer 2.7)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import SliceProfiles, SliceResult, Slicer, SlicerError, as_list, read_stats

WINDOWS_GUESSES = [
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
    r"C:\Program Files\PrusaSlicer\prusa-slicer-console.exe",
]


def find_executable(configured: str = "") -> str:
    if configured:
        if Path(configured).exists() or shutil.which(configured):
            return configured
        raise SlicerError(f"PrusaSlicer not found at {configured}. Check settings.local.yaml.")
    for name in ("prusa-slicer-console", "prusa-slicer", "PrusaSlicer"):
        exe = shutil.which(name)
        if exe:
            return exe
    if os.name == "nt":
        for g in WINDOWS_GUESSES:
            if Path(g).exists():
                return g
    raise SlicerError("PrusaSlicer is not installed on this computer.")


class PrusaSlicer(Slicer):
    name = "prusaslicer"

    def __init__(self, executable: str = "", timeout_s: int = 600):
        self.exe = find_executable(executable)
        self.timeout_s = timeout_s

    def slice(self, stls: Path | list[Path], out_gcode: Path, profiles: SliceProfiles,
              bed_center: tuple[float, float]) -> SliceResult:
        out_gcode.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.exe, "--export-gcode",
            "--load", str(profiles.printer),
            "--load", str(profiles.material),
            "--load", str(profiles.strength),
            "--center", f"{bed_center[0]:g},{bed_center[1]:g}",
            "--output", str(out_gcode),
            *[str(p) for p in as_list(stls)],   # several inputs are arranged on the plate
        ]
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout_s, env=env)
        except subprocess.TimeoutExpired:
            raise SlicerError("Slicing took too long and was stopped.")
        out_gcode.with_suffix(".log").write_text(
            " ".join(cmd) + "\n\n" + r.stdout + "\n" + r.stderr, encoding="utf-8")
        if r.returncode != 0 or not out_gcode.exists() or out_gcode.stat().st_size == 0:
            tail = (r.stderr or r.stdout)[-600:]
            raise SlicerError(f"The slicer could not prepare this part.\n{tail}")
        t, g, m = read_stats(out_gcode)
        return SliceResult(out_gcode, t, g, m)
