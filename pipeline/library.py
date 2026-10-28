"""Parts library: the Google Sheet catalog joined with the files on disk.

How a part is assembled:
  * One row in the sheet (name, category, license, link, ...).
  * Its part_id is the sheet's `part_id` column if present, otherwise a slug of
    part_name ("Sturdy wall hook" -> "sturdy-wall-hook").
  * Its files live in models/<Category>/<part_id>/ - put EVERYTHING from the download there,
    in any layout. Zip files are unpacked automatically.
        *.stl *.3mf *.obj       printable pieces/options (resized with "Size %")
        *.scad                  parametric OpenSCAD source (sizes from part.yaml params)
        *.txt *.md *.pdf        designer instructions, shown in the app
        *.png *.jpg ...         pictures, shown in the app (first one = library picture)
        part.yaml (optional)    labels, which file is the default, one-or-many choice, params
  * .thumbs/ holds generated pictures and is never committed.

A part can be printed only when it has at least one model file AND commercial_use_ok is YES.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import path, settings

MESH_EXTS = {".stl", ".3mf", ".obj"}
SCAD_EXT = ".scad"
MODEL_EXTS = MESH_EXTS | {SCAD_EXT}
TEXT_EXTS = {".txt", ".md"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SKIP_NAMES = {"put-stl-here.txt", "part.yaml", "license.txt", "licence.txt", ".gitkeep", ".ds_store"}
SKIP_DIRS = {".thumbs", "__macosx", ".git"}
THUMBS = ".thumbs"

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


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def nice_label(stem: str) -> str:
    """'hook_small-v2' -> 'Hook small v2'."""
    words = re.sub(r"[_\-.]+", " ", stem).strip()
    return (words[:1].upper() + words[1:]) if words else stem


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
    help="100% is the original size. Every piece grows or shrinks evenly.",
)


@dataclass
class ModelFile:
    """One printable file in a part folder: a piece of the part, or one option of it."""
    rel: str                    # path inside the part folder, with / separators
    path: Path
    label: str
    default: bool = False       # pre-selected for the operator
    count: int = 1              # default how many to print
    size_mm: tuple[float, float, float] | None = None

    @property
    def kind(self) -> str:
        return "scad" if self.path.suffix.lower() == SCAD_EXT else "mesh"


@dataclass
class Doc:
    """A designer instruction file or picture."""
    path: Path
    kind: str                   # text | pdf | image
    title: str


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
    files: list[ModelFile] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    choose: str = "any"          # any = tick any pieces | one = pick exactly one option
    params: list[ParamSpec] = field(default_factory=list)
    description: str = ""
    configured: bool = False     # part.yaml has a files: section (set up by a maintainer)

    # -------------------------------------------------- convenience
    @property
    def model_file(self) -> Path | None:
        return self.files[0].path if self.files else None

    @property
    def kind(self) -> str:
        if not self.files:
            return "missing"
        kinds = {f.kind for f in self.files}
        return kinds.pop() if len(kinds) == 1 else "mixed"

    @property
    def base_size_mm(self):
        d = self.default_selection()
        f = self.file(next(iter(d))) if d else None
        return f.size_mm if f else None

    @property
    def printable(self) -> bool:
        return bool(self.files) and self.commercial_ok

    @property
    def needs_setup(self) -> bool:
        """Several files but nobody has labelled them yet."""
        return len(self.files) > 1 and not self.configured

    @property
    def status(self) -> str:
        """Plain-language reason a part can or can't be printed."""
        if not self.commercial_ok:
            return "Not allowed for sale (license)"
        if not self.files:
            return "Model file not added yet"
        return "Ready"

    @property
    def images(self) -> list[Doc]:
        return [d for d in self.docs if d.kind == "image"]

    @property
    def instructions(self) -> list[Doc]:
        return [d for d in self.docs if d.kind != "image"]

    @property
    def thumbnail(self) -> Path | None:
        if self.images:
            return self.images[0].path
        if self.files:
            t = thumb_path(self, self.files[0])
            return t if t.exists() else None
        return None

    @property
    def credit(self) -> str:
        return f'"{self.name}" by {self.author} ({self.license}) {self.link}'

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}

    def file(self, rel: str) -> ModelFile | None:
        return next((f for f in self.files if f.rel == rel), None)

    def file_by_label(self, label: str) -> ModelFile | None:
        low = label.strip().lower()
        return next((f for f in self.files if f.label.lower() == low or f.rel.lower() == low), None)

    def default_selection(self) -> dict[str, int]:
        sel = {f.rel: f.count for f in self.files if f.default}
        if not sel and self.files:
            sel = {self.files[0].rel: self.files[0].count}
        if self.choose == "one" and len(sel) > 1:
            first = next(iter(sel))
            sel = {first: sel[first]}
        return sel


def thumb_path(part: Part, mf: ModelFile) -> Path:
    return part.folder / THUMBS / (mf.rel.replace("/", "__") + ".png")


# ---------------------------------------------------------------- scanning

def _load_part_yaml(folder: Path) -> dict:
    f = folder / "part.yaml"
    if not f.exists():
        return {}
    with open(f, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def scan_folder(folder: Path) -> tuple[list[Path], list[Path]]:
    """Return (model files, doc files) anywhere under the part folder."""
    models, docs = [], []
    if not folder.is_dir():
        return models, docs
    for p in sorted(folder.rglob("*"), key=lambda q: _natural_key(q.relative_to(folder).as_posix())):
        rel_parts = p.relative_to(folder).parts
        if any(part.lower() in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if not p.is_file() or p.name.lower() in SKIP_NAMES or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in MODEL_EXTS:
            models.append(p)
        elif ext in TEXT_EXTS | PDF_EXTS | IMAGE_EXTS:
            docs.append(p)
    return models, docs


def _sizes_cache(folder: Path) -> tuple[dict, Path]:
    f = folder / THUMBS / "sizes.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")), f
    except Exception:
        return {}, f


def _mesh_size(p: Path, cache: dict) -> tuple[float, float, float] | None:
    st = p.stat()
    key = f"{p.name}|{st.st_size}|{int(st.st_mtime)}"
    if key in cache:
        return tuple(cache[key])
    from .cad import load_mesh  # local import keeps library loading light

    try:
        size = tuple(round(float(v), 1) for v in load_mesh(p).extents)
    except Exception:
        return None
    cache[key] = size
    return size


def build_part(row: dict, models_dir: Path) -> Part:
    name = row["part_name"]
    pid = row.get("part_id") or slugify(name)
    category = normalize_category(row.get("category", ""))
    folder = models_dir / category / pid
    if not folder.is_dir() and (models_dir / pid).is_dir():
        folder = models_dir / pid
    meta = _load_part_yaml(folder)
    part = Part(
        part_id=pid,
        name=name,
        category=category,
        author=row.get("original_author", ""),
        license=row.get("license", ""),
        commercial_ok=row.get("commercial_use_ok", "").upper().startswith("Y"),
        link=row.get("link", ""),
        folder=folder,
        description=meta.get("description", "") or "",
        choose="one" if str(meta.get("choose", "any")).lower() == "one" else "any",
    )
    from .intake import unpack_zips  # unpack anything dropped in as a .zip

    unpack_zips(folder)
    model_paths, doc_paths = scan_folder(folder)

    file_meta = meta.get("files") or {}
    part.configured = bool(file_meta)
    cache, cache_file = _sizes_cache(folder)
    n_cache = len(cache)
    for p in model_paths:
        rel = p.relative_to(folder).as_posix()
        fm = file_meta.get(rel, {}) or {}
        if fm.get("hide"):
            continue
        mf = ModelFile(rel=rel, path=p, label=str(fm.get("label") or nice_label(p.stem)),
                       default=bool(fm.get("default", False)), count=int(fm.get("count", 1) or 1))
        if mf.kind == "mesh":
            mf.size_mm = _mesh_size(p, cache)
        part.files.append(mf)
    if len(cache) != n_cache:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    # Two files can't share a label in the operator list.
    seen: dict[str, int] = {}
    for mf in part.files:
        seen[mf.label.lower()] = seen.get(mf.label.lower(), 0) + 1
    for mf in part.files:
        if seen[mf.label.lower()] > 1:
            parent = Path(mf.rel).parent.name
            mf.label = f"{mf.label} ({nice_label(parent) if parent else mf.path.suffix[1:].upper()})"

    for p in doc_paths:
        ext = p.suffix.lower()
        kind = "image" if ext in IMAGE_EXTS else "pdf" if ext in PDF_EXTS else "text"
        part.docs.append(Doc(path=p, kind=kind, title=nice_label(p.stem)))

    if any(f.kind == "scad" for f in part.files):
        part.params += [ParamSpec(**p) for p in meta.get("params", [])]
    if any(f.kind == "mesh" for f in part.files):
        part.params.append(ParamSpec(**{**SCALE_PARAM.to_dict(), **(meta.get("scale") or {})}))
    return part


def read_catalog_rows(catalog_csv: Path) -> list[dict]:
    rows, seen = [], set()
    with open(catalog_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("part_name"):
                continue
            pid = row.get("part_id") or slugify(row["part_name"])
            if pid in seen:  # duplicate rows: keep the first
                continue
            seen.add(pid)
            row["part_id"] = pid
            rows.append(row)
    return rows


def load_catalog(catalog_csv: Path | None = None, models_dir: Path | None = None) -> list[Part]:
    cfg = settings()["library"]
    catalog_csv = catalog_csv or path(cfg["catalog_csv"])
    models_dir = models_dir or path(cfg["models_dir"])
    return [build_part(r, models_dir) for r in read_catalog_rows(catalog_csv)]


def get_part(parts: list[Part], part_id: str) -> Part | None:
    return next((p for p in parts if p.part_id == part_id), None)


def search(parts: list[Part], text: str = "", category: str | None = None) -> list[Part]:
    words = text.lower().split()
    out = []
    for p in parts:
        if category and category != "All" and p.category != category:
            continue
        hay = f"{p.name} {p.category} {p.description} {' '.join(f.label for f in p.files)}".lower()
        if all(w in hay for w in words):
            out.append(p)
    return sorted(out, key=lambda p: (not p.printable, p.name.lower()))


def ensure_thumbnails(parts: list[Part]) -> int:
    """Render a picture for every model file that doesn't have one yet."""
    from .cad import build_piece, render_thumbnail

    made = 0
    for p in parts:
        for mf in p.files:
            out = thumb_path(p, mf)
            if out.exists() and out.stat().st_mtime >= mf.path.stat().st_mtime:
                continue
            try:
                src = mf.path
                if mf.kind == "scad":
                    src = build_piece(p, mf, p.defaults(), p.folder / THUMBS / "_scad_preview.stl").stl
                render_thumbnail(src, out)
                made += 1
            except Exception:
                pass  # a missing picture should never stop the shop
    return made


# Sheet sync lives in sheet.py; re-exported so older imports keep working.
from .sheet import sync_from_sheet  # noqa: E402,F401
