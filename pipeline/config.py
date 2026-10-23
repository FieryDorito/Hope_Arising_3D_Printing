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


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        s = yaml.safe_load(f)
    local = CONFIG_DIR / "settings.local.yaml"
    if local.exists():
        with open(local, encoding="utf-8") as f:
            s = _merge(s, yaml.safe_load(f) or {})
    return s


def path(rel: str | os.PathLike) -> Path:
    """Resolve a path from settings relative to the project root."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p
