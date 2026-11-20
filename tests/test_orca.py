"""OrcaSlicer for every printer (Bambu P2S -> .gcode.3mf, Ender 3 -> .gcode), falling back when
it's missing, the profile tool, and sending to Bambu printers over Wi-Fi (printer faked)."""
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from pipeline import bambu_lan, config
from pipeline.history import list_jobs
from pipeline.library import get_part
from pipeline.run_job import JobRequest, run_job
from pipeline.slicers.orcaslicer import find_orca, read_3mf_stats
from tools.make_slicer_profiles import ProfileError, flatten, make

P2S, ENDER = "Bambu Lab P2S", "Creality Ender 3"
ROOT = Path(__file__).resolve().parent.parent

# A stand-in for the OrcaSlicer command line: checks the arguments it gets and writes
# a .gcode.3mf shaped like the real one. Records what it was given in calls.json.
FAKE = r'''#!{py}
import json, sys, zipfile
from pathlib import Path
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1]
machine, process = opt("--load-settings").split(";")
p = json.load(open(process))
log = Path({log!r})
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({{"argv": a, "process": p, "machine": json.load(open(machine))["name"],
              "filament": json.load(open(opt("--load-filaments")))["name"]}})
log.write_text(json.dumps(calls))
if {fail}:
    print("Slicing failed: nothing to print"); sys.exit(255)
assert a[-1].endswith(".3mf") and Path(a[-1]).exists()
out = Path(opt("--outputdir")) / opt("--export-3mf")
with zipfile.ZipFile(out, "w") as z:
    z.writestr("Metadata/plate_1.gcode", "; HEADER_BLOCK_START\n; sliced by fake orca\nG28\n")
    z.writestr("Metadata/slice_info.config",
        '<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="1834"/>'
        '<metadata key="weight" value="12.50"/><filament id="1" type="PLA" used_m="4.19" used_g="12.50"/>'
        '</plate></config>')
'''


def fake_orca(tmp_path, monkeypatch, fail=False) -> Path:
    exe = tmp_path / "orca-slicer"
    log = tmp_path / "calls.json"
    exe.write_text(FAKE.format(py=sys.executable, log=str(log), fail=fail))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    s = config.settings()
    monkeypatch.setitem(s, "slicer", {**s["slicer"], "orca": str(exe)})
    return log


@pytest.fixture
def no_orca(monkeypatch):
    """Pretend OrcaSlicer isn't installed, even if it is on this computer."""
    from pipeline.slicers import orcaslicer
    monkeypatch.setattr(orcaslicer, "_candidates", lambda: [])
    s = config.settings()
    monkeypatch.setitem(s, "slicer", {**s["slicer"], "orca": ""})


# ---------------------------------------------------------------- slicing

@pytest.mark.skipif(os.name == "nt", reason="fake slicer is a shell script")
def test_p2s_job_is_sliced_into_ready_gcode_3mf(test_library, tmp_path, monkeypatch):
    log = fake_orca(tmp_path, monkeypatch)
    job = run_job(JobRequest("test-hook", {}, "PLA", "Strong", printer=P2S), test_library)
    assert job.ok, job.errors
    assert job.output == "bambu" and not job.warnings
    f = job.slice.gcode
    assert f.name.endswith(".gcode.3mf") and f.parent == job.folder
    assert "Metadata/plate_1.gcode" in zipfile.ZipFile(f).namelist()
    assert (job.print_time_s, job.filament_g) == (1834, 12.5)
    assert job.cost == round(12.5 / 1000 * config.settings()["materials"]["PLA"]["price_per_kg"], 2)
    [call] = json.loads(log.read_text())
    assert call["machine"] == "Bambu Lab P2S 0.4 nozzle" and call["filament"] == "Generic PLA @BBL P2S"
    assert call["process"]["wall_loops"] == "5" and call["process"]["sparse_infill_density"] == "50%"
    assert "--slice" in call["argv"] and "--arrange" in call["argv"]
    assert json.loads((job.folder / "job.json").read_text())["output"] == "bambu"
    [past] = list_jobs(job.folder.parent)
    assert past.output == "bambu" and past.gcodes == [f]


@pytest.mark.skipif(os.name == "nt", reason="fake slicer is a shell script")
def test_petg_uses_petg_profile(test_library, tmp_path, monkeypatch):
    log = fake_orca(tmp_path, monkeypatch)
    job = run_job(JobRequest("test-hook", {}, "PETG", "Normal", printer=P2S), test_library)
    assert job.ok, job.errors
    [call] = json.loads(log.read_text())
    assert call["filament"] == "Generic PETG @BBL P2S" and call["process"]["wall_loops"] == "3"


@pytest.mark.skipif(os.name == "nt", reason="fake slicer is a shell script")
def test_ender_is_sliced_by_orca_into_plain_gcode(test_library, tmp_path, monkeypatch):
    log = fake_orca(tmp_path, monkeypatch)
    job = run_job(JobRequest("test-hook", {}, "PLA", "Normal", printer=ENDER), test_library)
    assert job.ok, job.errors
    assert job.output == "gcode" and job.slicer_used == "orcaslicer"
    f = job.slice.gcode
    assert f.name.endswith(".gcode") and not f.name.endswith(".gcode.3mf")
    text = f.read_text()
    assert text.startswith("; Hope Arising 3D Print Shop") and "sliced by fake orca" in text
    [call] = json.loads(log.read_text())
    assert call["machine"] == "Creality Ender-3 0.4 nozzle" and call["filament"] == "Generic PLA @Creality"
    assert not list(job.folder.glob("*.3mf")), "Ender gets G-code only"
    assert list_jobs(job.folder.parent)[0].output == "gcode"


def test_ender_without_orca_uses_prusaslicer(test_library, no_orca):
    job = run_job(JobRequest("test-hook", {}, printer=ENDER), test_library)
    assert job.ok, job.errors
    assert job.output == "gcode" and job.slicer_used == "prusaslicer"
    assert job.slice.gcode.read_text().startswith("; Hope Arising 3D Print Shop")
    assert any("OrcaSlicer is not installed" in w for w in job.warnings)


def test_p2s_without_orca_falls_back_to_3mf(test_library, no_orca):
    job = run_job(JobRequest("test-hook", {}, printer=P2S), test_library)
    assert job.ok, job.errors
    assert job.output == "3mf" and job.slice.gcode.suffixes == [".3mf"]
    assert any("not installed" in w for w in job.warnings)
    assert job.print_time_s and job.cost          # rough estimate still shown
    assert list_jobs(job.folder.parent)[0].output == "3mf"


@pytest.mark.skipif(os.name == "nt", reason="fake slicer is a shell script")
def test_p2s_orca_failure_falls_back_to_3mf(test_library, tmp_path, monkeypatch):
    fake_orca(tmp_path, monkeypatch, fail=True)
    job = run_job(JobRequest("test-hook", {}, printer=P2S), test_library)
    assert job.ok, job.errors
    assert job.output == "3mf" and not list(job.folder.glob("*.gcode.3mf"))
    assert any("could not slice" in w for w in job.warnings)
    assert list(job.folder.glob("*.orca.log"))


def test_missing_configured_orca_is_explained(monkeypatch):
    from pipeline.slicers.base import SlicerError
    with pytest.raises(SlicerError, match="not found"):
        find_orca("/nowhere/orca-slicer")


def test_read_stats_from_sliced_file(tmp_path):
    f = tmp_path / "x.gcode.3mf"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("Metadata/slice_info.config", '<plate><metadata key="prediction" value="600"/>'
                   '<filament used_m="1.5" used_g="4.5"/></plate>')
    assert read_3mf_stats(f) == (600, 4.5, 1.5)
    assert read_3mf_stats(tmp_path / "missing.gcode.3mf") == (None, None, None)


# ---------------------------------------------------------------- profiles

@pytest.mark.parametrize("folder,machine,bed", [
    ("p2s", "Bambu Lab P2S 0.4 nozzle", 256),
    ("ender3", "Creality Ender-3 0.4 nozzle", 220),
])
def test_shipped_orca_profiles_are_complete(folder, machine, bed):
    d = ROOT / "config" / "orca" / folder
    m = json.loads((d / "machine.json").read_text())
    assert m["type"] == "machine" and m["name"] == machine and m["from"] == "system"
    assert m["printable_area"] == ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"]
    assert len(m["machine_start_gcode"]) > 100            # templates were merged in
    for f in ["process.json", "filament_PLA.json", "filament_PETG.json"]:
        p = json.loads((d / f).read_text())
        assert "inherits" not in p and "include" not in p
        assert machine in p.get("compatible_printers", [machine])
    for mat in config.settings()["materials"]:
        assert (d / f"filament_{mat}.json").exists(), f"no OrcaSlicer profile for material {mat}"


def test_every_printer_with_orca_profiles_has_them():
    for p in config.printers():
        if p["orca_profiles"]:
            assert (ROOT / p["orca_profiles"] / "machine.json").exists(), p["name"]


def test_profile_tool_merges_layers(tmp_path):
    bbl = tmp_path / "BBL"
    for kind in ("machine", "process", "filament"):
        (bbl / kind).mkdir(parents=True)
    w = lambda kind, name, d: (bbl / kind / f"{name}.json").write_text(json.dumps({"name": name, **d}))
    (tmp_path / "OrcaFilamentLibrary" / "filament").mkdir(parents=True)
    (tmp_path / "OrcaFilamentLibrary" / "filament" / "libbase.json").write_text(json.dumps({"x": "lib"}))
    w("machine", "base", {"a": "1", "b": "1"})
    w("machine", "tpl", {"start": "G28", "instantiation": "false"})
    w("machine", "M", {"inherits": "base", "include": ["tpl"], "b": "2"})
    w("process", "P", {"compatible_printers": ["M"], "wall_loops": "2"})
    w("filament", "F", {"compatible_printers": ["Other"]})
    assert flatten(bbl / "machine", "M") == {"name": "M", "a": "1", "b": "2", "start": "G28"}
    with pytest.raises(ProfileError, match="not made for M"):
        make(bbl, tmp_path / "out", "M", "P", {"PLA": "F"})
    w("filament", "F", {"compatible_printers": ["M"], "inherits": "libbase"})   # from the shared library
    files = make(bbl, tmp_path / "out", "M", "P", {"PLA": "F"})
    assert json.loads(files[2].read_text())["x"] == "lib"
    assert [f.name for f in files] == ["machine.json", "process.json", "filament_PLA.json"]


# ---------------------------------------------------------------- Wi-Fi sending

LAN = {"address": "192.168.1.60", "serial": "01P00A123456789", "access_code": "12345678"}


def test_lan_needs_all_three_details(monkeypatch):
    monkeypatch.setitem(config.settings(), "bambu_lan", {P2S: {**LAN, "serial": ""}})
    assert bambu_lan.lan_config(P2S) is None
    monkeypatch.setitem(config.settings(), "bambu_lan", {P2S: LAN})
    lan = bambu_lan.lan_config(P2S)
    assert (lan.address, lan.serial, lan.access_code, lan.use_ams) == ("192.168.1.60", "01P00A123456789",
                                                                       "12345678", False)
    assert bambu_lan.lan_config("Creality Ender 3") is None


def test_access_code_in_settings_yaml_is_refused(monkeypatch):
    leaked = list(config._find_keys({"bambu_lan": {P2S: LAN}}))
    assert leaked == [f"bambu_lan.{P2S}.access_code"]


def test_print_command_points_at_uploaded_file():
    lan = bambu_lan.BambuLan(P2S, **LAN)
    cmd = bambu_lan.print_command(lan, "hook_PLA_20261119-1800.gcode.3mf")["print"]
    assert cmd["command"] == "project_file" and cmd["param"] == "Metadata/plate_1.gcode"
    assert cmd["url"] == "ftp:///hook_PLA_20261119-1800.gcode.3mf"
    assert cmd["subtask_name"] == "hook_PLA_20261119-1800" and cmd["use_ams"] is False


def test_send_uploads_all_and_starts_first(monkeypatch, tmp_path):
    files = [tmp_path / "a.gcode.3mf", tmp_path / "b.gcode.3mf"]
    for f in files:
        f.write_bytes(b"x")
    done = []
    monkeypatch.setattr(bambu_lan, "upload", lambda lan, f: done.append(("up", f.name)))
    monkeypatch.setattr(bambu_lan, "start_print", lambda lan, name: done.append(("start", name)))
    bambu_lan.send(bambu_lan.BambuLan(P2S, **LAN), files, start=True)
    assert done == [("up", "a.gcode.3mf"), ("up", "b.gcode.3mf"), ("start", "a.gcode.3mf")]
    done.clear()
    bambu_lan.send(bambu_lan.BambuLan(P2S, **LAN), files[:1], start=False)
    assert done == [("up", "a.gcode.3mf")]


def test_unreachable_printer_gives_plain_message(tmp_path):
    f = tmp_path / "a.gcode.3mf"
    f.write_bytes(b"x")
    lan = bambu_lan.BambuLan(P2S, "127.0.0.1", "S", "12345678")
    old = bambu_lan.FTP_PORT
    bambu_lan.FTP_PORT = 9                     # nothing listens here
    try:
        with pytest.raises(bambu_lan.BambuSendError, match="Could not reach|did not answer"):
            bambu_lan.upload(lan, f, timeout=3)
    finally:
        bambu_lan.FTP_PORT = old


def test_mqtt_start_publishes_to_printer(monkeypatch):
    """start_print with a fake MQTT client: right login, topic and message."""
    import paho.mqtt.client as mqtt
    seen = {}

    class FakeInfo:
        def wait_for_publish(self, t): pass
        def is_published(self): return True

    class FakeClient:
        def __init__(self, *a, **k): self.on_connect = None
        def username_pw_set(self, u, p): seen["login"] = (u, p)
        def tls_set_context(self, ctx): seen["tls"] = True
        def tls_insecure_set(self, v): pass
        def connect(self, host, port, keepalive): seen["to"] = (host, port)
        def loop_start(self): self.on_connect(self, None, None, 0)
        def loop_stop(self): pass
        def disconnect(self): pass
        def publish(self, topic, payload, qos):
            seen["topic"], seen["payload"] = topic, json.loads(payload)
            return FakeInfo()

    monkeypatch.setattr(mqtt, "Client", FakeClient)
    bambu_lan.start_print(bambu_lan.BambuLan(P2S, **LAN), "a.gcode.3mf", timeout=2)
    assert seen["login"] == ("bblp", "12345678") and seen["to"] == ("192.168.1.60", 8883) and seen["tls"]
    assert seen["topic"] == "device/01P00A123456789/request"
    assert seen["payload"]["print"]["url"] == "ftp:///a.gcode.3mf"


# ---------------------------------------------------------------- the real OrcaSlicer (CI)

def _real_orca():
    from pipeline.slicers.orcaslicer import _candidates
    return bool(os.environ.get("ORCA_SLICER") or _candidates())


@pytest.fixture
def real_orca(monkeypatch):
    if os.environ.get("ORCA_SLICER"):
        s = config.settings()
        monkeypatch.setitem(s, "slicer", {**s["slicer"], "orca": os.environ["ORCA_SLICER"]})


def _save_artifact(f: Path):
    d = os.environ.get("ORCA_ARTIFACT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        shutil.copy(f, Path(d) / f.name)


@pytest.mark.skipif(not _real_orca(), reason="OrcaSlicer not installed")
@pytest.mark.parametrize("strength", ["Normal", "Strong"])
def test_real_orca_slices_p2s(test_library, real_orca, strength):
    hook = get_part(test_library, "test-hook")
    job = run_job(JobRequest("test-hook", {}, "PLA", strength, files={hook.files[0].rel: 2}, printer=P2S),
                  test_library)
    log = "\n".join(p.read_text(errors="ignore")[-3000:] for p in job.folder.glob("*.log"))
    assert job.ok and job.output == "bambu", (job.errors, job.warnings, log)
    z = zipfile.ZipFile(job.slice.gcode)
    assert "Metadata/plate_1.gcode" in z.namelist()
    gcode = z.read("Metadata/plate_1.gcode").decode(errors="ignore")
    assert "M1002" in gcode, "Bambu's own P2S start code should be in the file"
    assert job.print_time_s and job.print_time_s > 60 and job.filament_g and job.filament_g > 1
    _save_artifact(job.slice.gcode)
    return job


@pytest.mark.skipif(not _real_orca(), reason="OrcaSlicer not installed")
def test_real_orca_strong_uses_more_plastic(test_library, real_orca):
    g = {st: run_job(JobRequest("test-hook", {}, "PLA", st, printer=P2S), test_library).filament_g
         for st in ("Normal", "Strong")}
    assert g["Strong"] > g["Normal"] * 1.1, g


@pytest.mark.skipif(not _real_orca(), reason="OrcaSlicer not installed")
@pytest.mark.parametrize("material,temp", [("PLA", "220"), ("PETG", "255")])
def test_real_orca_slices_ender(test_library, real_orca, material, temp):
    job = run_job(JobRequest("test-hook", {}, material, "Normal", printer=ENDER), test_library)
    log = "\n".join(p.read_text(errors="ignore")[-3000:] for p in job.folder.glob("*.log"))
    assert job.ok and job.slicer_used == "orcaslicer", (job.errors, job.warnings, log)
    text = job.slice.gcode.read_text(errors="ignore")
    assert text.startswith("; Hope Arising 3D Print Shop") and "G28" in text
    assert f"M104 S{temp}" in text or f"M109 S{temp}" in text
    assert job.print_time_s > 60 and job.filament_g > 1
    _save_artifact(job.slice.gcode)


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl to make a test certificate")
def test_ftps_upload_to_fake_printer(tmp_path, monkeypatch):
    """The real upload code against a fake printer file server (implicit TLS, like port 990)."""
    from tests.fake_bambu_ftps import FakePrinterFTPS, self_signed
    store = tmp_path / "printer"
    store.mkdir()
    srv = FakePrinterFTPS(store, "12345678", *self_signed(tmp_path))
    monkeypatch.setattr(bambu_lan, "FTP_PORT", srv.port)
    f = tmp_path / "hook.gcode.3mf"
    f.write_bytes(os.urandom(300_000))
    try:
        bambu_lan.upload(bambu_lan.BambuLan(P2S, "127.0.0.1", "S", "12345678"), f, timeout=10)
        assert (store / f.name).read_bytes() == f.read_bytes()
        with pytest.raises(bambu_lan.BambuSendError, match="access code is wrong"):
            bambu_lan.upload(bambu_lan.BambuLan(P2S, "127.0.0.1", "S", "00000000"), f, timeout=10)
    finally:
        srv.close()


@pytest.mark.skipif(not _real_orca(), reason="OrcaSlicer not installed")
@pytest.mark.parametrize("printer", [P2S, ENDER])
def test_real_orca_library_part_with_many_pieces(real_orca, printer):
    """A real shop part with several different pieces on one plate."""
    from pipeline.library import load_catalog
    parts = load_catalog(ROOT / "models" / "sources.csv", ROOT / "models")
    p = next(p for p in parts if p.part_id == "travel-salt-shaker")
    job = run_job(JobRequest(p.part_id, {}, "PLA", "Normal", files={f.rel: 1 for f in p.files}, printer=printer),
                  parts)
    log = "\n".join(f.read_text(errors="ignore")[-2000:] for f in job.folder.glob("*.log"))
    assert job.ok and job.slicer_used == "orcaslicer", (job.errors, job.warnings, log)
    assert job.filament_g > 5 and job.print_time_s > 600
    for pl in job.plates:
        _save_artifact(pl.result.gcode)
