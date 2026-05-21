from __future__ import annotations

import argparse
import base64
import binascii
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.core.ocr_preview import (
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


@router.post("/predict")
def predict_ocr(req: OcrPredictRequest):
    image_bytes = _decode_request_image(req.image)
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ocr_service_manager.start_idle_cleanup_thread()
    try:
        yield
    finally:
        ocr_service_manager.stop_idle_cleanup_thread()


app = FastAPI(
    title="CodeYun OCR Service",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(router, prefix="/api/services/ocr")


def selector_event_loop_factory():
    import asyncio

    return asyncio.SelectorEventLoop()


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
        loop="backend.services.ocr_daemon:selector_event_loop_factory" if sys.platform == "win32" else "auto",
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
