"""Adding files to the library from inside the app, and saving a part's setup.

  add_files()     save uploaded/dragged files into a part's folder (zips are unpacked)
  unpack_zips()   unpack any .zip in a part folder into a sub-folder (safely)
  save_setup()    write labels / default file / one-or-many choice into part.yaml
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

import yaml

MAX_UNZIP_BYTES = 500 * 1024 * 1024   # refuse zip bombs
MAX_FILES_IN_ZIP = 2000


def safe_name(name: str) -> str:
    """Keep only a plain file name (no folders, no odd characters)."""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip(" .")
    return base or "file"


def _unzip(zf: Path, dest: Path) -> int:
    n = 0
    with zipfile.ZipFile(zf) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES_IN_ZIP or sum(i.file_size for i in infos) > MAX_UNZIP_BYTES:
            raise ValueError(f"{zf.name} is too large to unpack.")
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        for i in infos:
            parts = [p for p in PurePosixPath(i.filename.replace("\\", "/")).parts
                     if p not in ("", ".", "..") and not p.endswith(":")]
            if not parts or parts[0].lower() == "__macosx":
                continue
            target = (dest.joinpath(*[safe_name(p) for p in parts])).resolve()
            if root not in target.parents:  # never write outside the part folder
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as out:
                out.write(src.read())
            n += 1
    return n


def unpack_zips(folder: Path) -> list[str]:
    """Unpack each zip once, into a sub-folder named after it. Returns messages."""
    msgs = []
    if not folder.is_dir():
        return msgs
    for zf in sorted(folder.rglob("*.zip")):
        if any(p.startswith(".") for p in zf.relative_to(folder).parts):
            continue
        dest = zf.with_suffix("")
        if dest.exists():
            continue
        try:
            n = _unzip(zf, dest)
            msgs.append(f"Unpacked {zf.name} ({n} files).")
        except (zipfile.BadZipFile, ValueError, OSError) as e:
            msgs.append(f"Could not unpack {zf.name}: {e}")
    return msgs


def add_files(folder: Path, uploads: list[tuple[str, bytes]]) -> list[str]:
    """Save uploaded files into the part folder. Returns plain-language messages."""
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / "PUT-STL-HERE.txt"
    msgs = []
    for name, data in uploads:
        target = folder / safe_name(name)
        stem, ext, i = target.stem, target.suffix, 2
        while target.exists():          # never overwrite an existing file
            target = folder / f"{stem} ({i}){ext}"
            i += 1
        target.write_bytes(data)
        msgs.append(f"Saved {target.name}.")
    msgs += unpack_zips(folder)
    if uploads and note.exists():
        note.unlink()
    return msgs


def save_setup(folder: Path, choose: str, files: dict[str, dict], description: str | None = None) -> Path:
    """Merge the operator-facing setup into part.yaml, keeping anything else already there.

    files: {rel_path: {"label": str, "default": bool, "count": int, "hide": bool}}
    """
    f = folder / "part.yaml"
    meta = {}
    if f.exists():
        meta = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    meta["choose"] = "one" if choose == "one" else "any"
    clean = {}
    for rel, v in files.items():
        entry = {"label": str(v.get("label") or "").strip() or Path(rel).stem,
                 "default": bool(v.get("default")),
                 "count": max(1, int(v.get("count") or 1))}
        if v.get("hide"):
            entry["hide"] = True
        clean[rel] = entry
    meta["files"] = clean
    if description is not None:
        meta["description"] = description.strip()
    folder.mkdir(parents=True, exist_ok=True)
    f.write_text("# Set up in the app (Library setup). Safe to edit by hand.\n"
                 + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return f
