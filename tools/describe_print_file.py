"""Print a short summary of sliced files (.gcode or .gcode.3mf): what's inside, temperatures,
where the nozzle prints, time and plastic. Used by CI to show what OrcaSlicer made.

    python tools/describe_print_file.py FILE [FILE ...]
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path


def gcode_of(f: Path) -> tuple[str, list[str], str]:
    if f.name.endswith(".3mf"):
        z = zipfile.ZipFile(f)
        names = z.namelist()
        g = next(n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n))
        info = z.read("Metadata/slice_info.config").decode(errors="ignore") if "Metadata/slice_info.config" in names else ""
        return z.read(g).decode(errors="ignore"), names, info
    return f.read_text(errors="ignore"), [], ""


def describe(f: Path) -> None:
    text, names, info = gcode_of(f)
    lines = text.splitlines()
    xs, ys, zs = [], [], []
    z = 0.0
    for ln in lines:
        if ln.startswith(("G1", "G2", "G3")):
            if m := re.search(r"Z([-\d.]+)", ln):
                z = float(m.group(1))
            if " E" in ln and not re.search(r" E-", ln):
                mx, my = re.search(r"X([-\d.]+)", ln), re.search(r"Y([-\d.]+)", ln)
                if mx and my:
                    xs.append(float(mx.group(1))); ys.append(float(my.group(1))); zs.append(z)
    temps = sorted(set(re.findall(r"^M10[49]\b[^;\n]*?\bS(\d+)", text, re.M)))
    beds = sorted(set(re.findall(r"^M1[49]0\b[^;\n]*?\bS(\d+)", text, re.M)))
    print(f"\n=== {f.name}  ({f.stat().st_size // 1024} KB)")
    if names:
        print("  inside:", ", ".join(n for n in names if n.startswith("Metadata/") and not n.endswith(".png"))[:400])
        for key in ("printer_model_id", "nozzle_diameters", "prediction", "weight"):
            m = re.search(rf'key="{key}"\s+value="([^"]*)"', info)
            if m:
                print(f"  {key}: {m.group(1)}")
    print("  first lines:", " | ".join(lines[:8])[:500])
    print("  nozzle temps:", temps, " bed temps:", beds)
    print("  has M1002 (Bambu start code):", "M1002" in text, " G28 (home):", "G28" in text)
    if xs:
        print(f"  prints within X {min(xs):.1f}..{max(xs):.1f}  Y {min(ys):.1f}..{max(ys):.1f}  "
              f"up to Z {max(zs):.2f}  ({len(xs)} extrusion moves)")
    for pat in ("; estimated printing time", "; total filament used", "; filament used \\[g\\]",
                "; model printing time", "; total estimated time", "; sparse_infill_density",
                "; wall_loops", "; support_type", "; enable_support", "; printer_model"):
        hit = next((ln for ln in lines if re.match(pat, ln, re.I)), None)
        if hit:
            print("  ", hit.strip()[:160])


if __name__ == "__main__":
    for a in sys.argv[1:]:
        describe(Path(a))
