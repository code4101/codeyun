import pytest

from backend.core import settings as settings_module


def clear_settings_cache():
    settings_module.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_development_defaults(monkeypatch):
    monkeypatch.delenv("CODEYUN_ENV", raising=False)
    monkeypatch.delenv("CODEYUN_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CODEYUN_ENABLE_DOCS", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.environment == "development"
    assert settings.docs_enabled is True
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.allow_all_cors is False


def test_production_defaults(monkeypatch):
    monkeypatch.setenv("CODEYUN_ENV", "production")
    monkeypatch.delenv("CODEYUN_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CODEYUN_ENABLE_DOCS", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.environment == "production"
    assert settings.docs_enabled is False
    assert settings.cors_origins == ()


def test_explicit_cors_and_docs_override(monkeypatch):
    monkeypatch.setenv("CODEYUN_ENV", "production")
    monkeypatch.setenv("CODEYUN_CORS_ORIGINS", "https://code4101.com, https://admin.code4101.com")
    monkeypatch.setenv("CODEYUN_ENABLE_DOCS", "1")
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.docs_enabled is True
    assert settings.cors_origins == (
        "https://code4101.com",
        "https://admin.code4101.com",
    )
