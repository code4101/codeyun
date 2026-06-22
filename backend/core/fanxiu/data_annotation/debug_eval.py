from __future__ import annotations

import contextlib
import io
import json
import threading
import time
from types import GeneratorType
from typing import Any

from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_manual_job_definition,
    normalize_data_annotation_debug_eval_payload,
    register_fanxiu_data_annotation_manual_job,
)


class DataAnnotationRuntimeDebugContext:
    """Stable facade injected as ``ctx`` for ``debug_eval`` manual jobs."""

    def __init__(
        self,
        runner: Any,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        readonly: bool = True,
    ) -> None:
        self._runner = runner
        self._ctx = ctx
        self._stop_event = stop_event
        self.readonly = bool(readonly)
        self.output: list[Any] = []

    @property
    def raw(self) -> dict[str, Any]:
        return self._ctx

    def check_stop(self) -> None:
        self._runner._raise_if_stopped(self._stop_event)

    def log(self, value: Any, *, kind: str = "detail") -> Any:
        message = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        self.output.append(value)
        self._runner._log(kind, str(message)[:1000])
        return value

    def table(self, rows: Any) -> Any:
        return self.log(rows)

    def status(self) -> dict[str, Any]:
        return self._runner.status()

    def frame(self) -> str:
        self.check_stop()
        return self._runner._screencap(self._ctx)

    def scene(self, frame: str | None = None, preferred_scene_ids: list[int] | None = None) -> dict[str, Any]:
        self.check_stop()
        frame_data_url = frame or self.frame()
        scene_id, score = self._runner._identify_scene_number(self._ctx, frame_data_url, preferred_scene_ids)
        with self._runner._lock:
            self._runner._status.update({
                "phase": "debug_eval",
                "current_scene": scene_id,
                "message": f"debug_eval 场景识别：{('#' + str(scene_id)) if scene_id is not None else 'unknown'} {score:.0f}%",
                "updated_at": time.time(),
            })
        return {"scene_id": scene_id, "score": score}

    def ocr(self, frame: str | None = None) -> list[dict[str, Any]]:
        self.check_stop()
        return self._runner._ocr_lines(frame or self.frame())

    def ocr_words_in_shapes(
        self,
        scene: int | str,
        shape_titles: list[str] | tuple[str, ...],
        *,
        frame: str | None = None,
        frame_data_url: str | None = None,
        padding: int = 0,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.check_stop()
        runtime = self._runner._fanxiu_runtime(self._ctx, stop_event=self._stop_event)
        return runtime.ocr_words_in_shapes(
            scene,
            tuple(shape_titles),
            frame_data_url=frame_data_url or frame or self.frame(),
            padding=padding,
            options=options,
        )

    def image(self, scene: int | str) -> dict[str, Any] | None:
        images: dict[int, dict[str, Any]] = self._ctx.get("images") or {}
        if isinstance(scene, int):
            return images.get(scene)
        text = str(scene or "").strip()
        if text.isdigit():
            return images.get(int(text))
        if text in self._runner.scene_ids:
            return images.get(self._runner.scene_ids[text])
        for image in images.values():
            if str(image.get("title") or "").strip() == text:
                return image
        return None

    def shape(self, scene: int | str, title: str, *, contains: bool = False) -> dict[str, Any] | None:
        return self._runner._find_shape(self.image(scene), title, contains=contains)

    def shape_score(self, scene: int | str, title: str, *, frame: str | None = None, contains: bool = False) -> float:
        image = self.image(scene)
        shape = self._runner._find_shape(image, title, contains=contains)
        if not image or not shape:
            raise RuntimeError(f"找不到标注：scene={scene} shape={title}")
        frame_data_url = frame or self.frame()
        return float(self._runner._shape_score(self._ctx, image, shape, frame_data_url) or 0.0)

    def shape_probe(
        self,
        scene: int | str,
        title: str,
        *,
        frame: str | None = None,
        contains: bool = False,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        image = self.image(scene)
        raw_shape = self._runner._find_shape(image, title, contains=contains)
        if not image or not raw_shape:
            raise RuntimeError(f"找不到标注：scene={scene} shape={title}")
        shape = dict(raw_shape)
        if overrides:
            shape.update(overrides)
        frame_data_url = frame or self.frame()
        condition_results: list[dict[str, Any]] = []
        for condition in self._runner._shape_match_conditions(shape):
            result = self._runner._match_shape(self._ctx, image, shape, frame_data_url, condition=condition)
            condition_results.append({
                "condition": condition,
                "similarity": float(result.get("similarity") or 0.0),
                "matched": bool(result.get("matched")),
                "ocr_text": str(result.get("ocr_text") or "")[:80],
            })
        score = max((float(item["similarity"]) for item in condition_results), default=0.0)
        keys = (
            "title",
            "x",
            "y",
            "w",
            "h",
            "sceneJumpTarget",
            "imageMatchRole",
            "ocrMatchRole",
            "ocrEnabled",
            "ocrText",
            "ocrMatchMode",
            "pixelTolerance",
            "floating",
            "jitterEnabled",
            "jitterRadius",
        )
        return {
            "scene": scene,
            "shape": {key: shape.get(key) for key in keys if key in shape},
            "score": score,
            "scene_threshold": float(getattr(self._runner, "scene_threshold", 80.0) or 80.0),
            "overlay_threshold": float(getattr(self._runner, "overlay_threshold", 55.0) or 55.0),
            "matched": any(bool(item.get("matched")) for item in condition_results),
            "conditions": condition_results,
        }

    def _require_act(self) -> None:
        if self.readonly:
            raise RuntimeError("debug_eval 当前为 readonly，动作调用需要 payload.mode='act'")

    def tap_shape(self, scene: int | str, title: str, *, contains: bool = False, frame: str | None = None) -> None:
        self._require_act()
        image = self.image(scene)
        shape = self._runner._find_shape(image, title, contains=contains)
        if not image or not shape:
            raise RuntimeError(f"找不到标注：scene={scene} shape={title}")
        self._runner._click_shape(self._ctx, image, shape, frame_data_url=frame)

    def wait_click(self, frame: int | str | None, shape: str, **options: Any):
        self._require_act()
        runtime = self._runner._fanxiu_runtime(self._ctx, stop_event=self._stop_event)
        return (yield from runtime.wait_click(frame, shape, **options))

    def wait_click_then_view(self, frame: int | str, shape: str, *targets: int | str, **options: Any):
        self._require_act()
        runtime = self._runner._fanxiu_runtime(self._ctx, stop_event=self._stop_event)
        return (yield from runtime.wait_click_then_view(frame, shape, *targets, **options))

    def tap(self, scene: int | str, x: float, y: float) -> None:
        self._require_act()
        image = self.image(scene)
        if not image:
            raise RuntimeError(f"找不到场景标注：{scene}")
        self._runner._click_frame_point(self._ctx, image, x, y)

    def drag(self, scene: int | str, start_x: float, start_y: float, end_x: float, end_y: float, duration_ms: int = 1000) -> None:
        self._require_act()
        image = self.image(scene)
        if not image:
            raise RuntimeError(f"找不到场景标注：{scene}")
        self._runner._drag_frame_point(self._ctx, image, start_x, start_y, end_x, end_y, duration_ms=duration_ms)

    def yield_tick(self):
        self.check_stop()
        yield BehaviorTreeStatus.RUNNING


def run_data_annotation_debug_eval(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Any:
    payload = normalize_data_annotation_debug_eval_payload(payload)
    debug_ctx = DataAnnotationRuntimeDebugContext(runner, ctx, stop_event, readonly=payload["mode"] != "act")
    namespace: dict[str, Any] = {
        "ctx": debug_ctx,
        "payload": payload,
        "BehaviorTreeStatus": BehaviorTreeStatus,
        "json": json,
        "time": time,
    }
    stdout = io.StringIO()
    with runner._lock:
        runner._set_status_locked("running", f"debug_eval 执行中：{payload['mode']}", phase="debug_eval")
    with contextlib.redirect_stdout(stdout):
        exec(payload["code"], namespace, namespace)
        result = None
        task = namespace.get("task")
        if payload.get("call_task") and callable(task):
            result = task(debug_ctx)
        elif "result" in namespace:
            result = namespace["result"]
    output = stdout.getvalue().strip()
    max_chars = int(payload.get("max_output_chars") or 4000)
    if output:
        runner._log("detail", f"debug_eval stdout: {output[:max_chars]}")
    if debug_ctx.output:
        runner._log("detail", f"debug_eval output: {json.dumps(debug_ctx.output, ensure_ascii=False, default=str)[:max_chars]}")
    if isinstance(result, GeneratorType):
        return result
    if result is not None:
        runner._log("detail", f"debug_eval result: {json.dumps(result, ensure_ascii=False, default=str)[:max_chars]}")
    return "success"


def register_fanxiu_data_annotation_debug_eval_job() -> None:
    if get_fanxiu_data_annotation_manual_job_definition("debug_eval") is not None:
        return

    @register_fanxiu_data_annotation_manual_job(
        "debug_eval",
        "调试代码",
        scheduler_supported=False,
        normalize_payload=normalize_data_annotation_debug_eval_payload,
    )
    def _run_data_annotation_debug_eval_manual_job(
        runner: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> Any:
        return run_data_annotation_debug_eval(runner, ctx, payload, stop_event)

