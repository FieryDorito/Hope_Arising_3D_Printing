"""Past prints, saving to an SD card / USB stick, Wi-Fi printers, and finding OpenSCAD."""
import json
import os
from pathlib import Path

import pytest

from pipeline import cad, config, drives, history, netprint


def _fake_job(root: Path, name: str, saved_at: str, gcode=True, **req) -> Path:
    d = root / name
    d.mkdir(parents=True)
    info = {"request": {"part_id": "test-spacer", "params": {"outer_diameter": 20}, "material": "PETG",
                        "strength": "Normal", "files": {"model.scad": 2}, "customer_request": "", **req},
            "final_params": {"outer_diameter": 20.0}, "files": {"model.scad": 2},
            "plates": [{"print_time_s": 600, "filament_g": 4.0}, {"print_time_s": 300, "filament_g": 2.0}],
            "cost": 10.8, "saved_at": saved_at}
    (d / "job.json").write_text(json.dumps(info), encoding="utf-8")
    if gcode:
        (d / "a_plate1of2.gcode").write_text("G28\n", encoding="utf-8")
        (d / "a_plate2of2.gcode").write_text("G28\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------- past prints

def test_list_jobs_newest_first_and_only_with_gcode(tmp_path):
    _fake_job(tmp_path, "old", "2026-11-01T10:00:00")
    _fake_job(tmp_path, "new", "2026-11-05T10:00:00", customer_request="cap for a pipe")
    _fake_job(tmp_path, "preview-only", "2026-11-06T10:00:00", gcode=False)
    jobs = history.list_jobs(tmp_path)
    assert [j.folder.name for j in jobs] == ["new", "old"]
    j = jobs[0]
    assert j.params == {"outer_diameter": 20.0}          # the final (checked) settings
    assert j.files == {"model.scad": 2}
    assert j.print_time_s == 900 and j.filament_g == 6.0 and j.cost == 10.8
    assert len(j.gcodes) == 2 and j.customer_request == "cap for a pipe"
    assert j.status == history.DEFAULT_STATUS and j.customer == ""


def test_save_order_merges_and_checks_status(tmp_path):
    d = _fake_job(tmp_path, "job", "2026-11-05T10:00:00")
    history.save_order(d, customer=" Mulu ", phone="0911 000000")
    history.save_order(d, status="Printing", notes="too tight")
    j = history.load_job(d)
    assert (j.customer, j.phone, j.status, j.notes) == ("Mulu", "0911 000000", "Printing", "too tight")
    history.save_order(d, status="nonsense")
    assert history.load_job(d).status == history.DEFAULT_STATUS


def test_list_jobs_handles_missing_folder(tmp_path):
    assert history.list_jobs(tmp_path / "nope") == []


# ---------------------------------------------------------------- SD card / USB stick

def test_copy_to_drive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "spacer_PLA.gcode"
    f.write_text("G28\nG1 X10\n", encoding="utf-8")
    card = tmp_path / "card"
    card.mkdir()
    written = drives.copy_to_drive([f], card)
    assert written == [card / f.name]
    assert (card / f.name).read_text(encoding="utf-8") == "G28\nG1 X10\n"


def test_copy_to_drive_errors_are_plain(tmp_path, monkeypatch):
    f = tmp_path / "x.gcode"
    f.write_text("G28\n", encoding="utf-8")
    with pytest.raises(drives.DriveError, match="taken out"):
        drives.copy_to_drive([f], tmp_path / "gone")
    monkeypatch.setattr(drives, "_free", lambda p: 10)
    with pytest.raises(drives.DriveError, match="full"):
        drives.copy_to_drive([f], tmp_path)


def test_list_removable_never_crashes(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no drives api")
    monkeypatch.setattr(drives, "_mounted_under", boom)
    monkeypatch.setattr(drives, "_windows_drives", boom)
    assert drives.list_removable() == []


def test_drive_name():
    d = drives.Drive(Path("/media/shop/SDCARD"), "SDCARD")
    assert "SDCARD" in d.name


# ---------------------------------------------------------------- Wi-Fi printers

def test_no_network_printers_by_default():
    assert netprint.configured_printers() == []


def test_send_posts_to_moonraker(tmp_path, monkeypatch):
    f = tmp_path / "part.gcode"
    f.write_text("G28\n", encoding="utf-8")
    seen = {}

    class R:
        status_code = 201

    def fake_post(url, files, data, timeout):
        seen.update(url=url, name=files["file"][0], data=data)
        return R()
    monkeypatch.setattr(netprint.requests, "post", fake_post)
    netprint.send(netprint.NetPrinter("K1C", "192.168.1.50"), f, start=True)
    assert seen["url"] == "http://192.168.1.50/server/files/upload"
    assert seen["name"] == "part.gcode" and seen["data"]["print"] == "true"


def test_send_unreachable_printer_gives_plain_message(tmp_path, monkeypatch):
    f = tmp_path / "part.gcode"
    f.write_text("G28\n", encoding="utf-8")

    def fail(*a, **k):
        raise netprint.requests.ConnectionError()
    monkeypatch.setattr(netprint.requests, "post", fail)
    with pytest.raises(netprint.NetPrintError, match="same Wi-Fi"):
        netprint.send(netprint.NetPrinter("K1C", "10.0.0.9"), f)


# ---------------------------------------------------------------- finding OpenSCAD

def test_openscad_found_in_windows_install_folder(tmp_path, monkeypatch):
    """The Windows installer doesn't add OpenSCAD to PATH; the app must still find it."""
    exe = tmp_path / "Program Files" / "OpenSCAD" / "openscad.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(cad.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    assert cad._openscad_exe() == str(exe)


def test_openscad_path_from_settings(tmp_path, monkeypatch):
    exe = tmp_path / "custom" / "openscad.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    monkeypatch.setitem(config.settings(), "cad", {"openscad_executable": str(exe)})
    assert cad._openscad_exe() == str(exe)
    monkeypatch.setitem(config.settings(), "cad", {"openscad_executable": str(tmp_path / "missing.exe")})
    with pytest.raises(cad.CadError, match="settings.local.yaml"):
        cad._openscad_exe()


def test_openscad_missing_message(monkeypatch):
    monkeypatch.setattr(cad.shutil, "which", lambda name: None)
    monkeypatch.setattr(cad, "_openscad_guesses", lambda: [Path("/definitely/not/here")])
    with pytest.raises(cad.CadError, match="not installed"):
        cad._openscad_exe()
