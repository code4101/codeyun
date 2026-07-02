from __future__ import annotations

from fastapi import FastAPI

from backend.api.runtime_management import router as runtime_management_router

from .codex import register as register_cluster_codex_standard_feature
from .control import register as register_cluster_control_standard_feature
from .device_agent import register as register_cluster_device_agent_standard_feature
from .devices import register as register_cluster_devices_standard_feature
from .entries import register as register_cluster_entries_standard_feature
from .rime_context import register as register_cluster_rime_context_standard_feature
from .services import register as register_cluster_services_standard_feature
from .tasks import register as register_cluster_tasks_standard_feature


def register(app: FastAPI) -> None:
    register_cluster_devices_standard_feature(app)
    register_cluster_entries_standard_feature(app)
    register_cluster_tasks_standard_feature(app)
    register_cluster_services_standard_feature(app)
    app.include_router(runtime_management_router, prefix="/api/runtime", tags=["runtime"])
    register_cluster_device_agent_standard_feature(app)
    register_cluster_codex_standard_feature(app)
    register_cluster_control_standard_feature(app)
    register_cluster_rime_context_standard_feature(app)
