"""Keep the local catalog (models/sources.csv) in step with the Google Sheet.

  check_for_updates()  download the sheet and compare with the local copy (writes nothing)
  apply_update()       save the downloaded sheet as the new local copy (old one kept as .bak)
  sync_from_sheet()    both at once (the "Update library" button)
"""
from __future__ import annotations

import csv
import io
import shutil
from dataclasses import dataclass, field

import requests

from .config import path, settings

WATCHED = ("part_name", "category", "license", "commercial_use_ok", "link")


@dataclass
class SheetUpdate:
    ok: bool
    message: str
    text: str = ""
    new: list[dict] = field(default_factory=list)       # rows added to the sheet
    removed: list[dict] = field(default_factory=list)   # rows no longer in the sheet
    changed: list[tuple[dict, list[str]]] = field(default_factory=list)  # (new row, fields)

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.removed or self.changed)


def sheet_csv_url() -> str:
    cfg = settings()["library"]
    if cfg.get("sheet_csv_url"):        # optional override (testing, or a different export)
        return cfg["sheet_csv_url"]
    return (f"https://docs.google.com/spreadsheets/d/{cfg['sheet_id']}/export"
            f"?format=csv&gid={cfg.get('sheet_gid', 0)}")


def sheet_edit_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{settings()['library']['sheet_id']}/edit"


def _rows(text: str) -> list[dict]:
    from .library import slugify

    out, seen = [], set()
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("part_name"):
            continue
        row["part_id"] = row.get("part_id") or slugify(row["part_name"])
        if row["part_id"] not in seen:
            seen.add(row["part_id"])
            out.append(row)
    return out


def diff(old_text: str, new_text: str) -> tuple[list, list, list]:
    old = {r["part_id"]: r for r in _rows(old_text)}
    new = {r["part_id"]: r for r in _rows(new_text)}
    added = [r for pid, r in new.items() if pid not in old]
    removed = [r for pid, r in old.items() if pid not in new]
    changed = []
    for pid, r in new.items():
        if pid in old:
            fields = [f for f in WATCHED if (old[pid].get(f, "") or "") != (r.get(f, "") or "")]
            if fields:
                changed.append((r, fields))
    return added, removed, changed


def check_for_updates(timeout: float = 15) -> SheetUpdate:
    try:
        r = requests.get(sheet_csv_url(), timeout=timeout)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    except requests.RequestException:
        return SheetUpdate(False, "Could not reach Google Sheets. Using the saved copy of the library.")
    if "<html" in text[:500].lower():
        return SheetUpdate(False, "The library sheet is not shared publicly. Ask a maintainer to set it to "
                                  "'Anyone with the link can view'. Using the saved copy.")
    header = [h.strip().lower() for h in next(csv.reader(io.StringIO(text)), [])]
    if "part_name" not in header:
        return SheetUpdate(False, "The sheet's first row changed (no 'part_name' column). Using the saved copy.")

    target = path(settings()["library"]["catalog_csv"])
    old = target.read_text(encoding="utf-8-sig") if target.exists() else ""
    added, removed, changed = diff(old, text)
    n = len(_rows(text))
    up = SheetUpdate(True, f"Library has {n} parts.", text, added, removed, changed)
    if up.has_changes:
        bits = [f"{len(added)} new"] if added else []
        bits += [f"{len(changed)} changed"] if changed else []
        bits += [f"{len(removed)} removed"] if removed else []
        up.message = f"Library sheet updated: {', '.join(bits)}."
    else:
        up.message = f"Library is up to date ({n} parts)."
    return up


def apply_update(up: SheetUpdate) -> None:
    if not up.ok or not up.text:
        return
    target = path(settings()["library"]["catalog_csv"])
    if target.exists():
        shutil.copy2(target, target.with_suffix(".csv.bak"))
    target.write_text(up.text, encoding="utf-8")


def sync_from_sheet(timeout: float = 20) -> tuple[bool, str]:
    up = check_for_updates(timeout)
    if up.ok:
        apply_update(up)
    return up.ok, up.message
