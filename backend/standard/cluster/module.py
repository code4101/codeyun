from __future__ import annotations

from fastapi import FastAPI

from .codex import register as register_cluster_codex_standard_feature
from .control import register as register_cluster_control_standard_feature
from .devices import register as register_cluster_devices_standard_feature
from .entries import register as register_cluster_entries_standard_feature
from .rime_context import register as register_cluster_rime_context_standard_feature
from .tasks import register as register_cluster_tasks_standard_feature


def register(app: FastAPI) -> None:
    register_cluster_devices_standard_feature(app)
    register_cluster_entries_standard_feature(app)
    register_cluster_tasks_standard_feature(app)
    register_cluster_codex_standard_feature(app)
    register_cluster_control_standard_feature(app)
    register_cluster_rime_context_standard_feature(app)
