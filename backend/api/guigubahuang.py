from __future__ import annotations

from fastapi import APIRouter

from backend.core.guigubahuang import load_guigubahuang_guide


router = APIRouter()


@router.get("/guide")
def get_guigubahuang_guide() -> dict:
    return load_guigubahuang_guide()
