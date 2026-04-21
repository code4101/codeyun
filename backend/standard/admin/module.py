from __future__ import annotations

from fastapi import FastAPI

from .accounts import register as register_admin_accounts_standard_feature
from .images import register as register_admin_images_standard_feature


def register(app: FastAPI) -> None:
    register_admin_accounts_standard_feature(app)
    register_admin_images_standard_feature(app)
