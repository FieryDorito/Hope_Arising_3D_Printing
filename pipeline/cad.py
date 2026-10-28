"""CAD stage: part + parameter values -> a printable STL.

  mesh parts (STL/3MF/OBJ): scaled evenly by scale_percent, then dropped onto the bed (z=0).
  scad parts: rendered by the OpenSCAD command line with each parameter passed as -D name=value.

Any future CAD backend (CadQuery, FreeCAD) only needs another branch in build_model().
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from .library import ModelFile, Part


class CadError(Exception):
    """Problem turning a part into an STL. Message is shown to the operator."""


@dataclass
class ModelInfo:
    stl: Path
    size_mm: tuple[float, float, float]
    watertight: bool
    volume_cm3: float


def load_mesh(model_file: Path) -> trimesh.Trimesh:
    try:
        mesh = trimesh.load(model_file, force="mesh")
    except Exception as e:
        raise CadError(f"Could not read the 3D file {model_file.name}.") from e
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise CadError(f"Could not read the 3D file {model_file.name}.")
    return mesh


def _openscad_exe() -> str:
    exe = shutil.which("openscad") or shutil.which("openscad.com")
    if not exe:
        raise CadError("OpenSCAD is not installed on this computer (needed for adjustable parts).")
    return exe


def _scad_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if not float(v).is_integer() else str(int(v))
    return '"' + str(v).replace('"', '\\"') + '"'


def build_piece(part: Part, mf: ModelFile, params: dict, out_stl: Path) -> ModelInfo:
    """Turn one model file of a part into a ready-to-slice STL."""
    out_stl.parent.mkdir(parents=True, exist_ok=True)

    if mf.kind == "scad":
        cmd = [_openscad_exe(), "-o", str(out_stl)]
        for p in part.params:
            if p.name == "scale_percent":
                continue
            cmd += ["-D", f"{p.name}={_scad_value(params.get(p.name, p.default))}"]
        cmd.append(str(mf.path))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not out_stl.exists():
            raise CadError(f"OpenSCAD failed for {mf.label}:\n{r.stderr[-800:]}")
        mesh = load_mesh(out_stl)
    else:
        mesh = load_mesh(mf.path)
        scale = float(params.get("scale_percent", 100)) / 100.0
        if scale != 1.0:
            mesh.apply_scale(scale)

    # Sit the piece on the bed and centre it at the origin; the slicer places it on the bed.
    lo, hi = mesh.bounds
    mesh.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])

    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
        trimesh.repair.fix_normals(mesh)

    mesh.export(out_stl)
    vol = abs(mesh.volume) / 1000.0 if mesh.is_watertight else 0.0
    return ModelInfo(
        stl=out_stl,
        size_mm=tuple(round(float(v), 1) for v in mesh.extents),
        watertight=bool(mesh.is_watertight),
        volume_cm3=round(float(vol), 1),
    )


def build_model(part: Part, params: dict, out_stl: Path) -> ModelInfo:
    """Single-file shortcut: builds the part's default (first) file."""
    if not part.files:
        raise CadError(f"No model file for {part.name} yet.")
    rel = next(iter(part.default_selection()))
    return build_piece(part, part.file(rel), params, out_stl)


# ---------------------------------------------------------------- pictures

def render_thumbnail(model_file: Path, out_png: Path, size_px: int = 320) -> Path:
    """Simple shaded picture of a part for the library grid (no GPU needed)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh = load_mesh(model_file)
    if len(mesh.faces) > 20000:
        mesh = _decimate(mesh, 20000)
    tris = mesh.vertices[mesh.faces]
    light = np.array([0.4, -0.5, 0.8]) / np.linalg.norm([0.4, -0.5, 0.8])
    shade = 0.35 + 0.65 * np.clip(mesh.face_normals @ light, 0, 1)
    colors = np.c_[0.95 * shade, 0.55 * shade, 0.2 * shade, np.ones_like(shade)]

    fig = plt.figure(figsize=(size_px / 100, size_px / 100), dpi=100)
    ax = fig.add_subplot(projection="3d")
    ax.add_collection3d(Poly3DCollection(tris, facecolors=colors, linewidths=0))
    lo, hi = mesh.bounds
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.view_init(elev=25, azim=-60)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, transparent=True)
    plt.close(fig)
    return out_png


def _decimate(mesh: trimesh.Trimesh, faces: int) -> trimesh.Trimesh:
    try:
        return mesh.simplify_quadric_decimation(face_count=faces)
    except Exception:
        idx = np.random.default_rng(0).choice(len(mesh.faces), faces, replace=False)
        return trimesh.Trimesh(mesh.vertices, mesh.faces[idx], process=False)


def preview_mesh(stl: Path, max_faces: int = 60000) -> trimesh.Trimesh:
    mesh = load_mesh(stl)
    return _decimate(mesh, max_faces) if len(mesh.faces) > max_faces else mesh
