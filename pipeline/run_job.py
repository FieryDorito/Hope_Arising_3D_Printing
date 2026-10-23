"""The only entry point the UI (or any future front end) uses.

    job = prepare(request, parts)   # validate + build STL  -> preview
    job = make_gcode(job)           # slice                -> G-code + time + cost

Every job gets its own folder in jobs/ with the request, the final settings,
the STL, the G-code and logs, so problems can be reproduced later.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .cad import CadError, ModelInfo, build_model
from .config import path, settings
from .library import Part, get_part
from .slicers import SliceProfiles, SliceResult, SlicerError, get_slicer
from .validate import check_fits, check_request


@dataclass
class JobRequest:
    part_id: str
    params: dict
    material: str = "PLA"
    strength: str = "Normal"
    customer_request: str = ""       # what the operator typed, if the AI was used
    ai_suggestion: dict | None = None


@dataclass
class Job:
    request: JobRequest
    folder: Path
    part: Part | None = None
    params: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model: ModelInfo | None = None
    slice: SliceResult | None = None
    cost: float | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def gcode_name(self) -> str:
        pid = self.part.part_id if self.part else "part"
        return f"{pid}_{self.request.material}_{self.folder.name[:13]}.gcode"


def _new_folder(part_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = path(settings()["jobs_dir"]) / f"{stamp}_{part_id}"
    n = 1
    while folder.exists():
        n += 1
        folder = folder.with_name(f"{stamp}_{part_id}_{n}")
    folder.mkdir(parents=True)
    return folder


def _log(job: Job) -> None:
    info = {
        "request": asdict(job.request),
        "final_params": job.params,
        "errors": job.errors,
        "warnings": job.warnings,
        "model": {"size_mm": job.model.size_mm, "watertight": job.model.watertight,
                  "volume_cm3": job.model.volume_cm3} if job.model else None,
        "slice": {"print_time_s": job.slice.print_time_s, "filament_g": job.slice.filament_g,
                  "filament_m": job.slice.filament_m} if job.slice else None,
        "cost": job.cost,
        "slicer": settings()["slicer"]["backend"],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    (job.folder / "job.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")


def prepare(req: JobRequest, parts: list[Part]) -> Job:
    part = get_part(parts, req.part_id)
    job = Job(request=req, folder=_new_folder(req.part_id or "unknown"), part=part)
    chk = check_request(part, req.params, req.material, req.strength)
    job.errors += chk.errors
    job.warnings += chk.warnings
    job.params = chk.params
    if job.ok:
        try:
            job.model = build_model(part, job.params, job.folder / "model.stl")
            job.errors += check_fits(job.model.size_mm)
            if not job.model.watertight:
                job.warnings.append("The 3D model has small holes. It will probably still print, "
                                    "but check the first layers.")
        except CadError as e:
            job.errors.append(str(e))
        except Exception:
            job.errors.append("Unexpected problem building the model. Details saved in the job folder.")
            (job.folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    _log(job)
    return job


def make_gcode(job: Job) -> Job:
    if not job.ok or job.model is None:
        return job
    s = settings()
    profiles = SliceProfiles(
        printer=path(s["printer"]["profile"]),
        material=path(s["materials"][job.request.material]["profile"]),
        strength=path(s["strength"][job.request.strength]["profile"]),
        material_name=job.request.material,
        strength_name=job.request.strength,
    )
    center = (s["printer"]["bed_x_mm"] / 2, s["printer"]["bed_y_mm"] / 2)
    out = job.folder / job.gcode_name
    try:
        job.slice = get_slicer().slice(job.model.stl, out, profiles, center)
        from .slicers.base import prepend_header

        prepend_header(out, [
            "Hope Arising 3D Print Shop",
            f"Part: {job.part.name}  Settings: {json.dumps(job.params)}",
            f"Material: {job.request.material}  Strength: {job.request.strength}",
            f"Credit: {job.part.credit}",
        ])
        if job.slice.filament_g is not None:
            price = s["materials"][job.request.material].get("price_per_kg", 0)
            job.cost = round(job.slice.filament_g / 1000 * price, 2)
    except SlicerError as e:
        job.errors.append(str(e))
    except Exception:
        job.errors.append("Unexpected problem while slicing. Details saved in the job folder.")
        (job.folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    _log(job)
    return job


def run_job(req: JobRequest, parts: list[Part]) -> Job:
    """Both steps at once (used by tests and scripts)."""
    return make_gcode(prepare(req, parts))


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m} min" if h else f"{m} min"
