"""Stateless, account-neutral recognition of client-owned screenshots.

HTTP authorization, quotas, retries and execution ownership belong to the
remote session service. This module never captures or operates a device.
"""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 4096 * 4096


def plan_remote_observation(
    image_bytes: bytes, *, job_id: str, target_scene_id: int | None = None,
    runtime_snapshot: dict | None = None,
) -> dict[str, Any]:
    """Analyze PNG/JPEG bytes for ``observe`` or one step of ``navigate``.

    An unknown navigation frame requests another observation; the session
    provides the finite wait budget. Unsupported sizes never produce clicks.
    Oversize/malformed input raises ValueError before loading the recognizer.
    """
    if job_id == "runtime_probe":
        from backend.core.fanxiu.remote.runtime_observation import plan_runtime_probe
        return plan_runtime_probe(runtime_snapshot)
    if job_id not in {"observe", "navigate"}:
        raise ValueError("不支持的远程作业")
    if job_id == "navigate" and (target_scene_id is None or target_scene_id <= 0):
        raise ValueError("navigate 必须指定正数 target_scene_id")
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("截图为空或超过 6 MiB")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("截图只支持 PNG/JPEG")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS or min(width, height) < 1 or max(width, height) > 4096:
                raise ValueError("截图像素超过限制")
            image.load()
            # Canonical PNG ensures the OpenCV matching provider receives a
            # decodable frame even for unusual JPEG modes or PNG palettes.
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法解码截图") from exc
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor

    frame = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    # A dedicated executor per request isolates mutable frame/asset/OCR state
    # from local jobs and from other accounts; only immutable assets are shared.
    return BehaviorTreeExecutor().analyze_external_frame(
        frame, frame_width=width, frame_height=height,
        target_scene_id=target_scene_id if job_id == "navigate" else None,
    )
