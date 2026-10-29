"""OpenSCAD Customizer settings, diagonal fitting, and CAD-only folders."""
import trimesh

from pipeline.cad import _turn_to_fit
from pipeline.library import load_catalog, get_part
from pipeline.scad_params import read_params
from pipeline.validate import check_request, fit_angle

SCAD = """// demo
/* [Main] */
// Overall width of the bracket.
width = 40;
knobs = 3;            // [1:1:9]
style = 1;            // [0:Plain, 1:Rounded]
cols = 2;             // [1,2]
label = "hi";
$fn = 64;
formula = width * 2;
/* [Hidden] */
secret = 5;
module bracket() { cube([width, 10, 5]); }
bracket();
"""


def test_customizer_settings_read(tmp_path):
    f = tmp_path / "m.scad"
    f.write_text(SCAD)
    ps = {p["name"]: p for p in read_params(f)}
    assert set(ps) == {"width", "knobs", "style", "cols"}
    assert ps["width"]["basic"] and ps["width"]["help"].startswith("Overall width")
    assert (ps["knobs"]["min"], ps["knobs"]["max"], ps["knobs"]["kind"]) == (1, 9, "int")
    assert ps["style"]["kind"] == "choice" and ps["style"]["choices"][1] == [1.0, "Rounded"]
    assert [v for v, _ in ps["cols"]["choices"]] == [1.0, 2.0]


def test_scad_part_without_yaml_gets_settings(tmp_path):
    models = tmp_path / "models"
    d = models / "Utility" / "bracket"
    d.mkdir(parents=True)
    (d / "bracket.scad").write_text(SCAD)
    csv = tmp_path / "s.csv"
    csv.write_text("part_name,category,commercial_use_ok\nBracket,Utility,YES\n")
    p = get_part(load_catalog(csv, models), "bracket")
    assert [x.name for x in p.params] == ["width", "knobs", "style", "cols"]
    assert check_request(p, {"style": 0}, "PLA", "Normal").ok
    assert not check_request(p, {"style": 7}, "PLA", "Normal").ok


def test_long_part_fits_diagonally():
    assert fit_angle(100, 50) == 0
    assert fit_angle(230, 57) not in (None, 0)      # screwdriver holder
    assert fit_angle(300, 100) is None
    m = trimesh.creation.box([230, 40, 20])
    _turn_to_fit(m)
    x, y, _ = m.extents
    assert x <= 210 and y <= 210


def test_cad_source_only_folder(tmp_path):
    models = tmp_path / "models"
    d = models / "Household" / "funnel"
    d.mkdir(parents=True)
    (d / "funnel.FCStd").write_bytes(b"PK")
    csv = tmp_path / "s.csv"
    csv.write_text("part_name,category,commercial_use_ok\nFunnel,Household,YES\n")
    p = get_part(load_catalog(csv, models), "funnel")
    assert not p.printable and "FCSTD" in p.status
