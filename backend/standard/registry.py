from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from .admin import register as register_admin_standard_features
from .attendance import register as register_attendance_standard_features
from .cluster import register as register_cluster_standard_features
from .fanxiu import register as register_fanxiu_standard_features
from .mystia import register as register_mystia_standard_features
from .notes import register as register_notes_standard_features
from .tools import register as register_tools_standard_features


StandardModuleRegistrar = Callable[[FastAPI], None]

STANDARD_MODULE_REGISTRARS: tuple[StandardModuleRegistrar, ...] = (
    register_admin_standard_features,
    register_attendance_standard_features,
    register_cluster_standard_features,
    register_fanxiu_standard_features,
    register_mystia_standard_features,
    register_notes_standard_features,
    register_tools_standard_features,
)


def register_standard_modules(app: FastAPI) -> None:
    for register in STANDARD_MODULE_REGISTRARS:
        register(app)
