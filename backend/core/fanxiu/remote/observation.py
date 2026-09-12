"""Stateless, account-neutral recognition of client-owned screenshots.

HTTP authorization, quotas, retries and execution ownership belong to the
remote session service. This module never captures or operates a device.
"""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError
from backend.core.fanxiu.remote.frame_geometry import FrameGeometry

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 4096 * 4096


def plan_remote_observation(
    image_bytes: bytes, *, job_id: str, target_scene_id: int | None = None,
    runtime_snapshot: dict | None = None,
) -> dict[str, Any]:
    """Analyze PNG/JPEG bytes for ``observe`` or one step of ``navigate``.

    Full-frame 9:16 screenshots are normalized to the established asset canvas;
    returned actions remain in the original screenshot pixels. DPI is not a
    coordinate multiplier: layout changes still have to pass scene recognition.
    An unknown frame requests another observation within the session budget.
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
            from backend.core.fanxiu.client.mumu_control import DEFAULT_FIXED_WIDTH, DEFAULT_FIXED_HEIGHT
            geometry = FrameGeometry(width, height, int(DEFAULT_FIXED_WIDTH), int(DEFAULT_FIXED_HEIGHT))
            if not geometry.compatible:
                return {"status": "blocked", "reason": "frame_geometry_unsupported", "observation": {
                    "frame_width": width, "frame_height": height,
                    "expected_aspect_ratio": "9:16", "minimum_frame_size": {"width": 540, "height": 960}}}
            # Canonical PNG ensures the OpenCV matching provider receives a
            # decodable frame even for unusual JPEG modes or PNG palettes.
            output = BytesIO()
            normalized = geometry.normalize_image(image)
            normalized.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法解码截图") from exc
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor

    frame = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    # A dedicated executor per request isolates mutable frame/asset/OCR state
    # from local jobs and from other accounts; only immutable assets are shared.
    result = BehaviorTreeExecutor().analyze_external_frame(
        frame, frame_width=geometry.reference_width, frame_height=geometry.reference_height,
        target_scene_id=target_scene_id if job_id == "navigate" else None,
        sampling_size=(width, height),
    )
    result.setdefault("observation", {}).update(frame_width=width, frame_height=height,
                                               frame_transform=geometry.metadata())
    action = result.get("action")
    if action and action["kind"] == "tap":
        action["x"], action["y"] = geometry.original_point(action["x"], action["y"])
    elif action and action["kind"] == "swipe":
        action["start_x"], action["start_y"] = geometry.original_point(action["start_x"], action["start_y"])
        action["end_x"], action["end_y"] = geometry.original_point(action["end_x"], action["end_y"])
    return result
