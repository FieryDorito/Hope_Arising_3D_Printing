from __future__ import annotations

from ..config import settings
from .base import SliceProfiles, SliceResult, Slicer, SlicerError

# The fallback slicer for G-code printers when OrcaSlicer isn't installed (`slicer.backend`).
# OrcaSlicer itself is used directly by run_job for every printer with `orca_profiles`.
BACKENDS = {
    "prusaslicer": "pipeline.slicers.prusaslicer:PrusaSlicer",
}


def get_slicer() -> Slicer:
    import importlib

    cfg = settings()["slicer"]
    target = BACKENDS.get(cfg["backend"])
    if not target:
        raise SlicerError(f"Unknown slicer '{cfg['backend']}' in settings.yaml.")
    mod, cls = target.split(":")
    return getattr(importlib.import_module(mod), cls)(cfg.get("executable", ""), cfg.get("timeout_s", 600))


__all__ = ["get_slicer", "Slicer", "SliceProfiles", "SliceResult", "SlicerError"]
