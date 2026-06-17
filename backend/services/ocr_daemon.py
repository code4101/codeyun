from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.runtime.process_launcher import install_child_process_no_window_default

install_child_process_no_window_default()

from backend.core.ocr.preview import (
    OcrPreviewError,
    OcrShapeType,
    ocr_service_manager,
    run_local_paddle_ocr_preview,
)
from backend.core.settings import get_settings


class OcrPredictRequest(BaseModel):
    image: str = Field(min_length=1)
    shape_type: OcrShapeType = "rectangle"
    options: dict[str, Any] = Field(default_factory=dict)


router = APIRouter()


def _decode_request_image(value: str) -> bytes:
    payload = (value or "").strip()
    if "," in payload and payload.split(",", 1)[0].lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image 必须是 base64 图片字符串") from exc

    max_bytes = get_settings().service_request_max_image_bytes
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"图片超过服务限制：{max_bytes} bytes")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image 不能为空")
    return image_bytes


def _service_status() -> dict[str, Any]:
    status = ocr_service_manager.get_status()
    return {
        **status,
        "running": True,
        "process_id": os.getpid(),
        "state": status.get("state") or "cold",
        "state_label": {
            "running": "识别中",
            "idle": "已加载",
            "cold": "待加载",
        }.get(str(status.get("state") or "cold"), str(status.get("state") or "未知")),
    }


@router.get("/status")
def get_ocr_status():
    return {"ok": True, "service": _service_status()}


@router.post("/reset")
def reset_ocr_service():
    ocr_service_manager.reset()
    return {"ok": True, "service": _service_status()}


def _log_predict_request(request: Request, *, image_bytes: bytes, shape_type: str) -> None:
    try:
        status = ocr_service_manager.get_status()
        row = {
            "event": "ocr_predict",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid": os.getpid(),
            "client": request.client.host if request.client else "",
            "caller": (request.headers.get("x-codeyun-ocr-caller") or "")[:500],
            "user_agent": (request.headers.get("user-agent") or "")[:200],
            "image_bytes": len(image_bytes),
            "shape_type": shape_type,
            "state": status.get("state"),
            "loaded": status.get("loaded"),
            "call_count": status.get("call_count"),
        }
        print(json.dumps(row, ensure_ascii=False), flush=True)
    except Exception:
        pass


@router.post("/predict")
def predict_ocr(req: OcrPredictRequest, request: Request):
    image_bytes = _decode_request_image(req.image)
    _log_predict_request(request, image_bytes=image_bytes, shape_type=req.shape_type)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(image_bytes)
            temp_path = temp_file.name
        preview = run_local_paddle_ocr_preview(Path(temp_path), shape_type=req.shape_type, options=req.options)
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    return {
        "ok": True,
        "engine": preview["engine"],
        "shape_type": preview["shape_type"],
        "shape_count": preview["shape_count"],
        "document": preview["document"],
    }


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _idle_exit_grace_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CODEYUN_OCR_IDLE_EXIT_GRACE_SECONDS") or 15))
    except ValueError:
        return 15.0


def _idle_exit_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(30.0):
        status = ocr_service_manager.get_status()
        if status.get("active_instance_count"):
            continue
        last_used_at = status.get("last_used_at")
        if last_used_at is None:
            continue
        try:
            idle_for = time.time() - float(last_used_at)
        except (TypeError, ValueError):
            continue
        idle_timeout = float(status.get("idle_timeout_seconds") or 0)
        if status.get("state") == "cold" and idle_for >= idle_timeout + _idle_exit_grace_seconds():
            os._exit(0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ocr_service_manager.start_idle_cleanup_thread()
    idle_exit_stop = threading.Event()
    idle_exit_thread: threading.Thread | None = None
    if _env_flag("CODEYUN_OCR_EXIT_AFTER_IDLE", default=True):
        idle_exit_thread = threading.Thread(
            target=_idle_exit_loop,
            args=(idle_exit_stop,),
            name="codeyun-ocr-idle-exit",
            daemon=True,
        )
        idle_exit_thread.start()
    try:
        yield
    finally:
        idle_exit_stop.set()
        ocr_service_manager.stop_idle_cleanup_thread()
        if idle_exit_thread and idle_exit_thread.is_alive():
            idle_exit_thread.join(timeout=2)


app = FastAPI(
    title="CodeYun OCR Service",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(router, prefix="/api/services/ocr")


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Run the standalone CodeYun OCR service.")
    parser.add_argument("--host", default=os.getenv("CODEYUN_OCR_SERVICE_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("CODEYUN_OCR_SERVICE_PORT") or 8765))
    args = parser.parse_args(argv)

    uvicorn.run(
        "backend.services.ocr_daemon:app",
        host=args.host,
        port=args.port,
        loop="asyncio",
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
