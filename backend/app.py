from contextlib import asynccontextmanager
import logging
import os

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.admin_feature_access import router as admin_feature_access_router
from backend.api.access import router as access_router
from backend.api.auth import router as auth_router
from backend.api.filesystem import router as filesystem_router
from backend.api.proxy_traffic_audit import router as proxy_traffic_audit_router
from backend.api.services import control_router as service_control_router
from backend.api.services import router as services_router
from backend.api.task_manager import (
    start_task_manager_services,
    stop_task_manager_services,
)
from backend.api.upload import router as upload_router
from backend.core.bootstrap import ensure_bootstrap_admin
from backend.core.auth import verify_api_token
from backend.core.background_task_runner import init_background_task_runner, shutdown_background_task_runner
from backend.core.fanxiu_capture_runtime import fanxiu_capture_runtime_service
from backend.core.fanxiu_packet_insight_worker import fanxiu_packet_insight_worker
from backend.core.service_tokens import ensure_legacy_service_tokens
from backend.core.system_metrics import shutdown_system_metrics_monitor, start_system_metrics_monitor
from backend.core.runtime_management import ensure_data_annotation_behavior_tree_service_on_startup
from backend.plugins import register_plugin_modules
from backend.core.settings import get_settings
from backend.core.storage import (
    ATTACHMENTS_URL_PREFIX,
    LEGACY_UPLOADS_URL_PREFIX,
    get_attachments_dir,
    migrate_legacy_attachments,
    migrate_legacy_source_data_dir,
)
from backend.core.wechat_ilink import shutdown_codex_bridges, start_enabled_codex_bridges
from backend.db import init_db
from backend.db import engine
from backend.standard import register_standard_modules
from sqlmodel import Session

settings = get_settings()
logger = logging.getLogger(__name__)


def _env_enabled(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _fanxiu_capture_runtime_service_enabled() -> bool:
    configured = _env_enabled(os.getenv("FX_CAPTURE_RUNTIME_SERVICE_ENABLED"))
    if configured is not None:
        return configured
    services_text = os.getenv("FX_RUNTIME_SERVICES")
    if services_text is None:
        return True
    services = {item.strip().lower() for item in services_text.split(",") if item.strip()}
    return bool(services & {"*", "all", "fanxiu", "fanxiu-capture-runtime", "fanxiu_capture_runtime", "capture_runtime", "capture", "凡修抓包"})


def _fanxiu_capture_watchdog_interval_seconds() -> float:
    try:
        return float(os.getenv("FX_CAPTURE_RUNTIME_WATCHDOG_INTERVAL_SECONDS") or 60)
    except (TypeError, ValueError):
        return 60.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_bootstrap_admin()
    with Session(engine) as session:
        ensure_legacy_service_tokens(session)
    await start_task_manager_services()
    if not settings.is_test:
        start_system_metrics_monitor()
    init_background_task_runner()
    if not settings.is_test:
        fanxiu_packet_insight_worker.start()
    if not settings.is_test and _fanxiu_capture_runtime_service_enabled():
        fanxiu_capture_runtime_service.start_watchdog(
            interval_seconds=_fanxiu_capture_watchdog_interval_seconds()
        )
    if not settings.is_test:
        try:
            ensure_data_annotation_behavior_tree_service_on_startup()
        except Exception as exc:
            # Startup must not fail just because the local game runtime is unavailable.
            logger.warning("Skipping Fanxiu behavior backend startup: %s", exc)
            pass
    if not settings.is_test:
        start_enabled_codex_bridges()
    yield
    if not settings.is_test:
        fanxiu_capture_runtime_service.stop_watchdog()
        fanxiu_packet_insight_worker.stop()
        shutdown_system_metrics_monitor()
    shutdown_codex_bridges()
    shutdown_background_task_runner()
    await stop_task_manager_services()


cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

if settings.allow_all_cors:
    cors_kwargs["allow_origin_regex"] = ".*"
else:
    cors_kwargs["allow_origins"] = list(settings.cors_origins)
    if settings.cors_origin_regex:
        cors_kwargs["allow_origin_regex"] = settings.cors_origin_regex

app = FastAPI(
    title="CodeYun Backend",
    description="Local backend for CodeYun tools",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    redoc_url=None,
)

app.add_middleware(CORSMiddleware, **cors_kwargs)

# Include routers with global authentication
app.include_router(auth_router, prefix="/api/auth", tags=["auth"]) # Public auth
app.include_router(access_router, prefix="/api/access", tags=["access"])
app.include_router(services_router, prefix="/api/services", tags=["services"])
app.include_router(service_control_router, prefix="/api/service-control", tags=["service-control"])
app.include_router(filesystem_router, prefix="/api/fs", tags=["filesystem"], dependencies=[Depends(verify_api_token)])
app.include_router(proxy_traffic_audit_router, prefix="/api/proxy-traffic-audit", tags=["proxy-traffic-audit"])
app.include_router(upload_router, prefix="/api/upload", tags=["upload"])
app.include_router(
    admin_feature_access_router,
    prefix="/api/admin/feature-access",
    tags=["admin-feature-access"],
)
register_standard_modules(app)
register_plugin_modules(app)

# Mount static files
if not settings.is_test:
    migrate_legacy_source_data_dir()
migrate_legacy_attachments()
attachments_dir = os.fspath(get_attachments_dir())
app.mount(ATTACHMENTS_URL_PREFIX, StaticFiles(directory=attachments_dir), name="attachments")
app.mount(LEGACY_UPLOADS_URL_PREFIX, StaticFiles(directory=attachments_dir), name="uploads-legacy")
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def read_root():
    return {"message": "CodeYun Backend is running"}


@app.get("/api/health")
def read_health():
    return {"status": "ok", "service": "codeyun-backend"}


if __name__ == "__main__":
    print(
        f"Starting backend in {settings.environment} mode on "
        f"{settings.backend_host}:{settings.backend_port}"
    )
    uvicorn.run(
        "backend.app:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=settings.is_development,
    )
