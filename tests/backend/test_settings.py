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
    assert settings.cors_origin_regex == r"^https?://[^/]+:(5173|4173)$"
    assert settings.allow_all_cors is False
    assert settings.ocr_device == "gpu"


def test_ocr_device_global_default_and_override(monkeypatch):
    monkeypatch.delenv("CODEYUN_OCR_DEVICE", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    assert settings_module.load_settings().ocr_device == "gpu"

    monkeypatch.setenv("CODEYUN_OCR_DEVICE", "cpu")

    assert settings_module.load_settings().ocr_device == "cpu"


def test_production_defaults(monkeypatch):
    monkeypatch.setenv("CODEYUN_ENV", "production")
    monkeypatch.delenv("CODEYUN_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CODEYUN_ENABLE_DOCS", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.environment == "production"
    assert settings.docs_enabled is False
    assert settings.cors_origins == ()
    assert settings.cors_origin_regex == ""


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
    assert settings.cors_origin_regex == ""


def test_ollama_defaults(monkeypatch):
    monkeypatch.delenv("CODEYUN_AI_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("CODEYUN_OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("CODEYUN_OLLAMA_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("CODEYUN_OLLAMA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.ai_default_provider == "deepseek"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_default_model == "qwen3-vl:4b"
    assert settings.ollama_timeout_seconds == 120.0


def test_ollama_overrides(monkeypatch):
    monkeypatch.setenv("CODEYUN_AI_DEFAULT_PROVIDER", "deepseek")
    monkeypatch.setenv("CODEYUN_OLLAMA_BASE_URL", "http://localhost:22334/")
    monkeypatch.setenv("CODEYUN_OLLAMA_DEFAULT_MODEL", "llama3.2:latest")
    monkeypatch.setenv("CODEYUN_OLLAMA_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("CODEYUN_LOAD_DOTENV", "0")

    settings = settings_module.load_settings()

    assert settings.ai_default_provider == "deepseek"
    assert settings.ollama_base_url == "http://localhost:22334"
    assert settings.ollama_default_model == "llama3.2:latest"
    assert settings.ollama_timeout_seconds == 45.0
