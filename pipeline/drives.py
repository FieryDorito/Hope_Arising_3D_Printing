"""Find SD cards / USB sticks plugged into the shop computer and copy print files onto them.

The app runs on the shop computer itself, so it can write to the card directly:
the worker never has to find a downloaded file.

    list_removable()            cards and sticks plugged in right now
    copy_to_drive(files, drive) copy G-code files to the top folder of the card

On Windows only drives Windows calls "removable" are listed, so the app can never write
to the computer's own hard disk by mistake. Some large USB hard drives call themselves
"fixed" and won't show up; SD cards and normal USB sticks do.
"""
from __future__ import annotations

import os
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path


class DriveError(Exception):
    """Copying failed. Message is shown to the operator."""


@dataclass
class Drive:
    path: Path
    label: str
    free_bytes: int | None = None

    @property
    def name(self) -> str:
        """What the worker sees, e.g. 'SD CARD (E:)'."""
        if sys.platform == "win32":
            letter = str(self.path)[:2]
            return f"{self.label} ({letter})" if self.label else f"Card / USB ({letter})"
        return self.label or self.path.name


def _free(p: Path) -> int | None:
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def _windows_drives() -> list[Drive]:
    import ctypes

    k32 = ctypes.windll.kernel32
    out = []
    mask = k32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) != 2:      # 2 = DRIVE_REMOVABLE
            continue
        name = ctypes.create_unicode_buffer(261)
        ok = k32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, None, None, None, None, 0)
        if not ok:                                              # empty card reader slot
            continue
        out.append(Drive(Path(root), name.value, _free(Path(root))))
    return out


def _mounted_under(bases: list[Path]) -> list[Drive]:
    out = []
    for base in bases:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and os.path.ismount(d):
                out.append(Drive(d, d.name, _free(d)))
    return out


def list_removable() -> list[Drive]:
    try:
        if sys.platform == "win32":
            return _windows_drives()
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        if sys.platform == "darwin":
            return [d for d in _mounted_under([Path("/Volumes")]) if os.path.realpath(d.path) != "/"]
        return _mounted_under([Path("/media") / user, Path("/run/media") / user, Path("/media")])
    except Exception:
        return []


def copy_to_drive(files: list[Path], drive: Path) -> list[Path]:
    """Copy each file to the top folder of the card. Returns where they went."""
    if not drive.exists():
        raise DriveError("The card or USB stick was taken out. Put it back in and try again.")
    need = sum(f.stat().st_size for f in files)
    free = _free(drive)
    if free is not None and free < need + 1_000_000:
        raise DriveError("The card is full. Delete some old print files from it and try again.")
    written = []
    try:
        for f in files:
            dest = drive / f.name
            with open(f, "rb") as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())          # make sure it is really on the card
            written.append(dest)
    except PermissionError as e:
        raise DriveError("The card is locked. Slide the small lock switch on the side of the "
                         "SD card up, put it back in, and try again.") from e
    except OSError as e:
        raise DriveError("Could not write to the card. Take it out, put it back in, and try again.") from e
    return written
