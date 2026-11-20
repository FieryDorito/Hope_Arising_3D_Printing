"""TEMPORARY: which settings make two pieces slice on the P2S with real OrcaSlicer?"""
import os, sys, tempfile, json, shutil
sys.path.insert(0, os.getcwd())
from pathlib import Path
from pipeline import config, run_job as rj
from pipeline.library import load_catalog
from pipeline.run_job import JobRequest, run_job
s = config.settings()
s["slicer"]["orca"] = os.environ["ORCA_SLICER"]
s["jobs_dir"] = tempfile.mkdtemp()
lib = Path(tempfile.mkdtemp()); (lib / "m" / "hook").mkdir(parents=True)
shutil.copy("tests/fixtures/test_hook.stl", lib / "m" / "hook" / "model.stl")
(lib / "c.csv").write_text("part_name,category,original_author,license,commercial_ok,link\nHook,Household,A,PD,YES,https://x\n".replace("commercial_ok","commercial_use_ok"))
parts = load_catalog(lib / "c.csv", lib / "m")
pid = parts[0].part_id
base = dict(s["strength"]["Normal"]["orca"])
for name, over, gap in [("as is", {}, 8), ("no support", {"enable_support": 0}, 8),
                        ("normal support", {"support_type": "normal(auto)"}, 8),
                        ("gap 25", {}, 25), ("no support gap 25", {"enable_support": 0}, 25),
                        ("no brim", {"brim_type": "no_brim"}, 8)]:
    s["strength"]["Normal"]["orca"] = {**base, **over}
    rj.PLATE_GAP_MM = gap
    job = run_job(JobRequest(pid, {}, "PLA", "Normal", files={parts[0].files[0].rel: 2}, printer="Bambu Lab P2S"), parts)
    print(f"{name:22s} -> output={job.output} warnings={[w[:60] for w in job.warnings]}", flush=True)
