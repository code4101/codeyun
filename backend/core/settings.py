from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in production
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
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


def _resolve_path(value: str | None, default: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return default

    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
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

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


def load_settings() -> Settings:
    _load_project_dotenv()
    environment = _normalize_environment(os.getenv("CODEYUN_ENV") or os.getenv("ENVIRONMENT"))
    data_dir = _resolve_path(os.getenv("CODEYUN_DATA_DIR"), DEFAULT_DATA_DIR)
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
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
