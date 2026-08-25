from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.core.hardware_temperature import get_temperature_snapshot
from backend.core.hardware_temperature.local_collector import request_elevated_collector


router = APIRouter()


@router.get("")
def read_hardware_temperatures() -> dict[str, Any]:
    return get_temperature_snapshot()


@router.post("/elevate")
def elevate_hardware_temperature_collector() -> dict[str, Any]:
    return request_elevated_collector()
