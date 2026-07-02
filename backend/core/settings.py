from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in production
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
LEGACY_SOURCE_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_DATA_WORKSPACE_NAME = "m2603codeyun"
DEFAULT_DEV_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
DEFAULT_DEV_CORS_ORIGIN_REGEX = r"^https?://[^/]+:(5173|4173)$"


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _normalize_environment(value: str | None) -> str:
    normalized = (value or "development").strip().lower()
    aliases = {
        "dev": "development",
        "local": "development",
        "prod": "production",
        "testing": "test",
    }
    return aliases.get(normalized, normalized)


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _sanitize_path_segment(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    return normalized or fallback


def _default_data_workspace_dir() -> Path:
    explicit = os.getenv("CODEYUN_DATA_WORKSPACE_DIR")
    if explicit and explicit.strip():
        path = Path(explicit.strip()).expanduser()
        if path.is_absolute():
            return path.resolve(strict=False)
        return (ROOT_DIR / path).resolve(strict=False)

    if ROOT_DIR.parent.name.lower() == "slns":
        base_dir = ROOT_DIR.parent.parent
    else:
        base_dir = ROOT_DIR.parent
    return (base_dir / "data" / DEFAULT_DATA_WORKSPACE_NAME).resolve(strict=False)


def _default_data_instance_name() -> str:
    explicit = os.getenv("CODEYUN_DATA_INSTANCE_NAME")
    if explicit and explicit.strip():
        raw_name = explicit.strip()
        if raw_name.lower().startswith("codepc_"):
            return _sanitize_path_segment(raw_name, "codepc_local")
        return f"codepc_{_sanitize_path_segment(raw_name, 'local')}"

    hostname = socket.gethostname() or "local"
    hostname_name = _sanitize_path_segment(hostname.lower(), "local")
    if hostname_name.lower().startswith("codepc_"):
        return hostname_name
    return f"codepc_{hostname_name}"


def default_data_dir() -> Path:
    return (_default_data_workspace_dir() / _default_data_instance_name()).resolve(strict=False)


DEFAULT_DATA_DIR = default_data_dir()


def _resolve_path(value: str | None, default: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return default.resolve(strict=False)

    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    return (ROOT_DIR / path).resolve()


def _load_project_dotenv() -> None:
    if load_dotenv is None or not _env_flag("CODEYUN_LOAD_DOTENV", True):
        return

    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    environment: str
    debug: bool
    docs_enabled: bool
    cors_origins: tuple[str, ...]
    cors_origin_regex: str
    allow_all_cors: bool
    backend_host: str
    backend_port: int
    database_url: str
    secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    device_token: str
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    bootstrap_admin_force_reset_password: bool
    ai_default_provider: str
    ollama_base_url: str
    ollama_default_model: str
    ollama_timeout_seconds: float
    deepseek_base_url: str
    deepseek_api_key: str
    deepseek_default_model: str
    deepseek_timeout_seconds: float
    deepseek_models: tuple[str, ...]
    ocr_device: str
    ocr_lang: str
    ocr_use_doc_orientation_classify: bool
    ocr_use_doc_unwarping: bool
    ocr_use_textline_orientation: bool
    ocr_idle_timeout_seconds: int
    ocr_max_instances: int
    ocr_acquire_timeout_seconds: float
    service_request_max_image_bytes: int
    public_base_url: str

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def data_workspace_dir(self) -> Path:
        if self.data_dir.name.lower().startswith("codepc_"):
            return self.data_dir.parent
        return self.data_dir

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


def load_settings() -> Settings:
    _load_project_dotenv()
    environment = _normalize_environment(os.getenv("CODEYUN_ENV") or os.getenv("ENVIRONMENT"))
    data_dir = _resolve_path(os.getenv("CODEYUN_DATA_DIR"), default_data_dir())
    default_db_file = data_dir / "codeyun.db"

    debug = _env_flag("CODEYUN_DEBUG", environment == "development")
    docs_enabled = _env_flag("CODEYUN_ENABLE_DOCS", environment != "production")

    cors_value = os.getenv("CODEYUN_CORS_ORIGINS")
    cors_origins = _split_csv(cors_value)
    cors_origin_regex = ""
    if not cors_origins and environment in {"development", "test"}:
        cors_origins = DEFAULT_DEV_CORS_ORIGINS
        cors_origin_regex = DEFAULT_DEV_CORS_ORIGIN_REGEX

    allow_all_cors = cors_origins == ("*",)

    database_url = (
        os.getenv("CODEYUN_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or f"sqlite:///{default_db_file}"
    )

    secret_key = (
        os.getenv("CODEYUN_SECRET_KEY")
        or os.getenv("SECRET_KEY")
        or "codeyun-insecure-secret-key-change-me"
    ).strip()

    jwt_algorithm = (os.getenv("CODEYUN_JWT_ALGORITHM") or "HS256").strip() or "HS256"

    try:
        access_token_expire_minutes = int(
            os.getenv("CODEYUN_ACCESS_TOKEN_EXPIRE_MINUTES") or (30 * 24 * 60)
        )
    except ValueError:
        access_token_expire_minutes = 30 * 24 * 60

    try:
        backend_port = int(os.getenv("CODEYUN_BACKEND_PORT") or 8000)
    except ValueError:
        backend_port = 8000

    try:
        ollama_timeout_seconds = float(os.getenv("CODEYUN_OLLAMA_TIMEOUT_SECONDS") or 120)
    except ValueError:
        ollama_timeout_seconds = 120.0

    try:
        deepseek_timeout_seconds = float(os.getenv("CODEYUN_DEEPSEEK_TIMEOUT_SECONDS") or 120)
    except ValueError:
        deepseek_timeout_seconds = 120.0

    ai_default_provider = (os.getenv("CODEYUN_AI_DEFAULT_PROVIDER") or "deepseek").strip().lower() or "deepseek"
    ollama_base_url = (os.getenv("CODEYUN_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip()
    ollama_default_model = (os.getenv("CODEYUN_OLLAMA_DEFAULT_MODEL") or "qwen3-vl:4b").strip()
    deepseek_base_url = (os.getenv("CODEYUN_DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
    deepseek_api_key = (os.getenv("CODEYUN_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    deepseek_default_model = (os.getenv("CODEYUN_DEEPSEEK_DEFAULT_MODEL") or "deepseek-v4-flash").strip()
    deepseek_models = _split_csv(os.getenv("CODEYUN_DEEPSEEK_MODELS")) or (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-chat",
        "deepseek-reasoner",
    )
    ocr_device = (os.getenv("CODEYUN_OCR_DEVICE") or "gpu").strip().lower() or "gpu"
    ocr_lang = (os.getenv("CODEYUN_OCR_LANG") or "ch").strip() or "ch"
    ocr_use_doc_orientation_classify = _env_flag("CODEYUN_OCR_USE_DOC_ORIENTATION_CLASSIFY", False)
    ocr_use_doc_unwarping = _env_flag("CODEYUN_OCR_USE_DOC_UNWARPING", False)
    ocr_use_textline_orientation = _env_flag("CODEYUN_OCR_USE_TEXTLINE_ORIENTATION", False)
    try:
        ocr_idle_timeout_seconds = int(os.getenv("CODEYUN_OCR_IDLE_TIMEOUT_SECONDS") or 120)
    except ValueError:
        ocr_idle_timeout_seconds = 120
    try:
        ocr_max_instances = int(os.getenv("CODEYUN_OCR_MAX_INSTANCES") or 1)
    except ValueError:
        ocr_max_instances = 1
    try:
        ocr_acquire_timeout_seconds = float(os.getenv("CODEYUN_OCR_ACQUIRE_TIMEOUT_SECONDS") or 30)
    except ValueError:
        ocr_acquire_timeout_seconds = 30.0
    try:
        service_request_max_image_bytes = int(os.getenv("CODEYUN_SERVICE_REQUEST_MAX_IMAGE_BYTES") or (20 * 1024 * 1024))
    except ValueError:
        service_request_max_image_bytes = 20 * 1024 * 1024

    return Settings(
        data_dir=data_dir,
        environment=environment,
        debug=debug,
        docs_enabled=docs_enabled,
        cors_origins=cors_origins,
        cors_origin_regex=cors_origin_regex,
        allow_all_cors=allow_all_cors,
        backend_host=(os.getenv("CODEYUN_BACKEND_HOST") or "0.0.0.0").strip() or "0.0.0.0",
        backend_port=backend_port,
        database_url=database_url,
        secret_key=secret_key,
        jwt_algorithm=jwt_algorithm,
        access_token_expire_minutes=access_token_expire_minutes,
        device_token=(os.getenv("CODEYUN_DEVICE_TOKEN") or "").strip(),
        bootstrap_admin_username=(os.getenv("CODEYUN_BOOTSTRAP_ADMIN_USERNAME") or "").strip(),
        bootstrap_admin_password=os.getenv("CODEYUN_BOOTSTRAP_ADMIN_PASSWORD") or "",
        bootstrap_admin_force_reset_password=_env_flag(
            "CODEYUN_BOOTSTRAP_ADMIN_FORCE_RESET_PASSWORD",
            False,
        ),
        ai_default_provider=ai_default_provider,
        ollama_base_url=ollama_base_url.rstrip("/"),
        ollama_default_model=ollama_default_model or "qwen3-vl:4b",
        ollama_timeout_seconds=max(1.0, ollama_timeout_seconds),
        deepseek_base_url=deepseek_base_url.rstrip("/"),
        deepseek_api_key=deepseek_api_key,
        deepseek_default_model=deepseek_default_model or "deepseek-v4-flash",
        deepseek_timeout_seconds=max(1.0, deepseek_timeout_seconds),
        deepseek_models=deepseek_models,
        ocr_device=ocr_device,
        ocr_lang=ocr_lang,
        ocr_use_doc_orientation_classify=ocr_use_doc_orientation_classify,
        ocr_use_doc_unwarping=ocr_use_doc_unwarping,
        ocr_use_textline_orientation=ocr_use_textline_orientation,
        ocr_idle_timeout_seconds=max(30, ocr_idle_timeout_seconds),
        ocr_max_instances=max(1, ocr_max_instances),
        ocr_acquire_timeout_seconds=max(0.0, ocr_acquire_timeout_seconds),
        service_request_max_image_bytes=max(1024 * 1024, service_request_max_image_bytes),
        public_base_url=(os.getenv("CODEYUN_PUBLIC_BASE_URL") or "").strip().rstrip("/"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
