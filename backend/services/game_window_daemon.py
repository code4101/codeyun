from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.services.launcher import install_child_process_no_window_default

install_child_process_no_window_default()

from backend.core.fanxiu.runtime.mumu_control import (
    activate_mumu_window,
    click_mumu_window_processed_point,
    drag_mumu_window_processed_points,
    stream_mumu_window_mjpeg,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_TARGET_TITLE = "云手机"
DEFAULT_FPS = 12.0
DEFAULT_QUALITY = 82
DEFAULT_AREA = "client"
DEFAULT_MODE = "screen"
DEFAULT_CROP = "0,0,0,0"
DEFAULT_TRIM_BORDER = "0,0,0,0"
DEFAULT_ROTATE = "0"

router = APIRouter()


class GameWindowClickRequest(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field(DEFAULT_MODE, pattern="^(auto|printwindow|screen)$")
    area: str = Field(DEFAULT_AREA, pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field(DEFAULT_ROTATE, pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)


class GameWindowDragRequest(BaseModel):
    start_x: float = Field(ge=0)
    start_y: float = Field(ge=0)
    end_x: float = Field(ge=0)
    end_y: float = Field(ge=0)
    duration_ms: int = Field(300, ge=50, le=3000)
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    mode: str = Field(DEFAULT_MODE, pattern="^(auto|printwindow|screen)$")
    area: str = Field(DEFAULT_AREA, pattern="^(outer|client)$")
    crop: Optional[str] = None
    trim_border: Optional[str] = None
    rotate: str = Field(DEFAULT_ROTATE, pattern="^(0|90|180|270|ccw|cw|none)$")
    fixed_width: int = Field(0, ge=0, le=4096)
    fixed_height: int = Field(0, ge=0, le=4096)
    frame_width: Optional[int] = Field(None, ge=1, le=8192)
    frame_height: Optional[int] = Field(None, ge=1, le=8192)


class GameWindowActivateRequest(BaseModel):
    title: Optional[str] = None
    title_match: str = Field("contains", pattern="^(contains|exact)$")
    click_title: bool = True


def _env_text(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip() or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _default_target_title() -> str:
    return _env_text("CODEYUN_GAME_WINDOW_TARGET_TITLE", DEFAULT_TARGET_TITLE)


def _service_status() -> dict[str, object]:
    return {
        "key": "fanxiu-game-window",
        "title": "凡修画面流",
        "running": True,
        "state": "running",
        "state_label": "运行中",
        "process_id": os.getpid(),
        "target_title": _default_target_title(),
        "fps": _env_float("CODEYUN_GAME_WINDOW_FPS", DEFAULT_FPS),
        "quality": _env_int("CODEYUN_GAME_WINDOW_QUALITY", DEFAULT_QUALITY),
        "mode": _env_text("CODEYUN_GAME_WINDOW_MODE", DEFAULT_MODE),
        "area": _env_text("CODEYUN_GAME_WINDOW_AREA", DEFAULT_AREA),
        "crop": _env_text("CODEYUN_GAME_WINDOW_CROP", DEFAULT_CROP),
        "trim_border": _env_text("CODEYUN_GAME_WINDOW_TRIM_BORDER", DEFAULT_TRIM_BORDER),
        "rotate": _env_text("CODEYUN_GAME_WINDOW_ROTATE", DEFAULT_ROTATE),
    }


@router.get("/status")
def get_game_window_status():
    return {"ok": True, "service": _service_status()}


@router.get("/stream")
def stream_game_window(
    title: Optional[str] = Query(None),
    title_match: str = Query("contains", pattern="^(contains|exact)$"),
    fps: float = Query(DEFAULT_FPS, ge=1.0, le=30.0),
    quality: int = Query(DEFAULT_QUALITY, ge=1, le=100),
    mode: str = Query(DEFAULT_MODE, pattern="^(auto|printwindow|screen)$"),
    area: str = Query(DEFAULT_AREA, pattern="^(outer|client)$"),
    crop: Optional[str] = Query(None),
    trim_border: Optional[str] = Query(None),
    rotate: str = Query(DEFAULT_ROTATE, pattern="^(0|90|180|270|ccw|cw|none)$"),
    fixed_width: int = Query(0, ge=0, le=4096),
    fixed_height: int = Query(0, ge=0, le=4096),
    auto_dismiss_popup: bool = Query(False),
    popup_check_interval: float = Query(3.0, ge=1.0, le=30.0),
):
    try:
        frames = stream_mumu_window_mjpeg(
            title=(title or _default_target_title()).strip() or _default_target_title(),
            title_match=title_match,
            fps=fps,
            quality=quality,
            mode=mode,
            area=area,
            crop=crop or _env_text("CODEYUN_GAME_WINDOW_CROP", DEFAULT_CROP),
            trim_border=trim_border or _env_text("CODEYUN_GAME_WINDOW_TRIM_BORDER", DEFAULT_TRIM_BORDER),
            rotate=rotate or _env_text("CODEYUN_GAME_WINDOW_ROTATE", DEFAULT_ROTATE),
            fixed_width=fixed_width,
            fixed_height=fixed_height,
            auto_dismiss_popup=auto_dismiss_popup,
            popup_check_interval=popup_check_interval,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        frames,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/input/click")
def click_game_window(req: GameWindowClickRequest):
    try:
        result = click_mumu_window_processed_point(
            x=req.x,
            y=req.y,
            title=(req.title or _default_target_title()).strip() or _default_target_title(),
            title_match=req.title_match,
            mode=req.mode,
            area=req.area,
            crop=req.crop or _env_text("CODEYUN_GAME_WINDOW_CROP", DEFAULT_CROP),
            trim_border=req.trim_border or _env_text("CODEYUN_GAME_WINDOW_TRIM_BORDER", DEFAULT_TRIM_BORDER),
            rotate=req.rotate or _env_text("CODEYUN_GAME_WINDOW_ROTATE", DEFAULT_ROTATE),
            fixed_width=req.fixed_width,
            fixed_height=req.fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.frame_width is not None:
        result["client_frame_width"] = req.frame_width
    if req.frame_height is not None:
        result["client_frame_height"] = req.frame_height
    return result


@router.post("/input/activate")
def activate_game_window(req: GameWindowActivateRequest):
    try:
        return activate_mumu_window(
            title=(req.title or _default_target_title()).strip() or _default_target_title(),
            title_match=req.title_match,
            click_title=req.click_title,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/input/drag")
def drag_game_window(req: GameWindowDragRequest):
    try:
        result = drag_mumu_window_processed_points(
            start_x=req.start_x,
            start_y=req.start_y,
            end_x=req.end_x,
            end_y=req.end_y,
            duration_ms=req.duration_ms,
            title=(req.title or _default_target_title()).strip() or _default_target_title(),
            title_match=req.title_match,
            mode=req.mode,
            area=req.area,
            crop=req.crop or _env_text("CODEYUN_GAME_WINDOW_CROP", DEFAULT_CROP),
            trim_border=req.trim_border or _env_text("CODEYUN_GAME_WINDOW_TRIM_BORDER", DEFAULT_TRIM_BORDER),
            rotate=req.rotate or _env_text("CODEYUN_GAME_WINDOW_ROTATE", DEFAULT_ROTATE),
            fixed_width=req.fixed_width,
            fixed_height=req.fixed_height,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.frame_width is not None:
        result["client_frame_width"] = req.frame_width
    if req.frame_height is not None:
        result["client_frame_height"] = req.frame_height
    return result


app = FastAPI(
    title="CodeYun Game Window Service",
    docs_url=None,
    redoc_url=None,
)
app.include_router(router)
app.include_router(router, prefix="/api/services/game-window")


def selector_event_loop_factory():
    import asyncio

    return asyncio.SelectorEventLoop()


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Run the standalone CodeYun game window stream service.")
    parser.add_argument("--host", default=os.getenv("CODEYUN_GAME_WINDOW_SERVICE_HOST") or DEFAULT_HOST)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CODEYUN_GAME_WINDOW_SERVICE_PORT") or DEFAULT_PORT),
    )
    args = parser.parse_args(argv)

    uvicorn.run(
        "backend.services.game_window_daemon:app",
        host=args.host,
        port=args.port,
        loop="backend.services.game_window_daemon:selector_event_loop_factory" if sys.platform == "win32" else "auto",
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()

