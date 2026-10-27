"""Settings safety: friendly errors for typos, and API keys kept out of the shared file."""
import shutil

import pytest

from pipeline import config


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    shutil.copy(config.CONFIG_DIR / "settings.yaml", tmp_path / "settings.yaml")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config.settings.cache_clear()
    yield tmp_path
    config.settings.cache_clear()


def test_shipped_settings_have_no_key(cfg_dir):
    assert config.check() == 0


def test_key_in_shared_settings_is_refused(cfg_dir, capsys):
    f = cfg_dir / "settings.yaml"
    f.write_text(f.read_text().replace("api_key_env: GEMINI_API_KEY",
                                       'api_key_env: GEMINI_API_KEY\n      api_key: "AIzaSyRealLookingKey"'))
    assert config.check() == 1
    assert "uploaded to GitHub" in capsys.readouterr().out


def test_key_in_local_file_is_fine(cfg_dir):
    (cfg_dir / "settings.local.yaml").write_text('agent:\n  providers:\n    gemini:\n      api_key: "AIzaX"\n')
    assert config.settings()["agent"]["providers"]["gemini"]["api_key"] == "AIzaX"
    assert config.settings()["agent"]["providers"]["gemini"]["model"]  # rest of block kept


def test_typo_gives_line_number(cfg_dir, capsys):
    (cfg_dir / "settings.local.yaml").write_text('agent:\n  providers:\n\tgemini:\n      api_key: "x"\n')
    assert config.check() == 1
    out = capsys.readouterr().out
    assert "settings.local.yaml" in out and "line 3" in out
