from __future__ import annotations

from fastapi import FastAPI

from .chars import register as register_fanxiu_chars_standard_feature
from .status import register as register_fanxiu_status_standard_feature


def register(app: FastAPI) -> None:
    register_fanxiu_status_standard_feature(app)
    register_fanxiu_chars_standard_feature(app)
