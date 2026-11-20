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
            if k == "api_key" and v and "paste-your" not in str(v) and not str(v).endswith("..."):
                yield here
            else:
                yield from _find_keys(v, here)


@lru_cache(maxsize=1)
def settings() -> dict:
    s = _read_yaml(CONFIG_DIR / "settings.yaml")
    leaked = list(_find_keys(s))
    if leaked:
        raise SettingsError(
            "An API key was typed into config/settings.yaml. That file is uploaded to GitHub, "
            "where anyone could copy the key. Move the key into config/settings.local.yaml "
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
#   gcode - the app slices the part itself (PrusaSlicer + the printer's .ini profile) and
#           gives a .gcode for the SD card / Wi-Fi. Ender 3, Prusa, Klipper printers, ...
#   3mf   - the app arranges the plate and gives a .3mf. The worker opens it in the printer's
#           own slicer (Bambu Studio, ...) and prints from there. For printers that won't run
#           plain G-code from a card, like Bambu Lab.
PRINTER_OUTPUTS = ("gcode", "3mf")
_PRINTER_DEFAULTS = {"margin_mm": 5, "output": "gcode", "profile": "", "slicer_app": "", "note": ""}


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
        if p["output"] == "gcode" and not p["profile"]:
            raise SettingsError(f"Printer '{p['name']}' makes G-code, so it needs a profile "
                                "(a PrusaSlicer .ini in config/printers/).")
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
