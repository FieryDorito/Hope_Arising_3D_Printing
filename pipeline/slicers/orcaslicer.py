"""OrcaSlicer command-line backend — EXPERIMENTAL, NOT YET TESTED.

OrcaSlicer uses JSON profiles, not the .ini files in config/. Before switching
`slicer.backend` to orcaslicer:
  1. In OrcaSlicer, set up the Ender 3 + filament + process exactly as the .ini files do.
  2. Export them as JSON into config/orca/ (machine.json, process_<Strength>.json,
     filament_<Material>.json).
  3. Run `pytest tests/test_slicer.py` on a computer with OrcaSlicer installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..config import path
from .base import SliceProfiles, SliceResult, Slicer, SlicerError, as_list, read_stats


class OrcaSlicer(Slicer):
    name = "orcaslicer"

    def __init__(self, executable: str = "", timeout_s: int = 600):
        self.exe = executable or shutil.which("orca-slicer") or shutil.which("OrcaSlicer")
        if not self.exe:
            raise SlicerError("OrcaSlicer is not installed on this computer.")
        self.timeout_s = timeout_s

    def slice(self, stls: Path | list[Path], out_gcode: Path, profiles: SliceProfiles,
              bed_center: tuple[float, float]) -> SliceResult:
        d = path("config/orca")
        machine = d / "machine.json"
        process = d / f"process_{profiles.strength_name}.json"
        filament = d / f"filament_{profiles.material_name}.json"
        missing = [p.name for p in (machine, process, filament) if not p.exists()]
        if missing:
            raise SlicerError(f"OrcaSlicer profiles missing in config/orca/: {', '.join(missing)}")

        out_dir = out_gcode.parent
        cmd = [self.exe, "--slice", "0",
               "--load-settings", f"{machine};{process}",
               "--load-filaments", str(filament),
               "--outputdir", str(out_dir), *[str(p) for p in as_list(stls)]]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
        produced = sorted(out_dir.glob("*.gcode"), key=lambda p: p.stat().st_mtime)
        if r.returncode != 0 or not produced:
            raise SlicerError(f"OrcaSlicer failed.\n{(r.stderr or r.stdout)[-600:]}")
        produced[-1].replace(out_gcode)
        t, g, m = read_stats(out_gcode)
        return SliceResult(out_gcode, t, g, m)
