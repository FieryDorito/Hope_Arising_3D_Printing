"""Parts library: the Google Sheet catalog joined with model files on disk.

How a part is assembled:
  * One row in the sheet (name, category, license, ...).
  * Its part_id is the sheet's `part_id` column if present, otherwise a slug of
    part_name ("Sturdy wall hook" -> "sturdy-wall-hook").
  * Its files live in models/<Category>/<part_id>/ (or models/<part_id>/):
        model.stl | model.3mf | model.obj   fixed shape, resized by scale only
        model.scad                          parametric OpenSCAD source
        part.yaml   (optional)              parameters, notes, print settings
        thumb.png   (auto-generated)

A part can be printed only when it has a model file AND commercial_use_ok is YES.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from .config import path, settings

MESH_EXTS = (".stl", ".3mf", ".obj")
SCAD_EXT = ".scad"

# Fixes for spelling variants in the sheet -> standard category names (Living Table 15.1).
CATEGORY_FIXES = {
    "houshold": "Household",
    "household": "Household",
    "agriculture": "Agricultural",
    "agricultural": "Agricultural",
    "plumbing": "Plumbing",
    "utility": "Utility",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def normalize_category(raw: str) -> str:
    key = (raw or "").strip().lower()
    return CATEGORY_FIXES.get(key, raw.strip().title() if raw else "Other")


@dataclass
class ParamSpec:
    name: str
    label: str
    default: float
    min: float
    max: float
    unit: str = "mm"
    step: float = 1.0
    help: str = ""
    kind: str = "number"  # number | int | bool

    def to_dict(self) -> dict:
        return self.__dict__.copy()


SCALE_PARAM = ParamSpec(
    name="scale_percent",
    label="Size",
    default=100,
    min=25,
    max=400,
    unit="%",
    step=5,
    help="100% is the original size. The whole part grows or shrinks evenly.",
)


@dataclass
class Part:
    part_id: str
    name: str
    category: str
    author: str
    license: str
    commercial_ok: bool
    link: str
    folder: Path
    model_file: Path | None = None
    params: list[ParamSpec] = field(default_factory=list)
    description: str = ""
    base_size_mm: tuple[float, float, float] | None = None  # for mesh parts

    @property
    def kind(self) -> str:
        if not self.model_file:
            return "missing"
        return "scad" if self.model_file.suffix.lower() == SCAD_EXT else "mesh"

    @property
    def printable(self) -> bool:
        return self.model_file is not None and self.commercial_ok

    @property
    def status(self) -> str:
        """Plain-language reason a part can or can't be printed."""
        if not self.commercial_ok:
            return "Not allowed for sale (license)"
        if not self.model_file:
            return "Model file not added yet"
        return "Ready"

    @property
    def thumbnail(self) -> Path | None:
        t = self.folder / "thumb.png"
        return t if t.exists() else None

    @property
    def credit(self) -> str:
        return f'"{self.name}" by {self.author} ({self.license}) {self.link}'

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}


# ---------------------------------------------------------------- loading

def _find_model(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    scad = folder / f"model{SCAD_EXT}"
    if scad.exists():
        return scad
    for ext in MESH_EXTS:
        hits = sorted(folder.glob(f"*{ext}")) + sorted(folder.glob(f"*{ext.upper()}"))
        if hits:
            return hits[0]
    return None


def _load_part_yaml(folder: Path) -> dict:
    f = folder / "part.yaml"
    if not f.exists():
        return {}
    with open(f, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _mesh_size(model_file: Path) -> tuple[float, float, float] | None:
    from .cad import load_mesh  # local import keeps library loading light

    try:
        ext = load_mesh(model_file).extents
        return tuple(round(float(v), 1) for v in ext)
    except Exception:
        return None


def load_catalog(catalog_csv: Path | None = None, models_dir: Path | None = None) -> list[Part]:
    cfg = settings()["library"]
    catalog_csv = catalog_csv or path(cfg["catalog_csv"])
    models_dir = models_dir or path(cfg["models_dir"])

    parts: list[Part] = []
    seen: set[str] = set()
    with open(catalog_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = row.get("part_name", "")
            if not name:
                continue
            pid = row.get("part_id") or slugify(name)
            if pid in seen:  # duplicate rows: keep the first
                continue
            seen.add(pid)

            category = normalize_category(row.get("category", ""))
            folder = models_dir / category / pid
            if not folder.is_dir() and (models_dir / pid).is_dir():
                folder = models_dir / pid
            meta = _load_part_yaml(folder)
            model = _find_model(folder)
            part = Part(
                part_id=pid,
                name=name,
                category=category,
                author=row.get("original_author", ""),
                license=row.get("license", ""),
                commercial_ok=row.get("commercial_use_ok", "").upper().startswith("Y"),
                link=row.get("link", ""),
                folder=folder,
                model_file=model,
                description=meta.get("description", ""),
            )
            if part.kind == "scad":
                part.params = [ParamSpec(**p) for p in meta.get("params", [])]
            elif part.kind == "mesh":
                part.base_size_mm = _mesh_size(model)
                scale = ParamSpec(**{**SCALE_PARAM.to_dict(), **meta.get("scale", {})})
                part.params = [scale]
            parts.append(part)
    return parts


def get_part(parts: list[Part], part_id: str) -> Part | None:
    return next((p for p in parts if p.part_id == part_id), None)


def search(parts: list[Part], text: str = "", category: str | None = None) -> list[Part]:
    words = text.lower().split()
    out = []
    for p in parts:
        if category and category != "All" and p.category != category:
            continue
        hay = f"{p.name} {p.category} {p.description}".lower()
        if all(w in hay for w in words):
            out.append(p)
    return sorted(out, key=lambda p: (not p.printable, p.name.lower()))


# ---------------------------------------------------------------- sheet sync

def sheet_csv_url() -> str:
    cfg = settings()["library"]
    return (
        f"https://docs.google.com/spreadsheets/d/{cfg['sheet_id']}/export"
        f"?format=csv&gid={cfg.get('sheet_gid', 0)}"
    )


def sync_from_sheet(timeout: float = 20) -> tuple[bool, str]:
    """Download the sheet as CSV and replace the local copy.

    The old copy is kept if anything goes wrong, so the shop keeps working offline.
    Returns (ok, message for the operator).
    """
    target = path(settings()["library"]["catalog_csv"])
    try:
        r = requests.get(sheet_csv_url(), timeout=timeout)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    except requests.RequestException:
        return False, "Could not reach Google Sheets. Using the saved copy of the library."

    if "<html" in text[:500].lower():
        return False, ("The library sheet is not shared publicly. Ask a maintainer to set it to "
                       "'Anyone with the link can view'. Using the saved copy.")
    header = next(csv.reader(io.StringIO(text)), [])
    if "part_name" not in [h.strip().lower() for h in header]:
        return False, "The sheet's first row changed (no 'part_name' column). Using the saved copy."

    rows = sum(1 for _ in csv.DictReader(io.StringIO(text)))
    if target.exists():
        shutil.copy2(target, target.with_suffix(".csv.bak"))
    target.write_text(text, encoding="utf-8")
    return True, f"Library updated: {rows} parts."


def ensure_thumbnails(parts: list[Part]) -> int:
    """Create thumb.png for any part with a mesh/scad model but no picture yet."""
    from .cad import render_thumbnail

    made = 0
    for p in parts:
        if p.model_file is None or p.thumbnail is not None:
            continue
        try:
            src = p.model_file
            if p.kind == "scad":  # render default STL first
                from .cad import build_model
                src = build_model(p, p.defaults(), p.folder / ".thumb_src.stl").stl
            render_thumbnail(src, p.folder / "thumb.png")
            made += 1
        except Exception:
            pass  # a missing picture should never stop the shop
    return made
