"""End-to-end: request -> validated params -> STL -> G-code."""
import re

import pytest

from pipeline.library import get_part, load_catalog
from pipeline.run_job import JobRequest, prepare, run_job
from pipeline.validate import check_request

BED = 220


def _xy_extent(gcode_path):
    xs, ys = [], []
    for line in open(gcode_path, encoding="utf-8"):
        if line.startswith("G1") and " E" in line:
            m = re.search(r"X([-\d.]+)", line); xs += [float(m.group(1))] if m else []
            m = re.search(r"Y([-\d.]+)", line); ys += [float(m.group(1))] if m else []
    return min(xs), max(xs), min(ys), max(ys)


def test_validation_blocks_bad_input(test_library):
    spacer = get_part(test_library, "test-spacer")
    c = check_request(spacer, {"outer_diameter": 500}, "PLA", "Normal")
    assert not c.ok and "Outside diameter" in c.errors[0]
    locked = get_part(test_library, "locked-part")
    assert not check_request(locked, {}, "PLA", "Normal").ok
    hook = get_part(test_library, "test-hook")
    assert check_request(hook, {"scale_percent": 400}, "PLA", "Normal").ok      # 120x132x240 fits
    hook.files[0].size_mm = (30.0, 33.0, 70.0)
    assert not check_request(hook, {"scale_percent": 400}, "PLA", "Normal").ok  # 280 mm: too tall


def test_scad_part_resized(test_library):
    job = prepare(JobRequest("test-spacer", {"outer_diameter": 30, "inner_diameter": 10, "height": 5}), test_library)
    assert job.ok, job.errors
    x, y, z = job.model.size_mm
    assert abs(x - 30) < 0.3 and abs(z - 5) < 0.01


def test_mesh_part_scaled(test_library):
    job = prepare(JobRequest("test-hook", {"scale_percent": 50}), test_library)
    assert job.ok, job.errors
    assert job.model.size_mm == (15.0, 16.5, 30.0)


@pytest.mark.parametrize("part_id,material,strength", [
    ("test-hook", "PLA", "Strong"),
    ("test-spacer", "PETG", "Normal"),
])
def test_end_to_end_gcode(test_library, part_id, material, strength):
    job = run_job(JobRequest(part_id, {}, material, strength), test_library)
    assert job.ok, job.errors
    g = job.slice.gcode
    text = g.read_text(encoding="utf-8")
    assert "Hope Arising 3D Print Shop" in text.splitlines()[0]
    assert "Credit:" in text[:2000]
    temp = "240" if material == "PETG" else "210"
    assert f"M104 S{temp}" in text or f"M109 S{temp}" in text
    assert job.slice.print_time_s and job.slice.print_time_s > 60
    assert job.slice.filament_g and job.slice.filament_g > 0
    assert job.cost and job.cost > 0
    x0, x1, y0, y1 = _xy_extent(g)
    assert 0 <= x0 and x1 <= BED and 0 <= y0 and y1 <= BED
    assert (job.folder / "job.json").exists()


# Every real part with a model file must make valid G-code at its default settings.
# Adding a part to library/models/ automatically adds it to this test.
REAL = [p for p in load_catalog() if p.printable]


@pytest.mark.parametrize("part", REAL, ids=[p.part_id for p in REAL])
def test_every_library_part_slices(part):
    job = run_job(JobRequest(part.part_id, part.defaults()), load_catalog())
    assert job.ok, f"{part.name}: {job.errors}"
