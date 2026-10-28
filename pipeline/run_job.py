"""The only entry point the UI (or any future front end) uses.

    job = prepare(request, parts)   # validate + build one STL per chosen piece -> preview
    job = make_gcode(job)           # arrange pieces onto plates and slice      -> G-code(s)

Pieces are packed onto as few plates as fit the bed; each plate is one G-code file.
Every job gets its own folder in jobs/ with the request, the final settings,
the STLs, the G-code and logs, so problems can be reproduced later.
"""
from __future__ import annotations

import json
import math
import re
import traceback
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .cad import CadError, ModelInfo, build_piece
from .config import path, settings
from .library import Part, get_part
from .slicers import SliceProfiles, SliceResult, SlicerError, get_slicer
from .validate import check_fits, check_request

PLATE_GAP_MM = 8  # space left between pieces when planning plates


@dataclass
class JobRequest:
    part_id: str
    params: dict
    material: str = "PLA"
    strength: str = "Normal"
    files: dict[str, int] | None = None   # rel path -> how many; None = the part's defaults
    customer_request: str = ""            # what the operator typed, if the AI was used
    ai_suggestion: dict | None = None


@dataclass
class Piece:
    rel: str
    label: str
    count: int
    model: ModelInfo


@dataclass
class Placement:
    stl: Path
    label: str
    x: float          # lower-left corner on the plate (mm)
    y: float
    turned: bool      # rotated 90 degrees to fit better


@dataclass
class Plate:
    placements: list[Placement] = field(default_factory=list)
    result: SliceResult | None = None

    @property
    def labels(self) -> list[str]:
        return [pl.label for pl in self.placements]


@dataclass
class Job:
    request: JobRequest
    folder: Path
    part: Part | None = None
    params: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pieces: list[Piece] = field(default_factory=list)
    plates: list[Plate] = field(default_factory=list)
    cost: float | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    # Single-piece shortcuts (older code and tests)
    @property
    def model(self) -> ModelInfo | None:
        return self.pieces[0].model if self.pieces else None

    @property
    def slice(self) -> SliceResult | None:
        return self.plates[0].result if self.plates and self.plates[0].result else None

    @property
    def sliced(self) -> bool:
        return bool(self.plates) and all(p.result for p in self.plates)

    @property
    def print_time_s(self) -> int | None:
        ts = [p.result.print_time_s for p in self.plates if p.result]
        return sum(ts) if ts and None not in ts else None

    @property
    def filament_g(self) -> float | None:
        gs = [p.result.filament_g for p in self.plates if p.result]
        return round(sum(gs), 2) if gs and None not in gs else None

    @property
    def gcode_name(self) -> str:
        return self.plate_name(0)

    def plate_name(self, i: int) -> str:
        pid = self.part.part_id if self.part else "part"
        tail = f"_plate{i + 1}of{len(self.plates)}" if len(self.plates) > 1 else ""
        return f"{pid}_{self.request.material}_{self.folder.name[:13]}{tail}.gcode"

    def zip_all(self) -> Path:
        z = self.folder / f"{self.part.part_id}_all_plates.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in self.plates:
                if p.result:
                    zf.write(p.result.gcode, p.result.gcode.name)
        return z


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
        "files": job.files,
        "errors": job.errors,
        "warnings": job.warnings,
        "pieces": [{"file": p.rel, "count": p.count, "size_mm": p.model.size_mm,
                    "watertight": p.model.watertight} for p in job.pieces],
        "plates": [{"pieces": pl.labels,
                    "print_time_s": pl.result.print_time_s if pl.result else None,
                    "filament_g": pl.result.filament_g if pl.result else None} for pl in job.plates],
        "cost": job.cost,
        "slicer": settings()["slicer"]["backend"],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    (job.folder / "job.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")


def _stl_name(rel: str) -> str:
    return re.sub(r"[^\w.-]+", "_", Path(rel).with_suffix("").as_posix()) + ".stl"


def prepare(req: JobRequest, parts: list[Part]) -> Job:
    part = get_part(parts, req.part_id)
    job = Job(request=req, folder=_new_folder(req.part_id or "unknown"), part=part)
    chk = check_request(part, req.params, req.material, req.strength, req.files)
    job.errors += chk.errors
    job.warnings += chk.warnings
    job.params, job.files = chk.params, chk.files
    if job.ok:
        try:
            for rel, n in job.files.items():
                mf = part.file(rel)
                model = build_piece(part, mf, job.params, job.folder / "pieces" / _stl_name(rel))
                errs = check_fits(model.size_mm)
                job.errors += [f"{mf.label}: {e}" for e in errs] if len(job.files) > 1 else errs
                if not model.watertight:
                    job.warnings.append(f"{mf.label}: the 3D model has small holes. It will probably "
                                        "still print, but check the first layers.")
                job.pieces.append(Piece(rel, mf.label, n, model))
        except CadError as e:
            job.errors.append(str(e))
        except Exception:
            job.errors.append("Unexpected problem building the model. Details saved in the job folder.")
            (job.folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    _log(job)
    return job


def plan_plates(pieces: list[Piece]) -> list[Plate]:
    """Shelf-pack every copy's footprint onto as few beds as possible, and record where.

    Deliberately simple and a little cautious (bounding boxes + gaps). The app places the
    pieces itself and hands the slicer ONE ready-arranged model per plate, so what you
    see planned here is exactly what prints.
    """
    p = settings()["printer"]
    W = p["bed_x_mm"] - 2 * p["margin_mm"]
    D = p["bed_y_mm"] - 2 * p["margin_mm"]
    items = []
    for pc in pieces:
        x, y, _ = pc.model.size_mm
        turned = y > x                      # long side along X packs shelves better
        w, d = (y, x) if turned else (x, y)
        if w > W:                           # too long that way: turn it back
            turned, w, d = not turned, d, w
        items += [(w, d, turned, pc)] * pc.count
    items.sort(key=lambda t: t[1], reverse=True)

    plates: list[dict] = []   # {"shelves": [[y, height, next_x]], "next_y": float, "plate": Plate}
    for w, d, turned, pc in items:
        spot = None
        for pl in plates:
            for sh in pl["shelves"]:
                if d <= sh[1] and sh[2] + w <= W:
                    spot = (pl, sh[2], sh[0])
                    sh[2] += w + PLATE_GAP_MM
                    break
            if spot is None and pl["next_y"] + d <= D:
                pl["shelves"].append([pl["next_y"], d, w + PLATE_GAP_MM])
                spot = (pl, 0.0, pl["next_y"])
                pl["next_y"] += d + PLATE_GAP_MM
            if spot:
                break
        if spot is None:
            pl = {"shelves": [[0.0, d, w + PLATE_GAP_MM]], "next_y": d + PLATE_GAP_MM, "plate": Plate()}
            plates.append(pl)
            spot = (pl, 0.0, 0.0)
        pl, px, py = spot
        pl["plate"].placements.append(Placement(pc.model.stl, pc.label, px, py, turned))
    return [pl["plate"] for pl in plates]


def build_plate_stl(plate: Plate, out: Path) -> Path:
    """Combine a plate's pieces, already placed, into one STL for the slicer."""
    import trimesh

    cache, meshes = {}, []
    for pl in plate.placements:
        if pl.stl not in cache:
            cache[pl.stl] = trimesh.load(pl.stl, force="mesh")
        m = cache[pl.stl].copy()
        if pl.turned:
            m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 0, 1]))
        lo = m.bounds[0]
        m.apply_translation([pl.x - lo[0], pl.y - lo[1], -lo[2]])
        meshes.append(m)
    trimesh.util.concatenate(meshes).export(out)
    return out


def make_gcode(job: Job) -> Job:
    if not job.ok or not job.pieces:
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
    job.plates = plan_plates(job.pieces)
    try:
        from .slicers.base import prepend_header

        slicer = get_slicer()
        for i, plate in enumerate(job.plates):
            out = job.folder / job.plate_name(i)
            plate_stl = build_plate_stl(plate, job.folder / f"plate{i + 1}.stl")
            plate.result = slicer.slice([plate_stl], out, profiles, center)
            counts = {}
            for lb in plate.labels:
                counts[lb] = counts.get(lb, 0) + 1
            prepend_header(out, [
                "Hope Arising 3D Print Shop",
                f"Part: {job.part.name}"
                + (f"  (plate {i + 1} of {len(job.plates)})" if len(job.plates) > 1 else ""),
                "Pieces: " + ", ".join(f"{n} x {lb}" for lb, n in counts.items()),
                f"Settings: {json.dumps(job.params)}",
                f"Material: {job.request.material}  Strength: {job.request.strength}",
                f"Credit: {job.part.credit}",
            ])
        if job.filament_g is not None:
            price = s["materials"][job.request.material].get("price_per_kg", 0)
            job.cost = round(job.filament_g / 1000 * price, 2)
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
