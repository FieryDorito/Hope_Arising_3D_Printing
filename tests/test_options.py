"""Parts with several files: zips, options/pieces, instructions, plates, uploads, sheet diff."""
import io
import zipfile

import trimesh

from pipeline.agents.base import library_brief
from pipeline.intake import add_files, save_setup
from pipeline.library import get_part, load_catalog
from pipeline.run_job import JobRequest, run_job
from pipeline.sheet import diff
from pipeline.validate import check_request

CATALOG = """part_name,category,original_author,license,commercial_use_ok,link
Salt Shaker,Household,Someone,Public Domain,YES,https://example.com/shaker
Big Tray,Household,Someone,Public Domain,YES,https://example.com/tray
"""


def stl_bytes(mesh) -> bytes:
    return trimesh.exchange.stl.export_stl(mesh)


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def build_lib(tmp_path):
    models = tmp_path / "models"
    shaker = models / "Household" / "salt-shaker"
    shaker.mkdir(parents=True)
    (shaker / "PUT-STL-HERE.txt").write_text("note")
    body = trimesh.creation.cylinder(radius=15, height=40)
    cap = trimesh.creation.cylinder(radius=16, height=8)
    (shaker / "shaker-files.zip").write_bytes(make_zip({
        "files/body_v2.stl": stl_bytes(body),
        "files/cap.stl": stl_bytes(cap),
        "README.txt": "Print the cap with 0.2 mm layers. Screw on gently.",
        "../escape.txt": "should not leave the folder",
        "__MACOSX/._junk": "x",
    }))
    (shaker / "photo.jpg").write_bytes(b"\xff\xd8\xff")  # tiny fake image
    tray = models / "Household" / "big-tray"
    tray.mkdir(parents=True)
    (tray / "tray.stl").write_bytes(stl_bytes(trimesh.creation.box([150, 150, 10])))
    csv = tmp_path / "sources.csv"
    csv.write_text(CATALOG)
    return csv, models


def test_zip_unpacked_and_files_found(tmp_path):
    csv, models = build_lib(tmp_path)
    p = get_part(load_catalog(csv, models), "salt-shaker")
    assert [f.label for f in p.files] == ["Body v2", "Cap"]
    assert [f.rel for f in p.files] == ["shaker-files/files/body_v2.stl", "shaker-files/files/cap.stl"]
    assert "README" in [d.path.stem for d in p.instructions] and p.images[0].path.name == "photo.jpg"
    assert p.thumbnail.name == "photo.jpg"          # designer photo used as library picture
    # "../escape.txt" in the zip is kept INSIDE the unpack folder, never above it
    assert (p.folder / "shaker-files" / "escape.txt").exists()
    assert not (p.folder / "escape.txt").exists() and not (tmp_path / "escape.txt").exists()
    assert p.needs_setup and p.default_selection() == {"shaker-files/files/body_v2.stl": 1}


def test_setup_saved_and_reloaded(tmp_path):
    csv, models = build_lib(tmp_path)
    p = get_part(load_catalog(csv, models), "salt-shaker")
    save_setup(p.folder, "any", {
        "shaker-files/files/body_v2.stl": {"label": "Body", "default": True, "count": 1},
        "shaker-files/files/cap.stl": {"label": "Lid", "default": True, "count": 2},
    }, description="Small salt shaker with screw lid.")
    p = get_part(load_catalog(csv, models), "salt-shaker")
    assert not p.needs_setup and [f.label for f in p.files] == ["Body", "Lid"]
    assert p.default_selection() == {"shaker-files/files/body_v2.stl": 1, "shaker-files/files/cap.stl": 2}
    assert "Lid" in library_brief([p]) and "screw lid" in p.description


def test_choose_one_enforced(tmp_path):
    csv, models = build_lib(tmp_path)
    p = get_part(load_catalog(csv, models), "salt-shaker")
    save_setup(p.folder, "one", {f.rel: {"label": f.label} for f in p.files})
    p = get_part(load_catalog(csv, models), "salt-shaker")
    both = {f.rel: 1 for f in p.files}
    assert not check_request(p, {}, "PLA", "Normal", both).ok
    assert check_request(p, {}, "PLA", "Normal", {p.files[1].rel: 3}).ok


def test_several_pieces_one_plate(tmp_path):
    csv, models = build_lib(tmp_path)
    parts = load_catalog(csv, models)
    p = get_part(parts, "salt-shaker")
    job = run_job(JobRequest("salt-shaker", {}, files={p.files[0].rel: 1, p.files[1].rel: 2}), parts)
    assert job.ok, job.errors
    assert len(job.plates) == 1 and job.slice.filament_g > 0
    head = job.slice.gcode.read_text()[:1500]
    assert "1 x Body v2" in head and "2 x Cap" in head


def test_too_many_for_one_bed_makes_more_plates(tmp_path):
    csv, models = build_lib(tmp_path)
    parts = load_catalog(csv, models)
    p = get_part(parts, "big-tray")
    job = run_job(JobRequest("big-tray", {}, files={p.files[0].rel: 2}), parts)
    assert job.ok, job.errors
    assert len(job.plates) == 2 and job.print_time_s > 0
    names = [pl.result.gcode.name for pl in job.plates]
    assert names[0].endswith("plate1of2.gcode") and names[1].endswith("plate2of2.gcode")
    assert len(zipfile.ZipFile(job.zip_all()).namelist()) == 2


def test_add_files_never_overwrites(tmp_path):
    folder = tmp_path / "models" / "Household" / "new-part"
    folder.mkdir(parents=True)
    (folder / "PUT-STL-HERE.txt").write_text("note")
    msgs = add_files(folder, [("../../sneaky name.stl", b"a"), ("sneaky name.stl", b"b"),
                              ("bundle.zip", make_zip({"inner.stl": b"c", "how-to.pdf": b"%PDF"}))])
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["bundle", "bundle.zip", "sneaky name (2).stl", "sneaky name.stl"]
    assert (folder / "bundle" / "inner.stl").exists() and any("Unpacked" in m for m in msgs)


def test_sheet_diff():
    old = "part_name,license,link\nA,PD,l1\nB,PD,l2\n"
    new = "part_name,license,link\nA,PD,l1-new\nC,PD,l3\n"
    added, removed, changed = diff(old, new)
    assert [r["part_name"] for r in added] == ["C"]
    assert [r["part_name"] for r in removed] == ["B"]
    assert changed[0][0]["part_name"] == "A" and changed[0][1] == ["link"]
