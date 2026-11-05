"""Past prints and the simple order list.

Every job already has its own folder in jobs/ (see run_job.py). A job that reached G-code
is a "past print". This module reads those folders back and keeps a small order.json
next to job.json with who it is for and where it is up to:

    list_jobs()                 newest first, only jobs that have a print file
    save_order(folder, ...)     customer name, phone, notes, status

Nothing here re-slices: reprinting uses the G-code that was already made.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import path, settings

# Order of the list is the order shown on screen.
STATUSES = ["Waiting to print", "Printing", "Done", "Picked up", "Failed"]
DEFAULT_STATUS = STATUSES[0]


@dataclass
class PastJob:
    folder: Path
    part_id: str
    params: dict
    material: str
    strength: str
    files: dict
    customer_request: str
    saved_at: datetime
    print_time_s: int | None
    filament_g: float | None
    cost: float | None
    gcodes: list[Path]
    order: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.order.get("status") or DEFAULT_STATUS

    @property
    def customer(self) -> str:
        return self.order.get("customer", "")

    @property
    def phone(self) -> str:
        return self.order.get("phone", "")

    @property
    def notes(self) -> str:
        return self.order.get("notes", "")


def jobs_dir() -> Path:
    return path(settings()["jobs_dir"])


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sum(values):
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def load_job(folder: Path) -> PastJob | None:
    """Read one job folder. None if it never got as far as a print file."""
    info = _read_json(folder / "job.json")
    gcodes = sorted(folder.glob("*.gcode"))
    if not info or not gcodes:
        return None
    req = info.get("request") or {}
    plates = info.get("plates") or []
    try:
        saved = datetime.fromisoformat(info.get("saved_at", ""))
    except ValueError:
        saved = datetime.fromtimestamp(folder.stat().st_mtime)
    return PastJob(
        folder=folder,
        part_id=req.get("part_id", ""),
        params=info.get("final_params") or req.get("params") or {},
        material=req.get("material", ""),
        strength=req.get("strength", ""),
        files=info.get("files") or req.get("files") or {},
        customer_request=req.get("customer_request", ""),
        saved_at=saved,
        print_time_s=_sum(p.get("print_time_s") for p in plates),
        filament_g=_sum(p.get("filament_g") for p in plates),
        cost=info.get("cost"),
        gcodes=gcodes,
        order=_read_json(folder / "order.json"),
    )


def list_jobs(folder: Path | None = None) -> list[PastJob]:
    root = folder or jobs_dir()
    if not root.exists():
        return []
    out = [j for d in root.iterdir() if d.is_dir() and (j := load_job(d))]
    return sorted(out, key=lambda j: j.saved_at, reverse=True)


def save_order(folder: Path, **fields) -> dict:
    """Merge fields (customer, phone, notes, status) into the job's order.json."""
    order = _read_json(folder / "order.json")
    order.update({k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if v is not None})
    if order.get("status") not in STATUSES:
        order["status"] = DEFAULT_STATUS
    order["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (folder / "order.json").write_text(json.dumps(order, indent=2), encoding="utf-8")
    return order
