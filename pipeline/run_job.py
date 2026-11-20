"""The only entry point the UI (or any future front end) uses.

    job = prepare(request, parts)   # validate + build one STL per chosen piece -> preview
    job = make_gcode(job)           # arrange pieces onto plates and make the print file(s)

Pieces are packed onto as few plates as fit the chosen printer's bed; each plate is one
print file. What that file is depends on the printer (see `printers:` in settings.yaml):
  output: gcode -> sliced here, a .gcode for the SD card / Wi-Fi
  output: bambu -> sliced here, a ready .gcode.3mf for Bambu Lab (can be sent over Wi-Fi)
  output: 3mf   -> a ready-arranged .3mf to open in the printer's own slicer
Printers with `orca_profiles` are sliced by OrcaSlicer. If OrcaSlicer isn't installed (or
fails), gcode printers fall back to PrusaSlicer (`profile`) and bambu printers to "3mf".
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
from .config import get_printer, path, settings
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
    printer: str = ""                     # printer name from settings.yaml; "" = the first one


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
    # result.gcode is this plate's print file: a .gcode, or a .3mf for "3mf" printers
    # (then the time/plastic numbers are estimates, or None if they couldn't be worked out).
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
    output: str = ""        # what was actually made: gcode, bambu (.gcode.3mf) or 3mf
    slicer_used: str = ""   # orcaslicer, prusaslicer, or "" if not sliced here

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def printer(self) -> dict:
        return get_printer(self.request.printer)

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
        kind = self.output or self.printer["output"]
        ext = {"gcode": "gcode", "bambu": "gcode.3mf", "3mf": "3mf"}[kind]
        return f"{pid}_{self.request.material}_{self.folder.name[:13]}{tail}.{ext}"

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
        "printer": job.printer["name"],
        "output": job.output or job.printer["output"],
        "slicer": job.slicer_used,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    (job.folder / "job.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")


def _stl_name(rel: str) -> str:
    return re.sub(r"[^\w.-]+", "_", Path(rel).with_suffix("").as_posix()) + ".stl"


def prepare(req: JobRequest, parts: list[Part]) -> Job:
    part = get_part(parts, req.part_id)
    job = Job(request=req, folder=_new_folder(req.part_id or "unknown"), part=part)
    chk = check_request(part, req.params, req.material, req.strength, req.files, req.printer)
    job.errors += chk.errors
    job.warnings += chk.warnings
    job.params, job.files = chk.params, chk.files
    if job.ok:
        try:
            for rel, n in job.files.items():
                mf = part.file(rel)
                model = build_piece(part, mf, job.params, job.folder / "pieces" / _stl_name(rel),
                                    job.printer)
                errs = check_fits(model.size_mm, job.printer)
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


def plan_plates(pieces: list[Piece], printer: dict | None = None) -> list[Plate]:
    """Shelf-pack every copy's footprint onto as few beds as possible, and record where.

    Deliberately simple and a little cautious (bounding boxes + gaps). The app places the
    pieces itself and hands the slicer ONE ready-arranged model per plate, so what you
    see planned here is exactly what prints.
    """
    p = printer or get_printer()
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


def _placed_meshes(plate: Plate) -> list:
    """Each piece of the plate as a mesh, moved to where plan_plates put it."""
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
    return meshes


def build_plate_stl(plate: Plate, out: Path) -> Path:
    """Combine a plate's pieces, already placed, into one STL for the slicer."""
    import trimesh

    trimesh.util.concatenate(_placed_meshes(plate)).export(out)
    return out


def build_plate_3mf(plate: Plate, out: Path, printer: dict) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    return _build_plate_3mf(plate, out, printer)


def _build_plate_3mf(plate: Plate, out: Path, printer: dict) -> Path:
    """The plate as a .3mf for the printer's own slicer: every piece a separate, named object,
    arranged as planned and centred on the bed. Opens in Bambu Studio, OrcaSlicer, PrusaSlicer."""
    import numpy as np
    import trimesh

    meshes = _placed_meshes(plate)
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    shift = [printer["bed_x_mm"] / 2 - (lo[0] + hi[0]) / 2, printer["bed_y_mm"] / 2 - (lo[1] + hi[1]) / 2, 0]
    scene, seen = trimesh.Scene(), {}
    for pl, m in zip(plate.placements, meshes):
        m.apply_translation(shift)
        seen[pl.label] = seen.get(pl.label, 0) + 1
        name = pl.label if seen[pl.label] == 1 else f"{pl.label} {seen[pl.label]}"
        scene.add_geometry(m, node_name=name, geom_name=name)
    scene.export(out)
    return out


def make_gcode(job: Job) -> Job:
    if not job.ok or not job.pieces:
        return job
    s = settings()
    printer = job.printer
    profiles = SliceProfiles(
        printer=path(printer["profile"]) if printer["profile"] else Path(),
        material=path(s["materials"][job.request.material]["profile"]),
        strength=path(s["strength"][job.request.strength]["profile"]),
        material_name=job.request.material,
        strength_name=job.request.strength,
    )
    center = (printer["bed_x_mm"] / 2, printer["bed_y_mm"] / 2)
    job.plates = plan_plates(job.pieces, printer)
    job.output = printer["output"]
    if printer["orca_profiles"] and printer["output"] in ("gcode", "bambu"):
        if _make_orca(job):
            return job
    if job.output in ("bambu", "3mf"):
        job.output = "3mf"
        return _make_3mf(job, profiles, center)
    if not printer["profile"]:
        job.errors.append(f"No way to slice for the {printer['name']} on this computer. Install OrcaSlicer.")
        _log(job)
        return job
    try:
        from .slicers.base import prepend_header

        job.slicer_used = settings()["slicer"]["backend"]
        slicer = get_slicer()
        for i, plate in enumerate(job.plates):
            out = job.folder / job.plate_name(i)
            plate_stl = build_plate_stl(plate, job.folder / f"plate{i + 1}.stl")
            plate.result = slicer.slice([plate_stl], out, profiles, center)
            prepend_header(out, _credit_lines(job, i))
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


def _credit_lines(job: Job, i: int) -> list[str]:
    counts: dict[str, int] = {}
    for lb in job.plates[i].labels:
        counts[lb] = counts.get(lb, 0) + 1
    return [
        "Hope Arising 3D Print Shop",
        f"Part: {job.part.name}" + (f"  (plate {i + 1} of {len(job.plates)})" if len(job.plates) > 1 else ""),
        "Pieces: " + ", ".join(f"{n} x {lb}" for lb, n in counts.items()),
        f"Settings: {json.dumps(job.params)}",
        f"Material: {job.request.material}  Strength: {job.request.strength}",
        f"Credit: {job.part.credit}",
    ]


def _make_orca(job: Job) -> bool:
    """Slice every plate with OrcaSlicer using the printer's own profiles.

    Bambu printers keep the sliced .gcode.3mf (what they print, and what Wi-Fi sending needs);
    other printers get the plain .gcode out of it. Returns False, with a warning, if OrcaSlicer
    isn't installed or fails - make_gcode then uses the fallback, so an order never gets stuck.
    """
    from .slicers.base import prepend_header
    from .slicers.orcaslicer import OrcaSlicer, extract_gcode

    s, printer = settings(), job.printer
    try:
        orca = OrcaSlicer(s["slicer"].get("orca", ""), s["slicer"].get("timeout_s", 600))
    except SlicerError:
        job.warnings.append("OrcaSlicer is not installed on this computer, so "
                            + ("the file must be sliced by hand. Install OrcaSlicer to skip this step."
                               if job.output == "bambu" else "the older slicer was used."))
        return False
    overrides = s["strength"][job.request.strength].get("orca") or {}
    made: list[Path] = []
    try:
        for i, plate in enumerate(job.plates):
            src = build_plate_3mf(plate, job.folder / "orca_in" / f"plate{i + 1}.3mf", printer)
            final = job.folder / job.plate_name(i)
            sliced = final if job.output == "bambu" else job.folder / "orca_in" / f"plate{i + 1}.gcode.3mf"
            res = orca.slice(src, sliced, path(printer["orca_profiles"]), job.request.material, overrides)
            made.append(final)
            if job.output != "bambu":
                extract_gcode(sliced, final)
                prepend_header(final, _credit_lines(job, i))
            plate.result = SliceResult(final, res.print_time_s, res.filament_g, res.filament_m)
        job.slicer_used = "orcaslicer"
        if job.filament_g is not None:
            price = s["materials"][job.request.material].get("price_per_kg", 0)
            job.cost = round(job.filament_g / 1000 * price, 2)
        _log(job)
        return True
    except SlicerError as e:
        for f in made:
            f.unlink(missing_ok=True)
        for plate in job.plates:
            plate.result = None
        job.warnings.append("OrcaSlicer could not slice this automatically, so "
                            + ("here is a file to slice by hand." if job.output == "bambu"
                               else "the older slicer was used.")
                            + f" (Details are in the job folder.) {str(e).splitlines()[0]}")
        return False


def _make_3mf(job: Job, profiles: SliceProfiles, center: tuple[float, float]) -> Job:
    """For printers that print from their own slicer: one ready-arranged .3mf per plate.

    If the printer has an estimate profile, each plate is also sliced (into estimate/, never
    used for printing) to show time, plastic and cost. If that fails the .3mf is still fine.
    """
    s, printer = settings(), job.printer
    try:
        slicer = None
        if printer["profile"]:
            try:
                slicer = get_slicer()
            except SlicerError:
                job.warnings.append("Print time and cost can't be estimated on this computer. "
                                    f"{printer['slicer_app'] or 'The printer software'} will show them.")
        for i, plate in enumerate(job.plates):
            out = build_plate_3mf(plate, job.folder / job.plate_name(i), printer)
            t = g = m = None
            if slicer:
                plate_stl = build_plate_stl(plate, job.folder / f"plate{i + 1}.stl")
                est = job.folder / "estimate" / f"plate{i + 1}.gcode"
                try:
                    r = slicer.slice([plate_stl], est, profiles, center)
                    t, g, m = r.print_time_s, r.filament_g, r.filament_m
                except SlicerError:
                    job.warnings.append(f"Could not estimate print {i + 1}. The file itself is fine.")
            plate.result = SliceResult(out, t, g, m)
        if job.filament_g is not None:
            price = s["materials"][job.request.material].get("price_per_kg", 0)
            job.cost = round(job.filament_g / 1000 * price, 2)
    except Exception:
        job.errors.append("Unexpected problem while making the print file. Details saved in the job folder.")
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
