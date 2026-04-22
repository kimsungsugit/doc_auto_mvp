from __future__ import annotations

import json
from pathlib import Path

from app.schema import _get_config, reload_config


class TestSettingsEnvOverride:
    """settings.py should respect environment variable overrides."""

    def test_data_dir_uses_env_override(self, monkeypatch, tmp_path):
        custom_dir = tmp_path / "custom_storage"
        custom_dir.mkdir()
        monkeypatch.setenv("DATA_DIR", str(custom_dir))

        # Re-import to pick up env var
        import importlib
        import app.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.DATA_DIR == custom_dir

        # Clean up: reload again without the env var
        monkeypatch.delenv("DATA_DIR", raising=False)
        importlib.reload(settings_mod)

    def test_defaults_work_without_env(self, monkeypatch):
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("APP_BASE_DIR", raising=False)
        monkeypatch.delenv("TEMPLATES_DIR", raising=False)

        import importlib
        import app.settings as settings_mod
        importlib.reload(settings_mod)

        assert settings_mod.DATA_DIR.name == "storage"
        assert settings_mod.TEMPLATES_DIR.name == "templates"


class TestConfigReload:
    """reload_config() should force config reload from disk."""

    def test_reload_picks_up_changes(self, tmp_path, monkeypatch):
        config_path = tmp_path / "document_types.json"
        config_path.write_text('{"테스트유형": {}}', encoding="utf-8")

        import app.schema as schema_mod
        monkeypatch.setattr(schema_mod, "CONFIG_PATH", config_path)
        reload_config()

        config = _get_config()
        assert "테스트유형" in config

        # Modify config
        config_path.write_text('{"새유형": {}, "테스트유형": {}}', encoding="utf-8")
        reload_config()

        config = _get_config()
        assert "새유형" in config

    def test_mtime_based_auto_refresh(self, tmp_path, monkeypatch):
        import time
        import app.schema as schema_mod

        config_path = tmp_path / "document_types.json"
        config_path.write_text('{"유형A": {}}', encoding="utf-8")
        monkeypatch.setattr(schema_mod, "CONFIG_PATH", config_path)
        reload_config()

        config = _get_config()
        assert "유형A" in config

        # Write new content with a later mtime
        time.sleep(0.1)
        config_path.write_text('{"유형B": {}}', encoding="utf-8")

        config = _get_config()
        assert "유형B" in config
