"""Load settings.yaml (+ optional settings.local.yaml overrides).

Every other module gets paths and options from here, so nothing is hard-coded.
"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsError(Exception):
    """A settings file can't be read. Message says which file and line."""


def _read_yaml(f: Path) -> dict:
    try:
        with open(f, encoding="utf-8-sig") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        raise SettingsError(
            f"There is a typing mistake in config/{f.name}{where}. Check the spacing "
            "(use spaces, not tabs) and that text values are in quotes.") from e


def _find_keys(d, trail=""):
    """Yield paths of non-empty api_key values (used to keep keys out of settings.yaml)."""
    if isinstance(d, dict):
        for k, v in d.items():
            here = f"{trail}.{k}" if trail else str(k)
            if k in ("api_key", "access_code") and v and "paste-your" not in str(v) \
                    and not str(v).endswith("...") and not str(v).startswith("from-printer"):
                yield here
            else:
                yield from _find_keys(v, here)


@lru_cache(maxsize=1)
def settings() -> dict:
    s = _read_yaml(CONFIG_DIR / "settings.yaml")
    leaked = list(_find_keys(s))
    if leaked:
        raise SettingsError(
            "An API key or printer access code was typed into config/settings.yaml. That file is "
            "uploaded to GitHub, where anyone could copy it. Move it into config/settings.local.yaml "
            "(see settings.local.example.yaml) and remove it from settings.yaml.")
    local = CONFIG_DIR / "settings.local.yaml"
    if local.exists():
        s = _merge(s, _read_yaml(local))
    return s


def check() -> int:
    """Run by start.bat before launching: print a plain message and fail if settings are broken."""
    try:
        settings()
        printers()
    except SettingsError as e:
        print("\n" + str(e) + "\n")
        return 1
    local = CONFIG_DIR / "settings.local.yaml"
    if not local.exists() and (CONFIG_DIR / "settings.local.yaml.txt").exists():
        print("\nNote: config/settings.local.yaml.txt should be named settings.local.yaml "
              "(Notepad added .txt). The AI helper won't work until it's renamed.\n")
    return 0



def path(rel: str | os.PathLike) -> Path:
    """Resolve a path from settings relative to the project root."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


# ---------------------------------------------------------------- printers

# What the app hands over for each kind of printer:
#   gcode - a sliced .gcode for the SD card / Wi-Fi (Ender 3, Prusa, Klipper printers, ...)
#   bambu - a sliced .gcode.3mf for Bambu Lab, which the app can also send over Wi-Fi
#   3mf   - an arranged, NOT sliced .3mf: the worker slices it in the printer's own software
# Slicing: OrcaSlicer with the printer's `orca_profiles` (a folder made by
# tools/make_slicer_profiles.py) - works for most printers. If OrcaSlicer is missing,
# gcode printers use PrusaSlicer with `profile` (an .ini) and bambu printers get a 3mf.
PRINTER_OUTPUTS = ("gcode", "bambu", "3mf")
# OrcaSlicer's names for build plates. Each filament profile has a bed temperature per plate.
BED_TYPES = ("Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate",
             "Textured Cool Plate", "Supertack Plate")
_PRINTER_DEFAULTS = {"margin_mm": 5, "output": "gcode", "profile": "", "slicer_app": "", "note": "",
                     "orca_profiles": "", "bed_type": ""}


def printers() -> list[dict]:
    """Every printer in settings.yaml, first one = default.

    Accepts the newer `printers:` list and the older single `printer:` block.
    """
    s = settings()
    raw = s.get("printers")
    if not raw and s.get("printer"):
        raw = [s["printer"]]
    out = []
    for i, p in enumerate(raw or []):
        if not isinstance(p, dict):
            raise SettingsError(f"Printer number {i + 1} in config/settings.yaml is not set up right.")
        p = {**_PRINTER_DEFAULTS, **p}
        p["name"] = str(p.get("name") or f"Printer {i + 1}")
        missing = [k for k in ("bed_x_mm", "bed_y_mm", "max_z_mm") if not p.get(k)]
        if missing:
            raise SettingsError(f"Printer '{p['name']}' in config/settings.yaml needs: {', '.join(missing)}.")
        p["output"] = str(p["output"]).lower().lstrip(".")
        if p["output"] not in PRINTER_OUTPUTS:
            raise SettingsError(f"Printer '{p['name']}': output must be one of "
                                f"{', '.join(PRINTER_OUTPUTS)} (it says '{p['output']}').")
        if p["output"] == "gcode" and not (p["profile"] or p["orca_profiles"]):
            raise SettingsError(f"Printer '{p['name']}' makes G-code, so it needs orca_profiles "
                                "(made by tools/make_slicer_profiles.py) or a PrusaSlicer profile.")
        if p["orca_profiles"] and p["bed_type"] not in BED_TYPES:
            raise SettingsError(f"Printer '{p['name']}': bed_type must be one of {', '.join(BED_TYPES)} "
                                "(the build plate on the printer; it sets the bed temperature).")
        if p["output"] == "bambu" and not p["orca_profiles"]:
            raise SettingsError(f"Printer '{p['name']}' is sliced with OrcaSlicer, so it needs "
                                "orca_profiles (a folder made by tools/make_slicer_profiles.py).")
        out.append(p)
    if not out:
        raise SettingsError("No printer is set up in config/settings.yaml (see 'printers:').")
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise SettingsError("Two printers in config/settings.yaml have the same name.")
    return out


def get_printer(name: str | None = None) -> dict:
    """The printer with this name; the first (default) one if the name is empty or unknown."""
    ps = printers()
    return next((p for p in ps if name and p["name"] == name), ps[0])


if __name__ == "__main__":
    raise SystemExit(check())
