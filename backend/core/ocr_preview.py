from __future__ import annotations

import json
import os
import sys
import gc
import time
import site
from pathlib import Path
from dataclasses import dataclass
from threading import Condition, Event, RLock, Thread
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from backend.core.settings import get_settings

OcrShapeType = Literal["polygon", "rectangle"]


class OcrPreviewError(RuntimeError):
    pass


_OCR_DLL_DIRECTORY_HANDLES: list[Any] = []


@dataclass(frozen=True, slots=True)
class PaddleOcrRuntimeConfig:
    device: str
    lang: str
    use_doc_orientation_classify: bool
    use_doc_unwarping: bool
    use_textline_orientation: bool

    @property
    def key(self) -> tuple[str, str, bool, bool, bool]:
        return (
            self.device,
            self.lang,
            self.use_doc_orientation_classify,
            self.use_doc_unwarping,
            self.use_textline_orientation,
        )


@dataclass(slots=True)
class _OcrInstanceRecord:
    id: int
    config: PaddleOcrRuntimeConfig
    instance: Any
    generation: int
    created_at: float
    last_used_at: float


def _round_float(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _normalize_quad_points(value: Any) -> list[list[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None

    points: list[list[float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        x = _safe_float(item[0])
        y = _safe_float(item[1])
        if x is None or y is None:
            return None
        points.append([x, y])
    return points


def _normalize_rectangle_points(value: Any) -> list[list[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    x1 = _safe_float(value[0])
    y1 = _safe_float(value[1])
    x2 = _safe_float(value[2])
    y2 = _safe_float(value[3])
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return None
    return [[x1, y1], [x2, y2]]


def _polygon_to_rectangle_points(points: list[list[float]]) -> list[list[float]] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [[min(xs), min(ys)], [max(xs), max(ys)]]


def _extract_predict_payload(result: Any) -> dict[str, Any]:
    def _normalize_payload(value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None

        if isinstance(value, dict):
            inner = value.get("res")
            return inner if isinstance(inner, dict) else value

        if isinstance(value, list) and value:
            first_item = value[0]
            if isinstance(first_item, dict):
                inner = first_item.get("res")
                return inner if isinstance(inner, dict) else first_item

        return None

    if hasattr(result, "json"):
        payload = getattr(result, "json")
        if callable(payload):
            payload = payload()
        normalized = _normalize_payload(payload)
        if normalized is not None:
            return normalized

    if hasattr(result, "res"):
        normalized = _normalize_payload(getattr(result, "res"))
        if normalized is not None:
            return normalized

    normalized = _normalize_payload(result)
    if normalized is not None:
        return normalized

    raise OcrPreviewError("PaddleOCR 返回结果格式不支持")


def _build_shape_label_text(payload: dict[str, Any], index: int) -> str:
    texts = payload.get("rec_texts") or []
    scores = payload.get("rec_scores") or []
    angles = payload.get("textline_orientation_angles") or []

    label: dict[str, Any] = {
        "text": texts[index] if index < len(texts) else "",
    }
    if index < len(scores):
        score = _safe_float(scores[index])
        if score is not None:
            label["score"] = _round_float(score)
    if index < len(angles):
        angle = _safe_float(angles[index])
        if angle is not None:
            label["angle"] = _round_float(angle, digits=2)
    return json.dumps(label, ensure_ascii=False)


def build_ocr_labelme_document_from_payload(
    payload: dict[str, Any],
    *,
    image_path: str,
    image_width: int,
    image_height: int,
    shape_type: OcrShapeType = "polygon",
) -> dict[str, Any]:
    shapes: list[dict[str, Any]] = []
    raw_polygons = payload.get("dt_polys") or payload.get("rec_polys") or []
    raw_boxes = payload.get("rec_boxes") or []

    if shape_type == "rectangle":
        for index, raw_box in enumerate(raw_boxes):
            rectangle_points = _normalize_rectangle_points(raw_box)
            if rectangle_points is None:
                polygon_points = _normalize_quad_points(raw_polygons[index]) if index < len(raw_polygons) else None
                rectangle_points = _polygon_to_rectangle_points(polygon_points or [])
            if rectangle_points is None:
                continue
            shapes.append(
                {
                    "label": _build_shape_label_text(payload, index),
                    "points": rectangle_points,
                    "group_id": None,
                    "shape_type": "rectangle",
                    "flags": {},
                }
            )
    else:
        for index, raw_polygon in enumerate(raw_polygons):
            polygon_points = _normalize_quad_points(raw_polygon)
            if polygon_points is None:
                continue
            shapes.append(
                {
                    "label": _build_shape_label_text(payload, index),
                    "points": polygon_points,
                    "group_id": None,
                    "shape_type": "polygon",
                    "flags": {},
                }
            )

    return {
        "version": "5.1.7",
        "flags": {},
        "shapes": shapes,
        "imagePath": Path(image_path).name,
        "imageData": None,
        "imageHeight": image_height,
        "imageWidth": image_width,
    }


def _apply_ocr_runtime_environment(*, device: str) -> None:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    if sys.platform.startswith("win"):
        # PaddleOCR may silently fall back from GPU to CPU on Windows. Keep the
        # CPU fallback on the safer non-MKLDNN path unless explicitly overridden.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        if str(device or "").strip().lower().startswith("gpu"):
            _add_windows_nvidia_dll_directories()


def _iter_site_package_dirs() -> list[Path]:
    candidates = [Path(sys.prefix) / "Lib" / "site-packages"]
    try:
        candidates.extend(Path(item) for item in site.getsitepackages())
    except Exception:
        pass
    try:
        candidates.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve(strict=False)
        key = os.fspath(resolved).lower()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _nvidia_dll_directories() -> list[Path]:
    result: list[Path] = []
    for site_packages in _iter_site_package_dirs():
        nvidia_root = site_packages / "nvidia"
        if not nvidia_root.exists():
            continue
        for package_dir in nvidia_root.iterdir():
            bin_dir = package_dir / "bin"
            if bin_dir.is_dir():
                result.append(bin_dir.resolve(strict=False))
    return result


def _add_windows_nvidia_dll_directories() -> None:
    global _OCR_DLL_DIRECTORY_HANDLES
    dll_dirs = _nvidia_dll_directories()
    if not dll_dirs:
        return
    current_path = os.environ.get("PATH") or ""
    current_parts = {part.lower() for part in current_path.split(os.pathsep) if part}
    missing_parts: list[str] = []
    for dll_dir in dll_dirs:
        dll_dir_text = os.fspath(dll_dir)
        if dll_dir_text.lower() not in current_parts:
            missing_parts.append(dll_dir_text)
        if hasattr(os, "add_dll_directory"):
            try:
                handle = os.add_dll_directory(dll_dir_text)
            except OSError:
                continue
            _OCR_DLL_DIRECTORY_HANDLES.append(handle)
    if missing_parts:
        os.environ["PATH"] = os.pathsep.join([*missing_parts, current_path]) if current_path else os.pathsep.join(missing_parts)


def _assert_gpu_runtime_available() -> None:
    try:
        import paddle
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise OcrPreviewError("OCR 配置为 GPU，但 Paddle 不可用") from exc
    try:
        compiled_with_cuda = bool(paddle.device.is_compiled_with_cuda())
        cuda_count = int(paddle.device.cuda.device_count())
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise OcrPreviewError(f"OCR GPU 运行环境检查失败：{exc}") from exc
    if not compiled_with_cuda:
        raise OcrPreviewError("OCR 配置为 GPU，但当前安装的是非 CUDA 版 Paddle，请安装 paddlepaddle-gpu")
    if cuda_count <= 0:
        raise OcrPreviewError("OCR 配置为 GPU，但 Paddle 未检测到可用 CUDA 设备")


def _coerce_bool_option(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _build_runtime_config(options: dict[str, Any] | None = None) -> PaddleOcrRuntimeConfig:
    settings = get_settings()
    options = options or {}
    device = str(options.get("device") or options.get("ocr_device") or settings.ocr_device).strip().lower() or settings.ocr_device
    lang = str(options.get("lang") or options.get("ocr_lang") or settings.ocr_lang).strip() or settings.ocr_lang
    return PaddleOcrRuntimeConfig(
        device=device,
        lang=lang,
        use_doc_orientation_classify=_coerce_bool_option(
            options.get("use_doc_orientation_classify", options.get("ocr_use_doc_orientation_classify")),
            settings.ocr_use_doc_orientation_classify,
        ),
        use_doc_unwarping=_coerce_bool_option(
            options.get("use_doc_unwarping", options.get("ocr_use_doc_unwarping")),
            settings.ocr_use_doc_unwarping,
        ),
        use_textline_orientation=_coerce_bool_option(
            options.get("use_textline_orientation", options.get("ocr_use_textline_orientation")),
            settings.ocr_use_textline_orientation,
        ),
    )


def _create_ocr_instance(config: PaddleOcrRuntimeConfig) -> Any:
    _apply_ocr_runtime_environment(device=config.device)
    if str(config.device or "").strip().lower().startswith("gpu"):
        _assert_gpu_runtime_available()
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise OcrPreviewError("PaddleOCR 不可用，请先完成 codeyun backend 的 OCR 依赖安装") from exc

    return PaddleOCR(
        lang=config.lang,
        device=config.device,
        use_doc_orientation_classify=config.use_doc_orientation_classify,
        use_doc_unwarping=config.use_doc_unwarping,
        use_textline_orientation=config.use_textline_orientation,
    )


def get_ocr_runtime_diagnostics(device: str | None = None) -> dict[str, Any]:
    resolved_device = str(device or get_settings().ocr_device or "").strip().lower()
    if resolved_device.startswith("gpu"):
        _apply_ocr_runtime_environment(device=resolved_device)
    diagnostics: dict[str, Any] = {
        "device": resolved_device,
        "nvidia_dll_dirs": [os.fspath(path) for path in _nvidia_dll_directories()] if sys.platform.startswith("win") else [],
    }
    try:
        import paddle
    except Exception as exc:  # pragma: no cover - depends on runtime env
        diagnostics.update({
            "paddle_available": False,
            "error": str(exc),
        })
        return diagnostics
    diagnostics.update({
        "paddle_available": True,
        "paddle_version": getattr(paddle, "__version__", ""),
    })
    try:
        diagnostics.update({
            "compiled_with_cuda": bool(paddle.device.is_compiled_with_cuda()),
            "cuda_device_count": int(paddle.device.cuda.device_count()),
            "current_device": str(paddle.device.get_device()),
        })
    except Exception as exc:  # pragma: no cover - depends on runtime env
        diagnostics["error"] = str(exc)
    return diagnostics


def _get_ocr_instance(config: PaddleOcrRuntimeConfig | None = None) -> Any:
    return _create_ocr_instance(config or _build_runtime_config())


class PaddleOcrServiceManager:
    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._idle_instances: list[_OcrInstanceRecord] = []
        self._total_instances = 0
        self._active_instances = 0
        self._next_instance_id = 0
        self._generation = 0
        self._call_count = 0
        self._error_count = 0
        self._last_loaded_at: float | None = None
        self._last_used_at: float | None = None
        self._last_error: str | None = None
        self._cleanup_stop_event: Event | None = None
        self._cleanup_thread: Thread | None = None

    def _settings_limits(self) -> tuple[int, int, float]:
        settings = get_settings()
        return (
            max(1, settings.ocr_max_instances),
            max(30, settings.ocr_idle_timeout_seconds),
            max(0.0, settings.ocr_acquire_timeout_seconds),
        )

    def start_idle_cleanup_thread(self) -> None:
        with self._condition:
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                return
            stop_event = Event()
            self._cleanup_stop_event = stop_event
            self._cleanup_thread = Thread(
                target=self._cleanup_loop,
                name="codeyun-ocr-idle-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()

    def stop_idle_cleanup_thread(self) -> None:
        thread: Thread | None = None
        with self._condition:
            if self._cleanup_stop_event:
                self._cleanup_stop_event.set()
            thread = self._cleanup_thread
            self._cleanup_stop_event = None
            self._cleanup_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _cleanup_loop(self) -> None:
        while True:
            with self._condition:
                stop_event = self._cleanup_stop_event
            if stop_event is None:
                return
            _, idle_timeout_seconds, _ = self._settings_limits()
            wait_seconds = max(15.0, min(60.0, idle_timeout_seconds / 2))
            if stop_event.wait(wait_seconds):
                return
            self.cleanup_idle()

    def _drop_idle_records_locked(self, records: list[_OcrInstanceRecord]) -> None:
        if not records:
            return
        drop_ids = {record.id for record in records}
        self._idle_instances = [record for record in self._idle_instances if record.id not in drop_ids]
        self._total_instances = max(0, self._total_instances - len(records))
        self._condition.notify_all()

    def _cleanup_idle_locked(self, now: float | None = None) -> int:
        now = now or time.time()
        _, idle_timeout_seconds, _ = self._settings_limits()
        expired = [
            record
            for record in self._idle_instances
            if now - record.last_used_at >= idle_timeout_seconds
        ]
        self._drop_idle_records_locked(expired)
        return len(expired)

    def cleanup_idle(self) -> int:
        with self._condition:
            released = self._cleanup_idle_locked()
        if released:
            gc.collect()
        return released

    def reset(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            idle_count = len(self._idle_instances)
            self._idle_instances = []
            self._total_instances = max(0, self._total_instances - idle_count)
            self._condition.notify_all()
        if idle_count:
            gc.collect()
        return self.get_status()

    def warmup(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        config = _build_runtime_config(options)
        record = self._acquire(config)
        self._release(record)
        with self._condition:
            self._last_used_at = time.time()
        return self.get_status()

    def _reserve_or_wait(self, config: PaddleOcrRuntimeConfig) -> tuple[int, int, bool, _OcrInstanceRecord | None]:
        deadline = time.time() + self._settings_limits()[2]
        with self._condition:
            while True:
                max_instances, _, acquire_timeout_seconds = self._settings_limits()
                now = time.time()
                self._cleanup_idle_locked(now)

                for index, record in enumerate(self._idle_instances):
                    if record.config.key == config.key:
                        self._idle_instances.pop(index)
                        self._active_instances += 1
                        return record.id, record.generation, False, record

                if self._total_instances < max_instances:
                    self._next_instance_id += 1
                    instance_id = self._next_instance_id
                    generation = self._generation
                    self._total_instances += 1
                    self._active_instances += 1
                    return instance_id, generation, True, None

                if self._idle_instances:
                    oldest = min(self._idle_instances, key=lambda item: item.last_used_at)
                    self._drop_idle_records_locked([oldest])
                    continue

                remaining = deadline - now
                if acquire_timeout_seconds <= 0 or remaining <= 0:
                    raise OcrPreviewError("OCR 服务繁忙，请稍后重试")
                self._condition.wait(timeout=remaining)

    def _create_record(self, instance_id: int, generation: int, config: PaddleOcrRuntimeConfig) -> _OcrInstanceRecord:
        try:
            try:
                instance = _get_ocr_instance(config)
            except TypeError:
                instance = _get_ocr_instance()
        except OcrPreviewError:
            raise
        except Exception as exc:  # pragma: no cover - depends on runtime env
            raise OcrPreviewError(f"PaddleOCR 初始化失败：{exc}") from exc

        now = time.time()
        with self._condition:
            self._last_loaded_at = now
        return _OcrInstanceRecord(
            id=instance_id,
            config=config,
            instance=instance,
            generation=generation,
            created_at=now,
            last_used_at=now,
        )

    def _acquire(self, config: PaddleOcrRuntimeConfig) -> _OcrInstanceRecord:
        instance_id, generation, should_create, record = self._reserve_or_wait(config)
        if not should_create and record is not None:
            return record

        try:
            return self._create_record(instance_id, generation, config)
        except Exception:
            with self._condition:
                self._active_instances = max(0, self._active_instances - 1)
                self._total_instances = max(0, self._total_instances - 1)
                self._condition.notify_all()
            raise

    def _release(self, record: _OcrInstanceRecord, *, reusable: bool = True) -> None:
        now = time.time()
        record.last_used_at = now
        with self._condition:
            self._active_instances = max(0, self._active_instances - 1)
            if reusable and record.generation == self._generation:
                self._idle_instances.append(record)
            else:
                self._total_instances = max(0, self._total_instances - 1)
            self._condition.notify_all()

    def predict_file(
        self,
        image_path: Path,
        *,
        shape_type: OcrShapeType = "polygon",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with Image.open(image_path) as image:
                image_width, image_height = image.size
        except FileNotFoundError as exc:
            raise OcrPreviewError("图片文件不存在") from exc
        except UnidentifiedImageError as exc:
            raise OcrPreviewError("目标文件不是可识别图片") from exc
        except OSError as exc:
            raise OcrPreviewError(f"读取图片失败：{exc}") from exc

        config = _build_runtime_config(options)
        record = self._acquire(config)
        try:
            results = record.instance.predict(str(image_path))
        except Exception as exc:  # pragma: no cover - depends on runtime env
            message = f"OCR 识别失败：{exc}"
            with self._condition:
                self._error_count += 1
                self._last_error = message
            raise OcrPreviewError(message) from exc
        finally:
            self._release(record)

        result = results[0] if isinstance(results, list) and results else {}
        payload = _extract_predict_payload(result) if result else {}
        document = build_ocr_labelme_document_from_payload(
            payload,
            image_path=str(image_path),
            image_width=image_width,
            image_height=image_height,
            shape_type=shape_type,
        )
        with self._condition:
            self._call_count += 1
            self._last_used_at = time.time()
        return {
            "engine": "paddleocr",
            "shape_type": shape_type,
            "shape_count": len(document["shapes"]),
            "document": document,
        }

    def get_status(self) -> dict[str, Any]:
        now = time.time()
        with self._condition:
            self._cleanup_idle_locked(now)
            settings = get_settings()
            _, idle_timeout_seconds, acquire_timeout_seconds = self._settings_limits()
            latest_idle_used_at = max((record.last_used_at for record in self._idle_instances), default=None)
            idle_expires_at = (
                latest_idle_used_at + idle_timeout_seconds
                if latest_idle_used_at is not None and self._active_instances == 0
                else None
            )
            return {
                "key": "ocr",
                "title": "OCR",
                "engine": "paddleocr",
                "device": settings.ocr_device,
                "lang": settings.ocr_lang,
                "loaded": self._total_instances > 0,
                "state": "running" if self._active_instances else ("idle" if self._total_instances else "cold"),
                "instance_count": self._total_instances,
                "idle_instance_count": len(self._idle_instances),
                "active_instance_count": self._active_instances,
                "max_instances": settings.ocr_max_instances,
                "idle_timeout_seconds": idle_timeout_seconds,
                "idle_expires_at": idle_expires_at,
                "idle_remaining_seconds": (
                    max(0.0, idle_expires_at - now)
                    if idle_expires_at is not None
                    else None
                ),
                "acquire_timeout_seconds": acquire_timeout_seconds,
                "call_count": self._call_count,
                "error_count": self._error_count,
                "last_loaded_at": self._last_loaded_at,
                "last_used_at": self._last_used_at,
                "last_error": self._last_error,
                "runtime": get_ocr_runtime_diagnostics(settings.ocr_device),
                "options": {
                    "use_doc_orientation_classify": settings.ocr_use_doc_orientation_classify,
                    "use_doc_unwarping": settings.ocr_use_doc_unwarping,
                    "use_textline_orientation": settings.ocr_use_textline_orientation,
                },
            }


ocr_service_manager = PaddleOcrServiceManager()


def run_paddle_ocr_preview(
    image_path: Path,
    *,
    shape_type: OcrShapeType = "polygon",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from backend.core.ocr_service_runtime import predict_via_ocr_service, should_use_inline_ocr

    if not should_use_inline_ocr():
        return predict_via_ocr_service(Path(image_path), shape_type=shape_type, options=options)
    return run_local_paddle_ocr_preview(Path(image_path), shape_type=shape_type, options=options)


def run_local_paddle_ocr_preview(
    image_path: Path,
    *,
    shape_type: OcrShapeType = "polygon",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ocr_service_manager.predict_file(Path(image_path), shape_type=shape_type, options=options)
