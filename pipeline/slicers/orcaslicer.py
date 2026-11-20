"""OrcaSlicer command-line slicing - used for every printer that has `orca_profiles`.

OrcaSlicer knows most printers (Bambu Lab, Creality, Prusa, Elegoo, Anycubic, Voron...).
The app arranges the plate as a .3mf, OrcaSlicer slices it with that printer's profiles
(made by tools/make_slicer_profiles.py into config/orca/<printer>/) and writes a sliced
.gcode.3mf. For Bambu Lab printers that file is what the printer prints; for other printers
the app takes the plain G-code out of it.

Finding OrcaSlicer: `slicer.orca` in settings.local.yaml if set, otherwise the normal
install places on Windows, Mac and Linux (incl. AppImage and Flatpak).
"""
from __future__ import annotations

import copy
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .base import SliceResult, SlicerError

FLATPAK_ID = "io.github.softfever.OrcaSlicer"

# The OrcaSlicer command line's own error numbers (src/libslic3r/Utils.hpp), in plain words.
CLI_ERRORS = {
    -5: "the printer settings files could not be read",
    -17: "the print settings don't belong to this printer",
    -50: "there is nothing on the plate that can be printed",
    -51: "the settings don't work together",
    -52: "a piece is partly off the bed",
    -100: "slicing failed",
    -101: "the pieces are too close together (their paths run into each other). "
          "Print fewer copies at once",
    -102: "part of the print is outside the area the printer can reach",
}


def _candidates() -> list[list[str]]:
    out: list[list[str]] = []
    for name in ("orca-slicer", "OrcaSlicer", "orcaslicer"):
        if exe := shutil.which(name):
            out.append([exe])
    if os.name == "nt":
        for base in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                                  os.environ.get("LOCALAPPDATA")]):
            for sub in ("OrcaSlicer", r"Programs\OrcaSlicer"):
                p = Path(base) / sub / "orca-slicer.exe"
                if p.exists():
                    out.append([str(p)])
    else:
        mac = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
        if mac.exists():
            out.append([str(mac)])
        home = Path.home()
        for pat in (str(home / "Applications" / "*Orca*Slicer*.AppImage"),
                    str(home / "Downloads" / "*Orca*Slicer*.AppImage"),
                    "/opt/*[Oo]rca*/*.AppImage"):
            for p in sorted(glob.glob(pat), reverse=True):
                out.append([p, "--appimage-extract-and-run"])
        if shutil.which("flatpak"):
            r = subprocess.run(["flatpak", "info", FLATPAK_ID], capture_output=True, text=True)
            if r.returncode == 0:
                out.append(["flatpak", "run", "--command=orca-slicer", FLATPAK_ID])
    return out


def find_orca(configured: str = "") -> list[str]:
    """The command to run OrcaSlicer, as a list. Raises SlicerError if it isn't installed."""
    if configured:
        parts = [configured] if Path(configured).exists() else configured.split()
        if Path(parts[0]).exists() or shutil.which(parts[0]):
            if parts[0].endswith(".AppImage") and len(parts) == 1:
                parts.append("--appimage-extract-and-run")
            return parts
        raise SlicerError(f"OrcaSlicer not found at {configured}. Check settings.local.yaml.")
    found = _candidates()
    if not found:
        raise SlicerError("OrcaSlicer is not installed on this computer.")
    return found[0]


def read_3mf_stats(gcode_3mf: Path) -> tuple[int | None, float | None, float | None]:
    """Print time (s), plastic (g) and (m) from a sliced file's Metadata/slice_info.config."""
    try:
        with zipfile.ZipFile(gcode_3mf) as z:
            info = z.read("Metadata/slice_info.config").decode("utf-8", "ignore")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None, None, None

    def meta(key):
        m = re.search(rf'key="{key}"\s+value="([\d.]+)"', info)
        return float(m.group(1)) if m else None

    t, g = meta("prediction"), meta("weight")
    used_m = [float(x) for x in re.findall(r'used_m="([\d.]+)"', info)]
    if not g:
        used_g = [float(x) for x in re.findall(r'used_g="([\d.]+)"', info)]
        g = sum(used_g) if used_g else g
    return (int(t) if t else None, g or None, sum(used_m) if used_m else None)


def plate_gcode_name(gcode_3mf: Path) -> str | None:
    try:
        with zipfile.ZipFile(gcode_3mf) as z:
            names = sorted(n for n in z.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n))
    except (zipfile.BadZipFile, OSError):
        return None
    return names[0] if names else None


def extract_gcode(gcode_3mf: Path, out: Path) -> Path:
    """Take the plain G-code out of a sliced .gcode.3mf (for printers that print .gcode)."""
    name = plate_gcode_name(gcode_3mf)
    if not name:
        raise SlicerError("OrcaSlicer made a file without G-code in it.")
    with zipfile.ZipFile(gcode_3mf) as z, open(out, "wb") as fh:
        shutil.copyfileobj(z.open(name), fh)
    return out


def _with_overrides(src: Path, overrides: dict, out: Path) -> Path:
    d = json.loads(src.read_text(encoding="utf-8"))
    d2 = copy.deepcopy(d)
    for k, v in (overrides or {}).items():
        # Per-extruder values are lists; keep the same shape.
        d2[k] = [str(v)] * len(d[k]) if isinstance(d.get(k), list) else str(v)
    out.write_text(json.dumps(d2, indent=1), encoding="utf-8")
    return out


class OrcaSlicer:
    name = "orcaslicer"

    def __init__(self, executable: str = "", timeout_s: int = 600):
        self.cmd = find_orca(executable)
        self.timeout_s = timeout_s

    def slice(self, plate_3mf: Path, out: Path, profiles_dir: Path, material: str,
              process_overrides: dict | None = None) -> SliceResult:
        """Slice plate_3mf into out (a .gcode.3mf)."""
        machine = profiles_dir / "machine.json"
        process = profiles_dir / "process.json"
        filament = profiles_dir / f"filament_{material}.json"
        missing = [p.name for p in (machine, process, filament) if not p.exists()]
        if missing:
            raise SlicerError(f"OrcaSlicer profiles missing in {profiles_dir}: {', '.join(missing)}. "
                              "Make them with tools/make_slicer_profiles.py.")
        out.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="orca_", dir=out.parent))
        proc = _with_overrides(process, process_overrides or {}, work / "process.json")
        result = work / "result.gcode.3mf"
        cmd = [*self.cmd,
               "--slice", "0",
               "--arrange", "0",
               "--load-settings", f"{machine};{proc}",
               "--load-filaments", str(filament),
               "--outputdir", str(work),
               "--export-3mf", result.name,
               str(plate_3mf)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s, cwd=work,
                               env={**os.environ})
        except subprocess.TimeoutExpired:
            raise SlicerError("OrcaSlicer took too long and was stopped.")
        except OSError as e:
            raise SlicerError(f"Could not start OrcaSlicer: {e}")
        (out.parent / (out.name.split(".")[0] + ".orca.log")).write_text(
            " ".join(cmd) + f"\n\nexit {r.returncode}\n\n" + r.stdout + "\n" + r.stderr, encoding="utf-8")
        if not result.exists():
            alt = sorted(work.glob("*.3mf"))
            result = alt[0] if alt else result
        if r.returncode != 0 or not result.exists() or not plate_gcode_name(result):
            tail = (r.stderr or r.stdout)[-600:]
            code = r.returncode - 256 if r.returncode > 127 else r.returncode
            why = CLI_ERRORS.get(code, f"exit {r.returncode}")
            raise SlicerError(f"OrcaSlicer could not slice this part: {why}\n{tail}")
        shutil.move(str(result), out)
        shutil.rmtree(work, ignore_errors=True)
        t, g, m = read_3mf_stats(out)
        return SliceResult(out, t, g, m)
