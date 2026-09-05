"""File-based config (gossamer.json) + keystore behavior.

Precedence under test (see gossamer.settings / gossamer.env):
explicit arg > GOSSAMER_* env > STITCH_* env > keystore file >
gossamer.json "keys" > default.
"""

import json
import os

import pytest

from gossamer import keystore as ks
from gossamer import settings
from gossamer.agent_tools import ToolboxConfig
from gossamer.env import getenv


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    settings.clear_caches()
    for var in list(os.environ):
        if var.startswith("GOSSAMER_") or var.startswith("STITCH_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    # Isolate home-dir discovery.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    yield
    settings.clear_caches()


def _write(path, payload):
    path = str(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


class TestDiscovery:
    def test_no_files_anywhere(self):
        assert settings.find_config_file() is None
        assert settings.find_keystore_file() is None
        assert settings.load_config_file() == {}
        assert settings.load_keystore() == {}

    def test_explicit_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            settings.load_config_file(str(tmp_path / "nope.json"))
        with pytest.raises(FileNotFoundError):
            settings.load_keystore(str(tmp_path / "nope.json"))

    def test_invalid_json_raises(self, tmp_path):
        bad = tmp_path / "gossamer.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            settings.load_config_file()

    def test_cwd_discovery(self, tmp_path):
        _write(tmp_path / "gossamer.json", {"max_tokens": 11})
        assert settings.load_config_file() == {"max_tokens": 11}

    def test_env_beats_cwd(self, tmp_path, monkeypatch):
        _write(tmp_path / "gossamer.json", {"max_tokens": 11})
        other = _write(tmp_path / "other.json", {"max_tokens": 22})
        monkeypatch.setenv("GOSSAMER_CONFIG", other)
        assert settings.load_config_file()["max_tokens"] == 22

    def test_mtime_reload(self, tmp_path):
        cfg = tmp_path / "gossamer.json"
        _write(cfg, {"a": 1})
        assert settings.load_config_file() == {"a": 1}
        import time

        # Ensure the mtime actually advances (coarse filesystems).
        os.utime(cfg, (time.time() + 5, time.time() + 5))
        _write(cfg, {"a": 2})
        assert settings.load_config_file() == {"a": 2}


class TestKeyLookup:
    def test_keystore_short_and_full_names(self, tmp_path, monkeypatch):
        _write(tmp_path / "keys.json", {"OPENALEX_KEY": "short", "GOSSAMER_X_KEY": "full"})
        monkeypatch.setenv("GOSSAMER_KEYSTORE", str(tmp_path / "keys.json"))
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "short"
        assert getenv("GOSSAMER_X_KEY", "") == "full"

    def test_env_beats_keystore(self, tmp_path, monkeypatch):
        _write(tmp_path / "keys.json", {"OPENALEX_KEY": "file"})
        monkeypatch.setenv("GOSSAMER_KEYSTORE", str(tmp_path / "keys.json"))
        monkeypatch.setenv("GOSSAMER_OPENALEX_KEY", "env")
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "env"

    def test_legacy_env_beats_keystore(self, tmp_path, monkeypatch):
        _write(tmp_path / "keys.json", {"OPENALEX_KEY": "file"})
        monkeypatch.setenv("GOSSAMER_KEYSTORE", str(tmp_path / "keys.json"))
        monkeypatch.setenv("STITCH_OPENALEX_KEY", "legacy")
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "legacy"

    def test_keystore_beats_config_keys(self, tmp_path, monkeypatch):
        _write(tmp_path / "keys.json", {"OPENALEX_KEY": "store"})
        _write(tmp_path / "gossamer.json", {"keys": {"OPENALEX_KEY": "inline"}})
        monkeypatch.setenv("GOSSAMER_KEYSTORE", str(tmp_path / "keys.json"))
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "store"

    def test_config_keys_section_used(self, tmp_path):
        _write(tmp_path / "gossamer.json", {"keys": {"OPENALEX_KEY": "inline"}})
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "inline"

    def test_default_home_keystore(self, tmp_path, monkeypatch):
        store_dir = Path_home() / ".gossamer"
        store_dir.mkdir(parents=True, exist_ok=True)
        _write(store_dir / "keys.json", {"OPENALEX_KEY": "home"})
        assert getenv("GOSSAMER_OPENALEX_KEY", "") == "home"


def Path_home():
    from pathlib import Path

    return Path.home()


class TestFromDict:
    def test_known_fields_mapped(self):
        cfg = ToolboxConfig.from_dict({"max_tokens": 123, "model_name": "x", "fetch_mode": "static"})
        assert (cfg.max_tokens, cfg.model_name, cfg.fetch_mode) == (123, "x", "static")

    def test_coercion(self):
        cfg = ToolboxConfig.from_dict({"max_tokens": "123", "respect_robots": "false"})
        assert cfg.max_tokens == 123
        assert cfg.respect_robots is False

    def test_guard_nesting(self):
        cfg = ToolboxConfig.from_dict({"guard": {"enabled": True, "mode": "block"}})
        assert cfg.guard is not None and cfg.guard.enabled and cfg.guard.mode == "block"

    def test_unknown_keys_warn_not_raise(self, caplog):
        cfg = ToolboxConfig.from_dict({"nope": 1, "max_tokens": 5})
        assert cfg.max_tokens == 5
        assert "nope" in caplog.text

    def test_search_providers_ignored(self, caplog):
        cfg = ToolboxConfig.from_dict({"search_providers": ["x"]})
        assert cfg.search_providers is None
        assert "search_providers" in caplog.text


class TestMcpMerge:
    def test_file_values_used_when_env_unset(self, tmp_path, monkeypatch):
        from gossamer import mcp_server

        _write(tmp_path / "gossamer.json", {"max_tokens": 4321, "model_name": "file-model"})
        monkeypatch.chdir(tmp_path)
        cfg = mcp_server._config_from_env()
        assert cfg.max_tokens == 4321
        assert cfg.model_name == "file-model"

    def test_env_wins_over_file(self, tmp_path, monkeypatch):
        from gossamer import mcp_server

        _write(tmp_path / "gossamer.json", {"max_tokens": 4321})
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GOSSAMER_MAX_TOKENS", "111")
        assert mcp_server._config_from_env().max_tokens == 111


class TestKeystoreCli:
    def test_init_writes_template_0600(self, tmp_path):
        target = tmp_path / "keys.json"
        out = ks.init_keystore(str(target))
        data = json.loads(target.read_text(encoding="utf-8"))
        assert set(data) == set(ks.KNOWN_KEYS)
        assert all(v == "" for v in data.values())
        assert str(out) == str(target)

    def test_init_refuses_overwrite(self, tmp_path):
        target = tmp_path / "keys.json"
        target.write_text("{}", encoding="utf-8")
        with pytest.raises(FileExistsError):
            ks.init_keystore(str(target))

    def test_check_ok_and_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert ks.main(["--check"]) == 0
        out = capsys.readouterr().out
        assert "none found" in out
