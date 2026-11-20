"""Make the full ("flattened") OrcaSlicer profiles the app slices a printer with.

OrcaSlicer's own profiles are split into layers: a printer profile `inherits` a common one,
which inherits another, and long G-code is kept in separate `include` templates. The
OrcaSlicer command line needs ONE complete file per printer / process / filament, so this
script merges the layers. It works for any printer OrcaSlicer knows (Bambu Lab, Creality,
Prusa, Elegoo, Anycubic, Voron, ...).

Example (the two printers that ship with the app):

    python tools/make_slicer_profiles.py PROFILES config/orca/p2s --vendor BBL \\
        --machine "Bambu Lab P2S 0.4 nozzle" --process "0.20mm Standard @BBL P2S" \\
        --filament PLA="Generic PLA @BBL P2S" --filament PETG="Generic PETG @BBL P2S"

    python tools/make_slicer_profiles.py PROFILES config/orca/ender3 --vendor Creality \\
        --machine "Creality Ender-3 0.4 nozzle" --process "0.20mm Standard @Creality Ender3" \\
        --filament PLA="Generic PLA @Creality" --filament PETG="Generic PETG @Creality"

PROFILES is OrcaSlicer's `resources/profiles` folder, for example
  Windows: C:\\Program Files\\OrcaSlicer\\resources\\profiles
  Mac:     /Applications/OrcaSlicer.app/Contents/Resources/profiles
  Source:  https://github.com/OrcaSlicer/OrcaSlicer  (resources/profiles)
The names to use are the ones OrcaSlicer shows; the files are in PROFILES/<vendor>/machine,
process and filament. The material names (PLA, PETG) must match `materials:` in
config/settings.yaml.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DROP = {"inherits", "include", "instantiation"}
LIBRARY = "OrcaFilamentLibrary"         # shared filament profiles other vendors build on


class ProfileError(Exception):
    pass


class Profiles:
    def __init__(self, root: Path, vendor: str):
        self.root, self.vendor = root, vendor
        if not (root / vendor).is_dir():
            raise ProfileError(f"No vendor folder {root / vendor}")

    def _find(self, kind: str, name: str) -> Path:
        for folder in [self.root / self.vendor / kind, self.root / LIBRARY / kind]:
            hits = list(folder.rglob(f"{name}.json")) if folder.is_dir() else []
            if hits:
                return hits[0]
        raise ProfileError(f"Profile not found: {kind}/{name} (looked in {self.vendor} and {LIBRARY})")

    def load(self, kind: str, name: str) -> dict:
        return json.loads(self._find(kind, name).read_text(encoding="utf-8"))

    def flatten(self, kind: str, name: str, _seen: tuple = ()) -> dict:
        """Merge a profile with everything it inherits and includes (own values win)."""
        if name in _seen:
            raise ProfileError(f"Profile loop: {' -> '.join(_seen + (name,))}")
        d = self.load(kind, name)
        out: dict = {}
        if d.get("inherits"):
            out.update(self.flatten(kind, d["inherits"], _seen + (name,)))
        for inc in _includes(d):
            out.update({k: v for k, v in self.flatten(kind, inc, _seen + (name,)).items() if k != "name"})
        out.update(d)
        for k in DROP:
            out.pop(k, None)
        return out


def _includes(d: dict) -> list[str]:
    inc = d.get("include") or []
    if isinstance(inc, str):                 # some files store the list as text
        inc = [s.strip(" '\"") for s in inc.strip("[]").split(",") if s.strip(" '\"")]
    return list(inc)


def flatten(folder: Path, name: str) -> dict:
    """Flatten a profile inside one kind folder (e.g. .../BBL/machine). Kept for simple use."""
    root, vendor, kind = folder.parent.parent, folder.parent.name, folder.name
    return Profiles(root, vendor).flatten(kind, name)


def _compatible(d: dict, machine: str) -> bool:
    compat = d.get("compatible_printers") or []
    return not compat or machine in compat


def make(root: Path, out: Path, machine: str, process: str, filaments: dict[str, str],
         vendor: str | None = None) -> list[Path]:
    """root = resources/profiles (with vendor) or a vendor folder itself (vendor=None)."""
    if vendor is None:
        root, vendor = root.parent, root.name
    prof = Profiles(root, vendor)
    version = ""
    meta = root / f"{vendor}.json"
    if meta.exists():
        version = json.loads(meta.read_text(encoding="utf-8")).get("version", "")
    out.mkdir(parents=True, exist_ok=True)
    jobs = [("machine", machine, "machine.json"), ("process", process, "process.json")]
    jobs += [("filament", fil, f"filament_{mat}.json") for mat, fil in filaments.items()]
    written = []
    for kind, name, fname in jobs:
        d = prof.flatten(kind, name)
        d.update({"type": kind, "name": name, "from": "system"})
        if version:
            d["version"] = version
        if kind != "machine" and not _compatible(d, machine):
            raise ProfileError(f"{name} is not made for {machine} (it lists {d['compatible_printers']}).")
        p = out / fname
        p.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(p)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profiles_dir", type=Path, help="OrcaSlicer resources/profiles")
    ap.add_argument("out_dir", type=Path, help="e.g. config/orca/p2s")
    ap.add_argument("--vendor", required=True, help="vendor folder, e.g. BBL, Creality, Prusa")
    ap.add_argument("--machine", required=True)
    ap.add_argument("--process", required=True)
    ap.add_argument("--filament", action="append", default=[], metavar="MATERIAL=PROFILE", required=True)
    a = ap.parse_args(argv)
    fil = dict(f.split("=", 1) for f in a.filament)
    try:
        for p in make(a.profiles_dir, a.out_dir, a.machine, a.process, fil, a.vendor):
            print("wrote", p)
    except ProfileError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
