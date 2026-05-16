from pathlib import Path

from backend.core import settings as settings_module


def test_default_data_dir_stays_outside_source_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEYUN_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_DATA_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("CODEYUN_DATA_INSTANCE_NAME", "mf")
    settings_module.get_settings.cache_clear()

    try:
        settings = settings_module.get_settings()
        assert settings.data_dir == (tmp_path / "workspace" / "codepc_mf").resolve()
        assert settings.data_workspace_dir == (tmp_path / "workspace").resolve()
        assert not settings.data_dir.is_relative_to(settings_module.ROOT_DIR)
    finally:
        settings_module.get_settings.cache_clear()


def test_default_data_dir_does_not_double_codepc_prefix(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEYUN_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEYUN_DATA_INSTANCE_NAME", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_DATA_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings_module.socket, "gethostname", lambda: "codepc_mf")
    settings_module.get_settings.cache_clear()

    try:
        settings = settings_module.get_settings()
        assert settings.data_dir == (tmp_path / "workspace" / "codepc_mf").resolve()
    finally:
        settings_module.get_settings.cache_clear()


def test_explicit_data_dir_is_still_respected(monkeypatch, tmp_path):
    custom_dir = tmp_path / "custom-data"
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")
    monkeypatch.setenv("CODEYUN_DATA_DIR", str(custom_dir))
    settings_module.get_settings.cache_clear()

    try:
        settings = settings_module.get_settings()
        assert settings.data_dir == custom_dir.resolve()
    finally:
        settings_module.get_settings.cache_clear()
