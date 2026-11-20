"""More than one printer: choosing one, bed checks per printer, and .3mf output for
printers that print from their own slicer (Bambu Lab)."""
import json
import zipfile

import pytest
import trimesh

from pipeline import config
from pipeline.config import SettingsError, get_printer, printers
from pipeline.history import list_jobs
from pipeline.library import get_part
from pipeline.run_job import JobRequest, run_job
from pipeline.validate import check_fits, check_request

ENDER, P2S = "Creality Ender 3", "Bambu Lab P2S"


@pytest.fixture
def p2s_as_3mf(monkeypatch):
    """The P2S in 'open it in Bambu Studio yourself' mode (output: 3mf)."""
    ps = [dict(p, output="3mf") if p["name"] == P2S else p for p in config.settings()["printers"]]
    monkeypatch.setitem(config.settings(), "printers", ps)


def test_shop_printers_listed_with_ender_first():
    names = [p["name"] for p in printers()]
    assert names[0] == ENDER and P2S in names
    assert get_printer()["name"] == ENDER
    assert get_printer("no such printer")["name"] == ENDER
    assert get_printer(P2S)["output"] == "bambu" and get_printer(ENDER)["output"] == "gcode"


def test_old_single_printer_setting_still_works(monkeypatch):
    s = config.settings()
    monkeypatch.delitem(s, "printers")
    monkeypatch.setitem(s, "printer", {"name": "Old Ender", "profile": "config/printers/ender3.ini",
                                       "bed_x_mm": 220, "bed_y_mm": 220, "max_z_mm": 250, "margin_mm": 5})
    [p] = printers()
    assert p["name"] == "Old Ender" and p["output"] == "gcode"


@pytest.mark.parametrize("bad", [
    {"name": "X", "output": "stl", "bed_x_mm": 1, "bed_y_mm": 1, "max_z_mm": 1},
    {"name": "X", "output": "gcode", "bed_x_mm": 1, "bed_y_mm": 1, "max_z_mm": 1},   # gcode needs a profile
    {"name": "X", "output": "3mf", "bed_x_mm": 200},                                 # no bed size
])
def test_broken_printer_settings_give_plain_message(monkeypatch, bad):
    monkeypatch.setitem(config.settings(), "printers", [bad])
    with pytest.raises(SettingsError):
        printers()


def test_bed_size_depends_on_the_printer():
    big = (230, 230, 100)                    # too wide for the Ender, fine on the P2S
    assert check_fits(big, get_printer(ENDER))
    assert check_fits(big, get_printer(P2S)) == []
    assert "too tall" in check_fits((50, 50, 257), get_printer(P2S))[0]
    assert check_fits((50, 50, 256), get_printer(P2S)) == []


def test_unknown_printer_is_rejected(test_library):
    hook = get_part(test_library, "test-hook")
    c = check_request(hook, {}, "PLA", "Normal", printer="Mystery 9000")
    assert not c.ok and "Unknown printer" in c.errors[0]


def test_3mf_printer_job_makes_a_3mf_not_gcode(test_library, p2s_as_3mf):
    hook = get_part(test_library, "test-hook")
    job = run_job(JobRequest("test-hook", {}, files={hook.files[0].rel: 3}, printer=P2S), test_library)
    assert job.ok, job.errors
    f = job.slice.gcode
    assert f.suffix == ".3mf"
    assert not list(job.folder.glob("*.gcode")), "no G-code may be offered for a Bambu printer"
    # A real 3MF with every copy as its own object, all inside the 256 mm bed.
    assert "3D/3dmodel.model" in zipfile.ZipFile(f).namelist()
    scene = trimesh.load(f)
    assert len(scene.geometry) == 3
    lo, hi = scene.bounds
    assert lo[0] >= 0 and lo[1] >= 0 and hi[0] <= 256 and hi[1] <= 256 and abs(lo[2]) < 0.01
    assert abs((lo[0] + hi[0]) / 2 - 128) < 1 and abs((lo[1] + hi[1]) / 2 - 128) < 1
    # Estimates still work, so the shop still sees a cost.
    assert job.print_time_s and job.filament_g and job.cost
    info = json.loads((job.folder / "job.json").read_text())
    assert info["printer"] == P2S and info["output"] == "3mf"
    [past] = list_jobs(job.folder.parent)
    assert past.printer == P2S and past.output == "3mf" and past.gcodes == [f]


def test_3mf_printer_without_estimate_profile_still_gives_file(test_library, monkeypatch, p2s_as_3mf):
    ps = [dict(p) for p in config.settings()["printers"]]
    for p in ps:
        if p["name"] == P2S:
            p.pop("profile")
    monkeypatch.setitem(config.settings(), "printers", ps)
    job = run_job(JobRequest("test-hook", {}, printer=P2S), test_library)
    assert job.ok, job.errors
    assert job.slice.gcode.suffix == ".3mf" and job.print_time_s is None and job.cost is None


def test_ender_job_unchanged(test_library):
    job = run_job(JobRequest("test-hook", {}, printer=ENDER), test_library)
    assert job.ok, job.errors
    assert job.slice.gcode.suffix == ".gcode"
    assert json.loads((job.folder / "job.json").read_text())["printer"] == ENDER
