import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import config  # noqa: E402
from pipeline.library import load_catalog  # noqa: E402

FIX = ROOT / "tests" / "fixtures"

TEST_CATALOG = """part_name,category,original_author,license,commercial_use_ok,link
Test Hook,Houshold,Test Author,Public Domain,YES,https://example.com/hook
Test Spacer,Utility,Test Author,Public Domain,YES,https://example.com/spacer
Locked Part,Plumbing,Someone,CC BY-NC,NO,https://example.com/locked
No File Yet,Agricultural,Someone,Public Domain,YES,https://example.com/nofile
"""


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    """Send job folders to a temp dir so tests never touch the real jobs/ folder."""
    s = config.settings()
    monkeypatch.setitem(s, "jobs_dir", str(tmp_path / "jobs"))
    yield


@pytest.fixture
def test_library(tmp_path):
    lib = tmp_path / "library"
    models = lib / "models"
    (models / "Household" / "test-hook").mkdir(parents=True)
    shutil.copy(FIX / "test_hook.stl", models / "Household" / "test-hook" / "model.stl")
    (models / "test-spacer").mkdir()
    shutil.copy(FIX / "test_spacer.scad", models / "test-spacer" / "model.scad")
    shutil.copy(FIX / "test_spacer.yaml", models / "test-spacer" / "part.yaml")
    (models / "locked-part").mkdir()
    shutil.copy(FIX / "test_hook.stl", models / "locked-part" / "model.stl")
    csv_path = lib / "catalog.csv"
    csv_path.write_text(TEST_CATALOG, encoding="utf-8")
    return load_catalog(csv_path, models)
