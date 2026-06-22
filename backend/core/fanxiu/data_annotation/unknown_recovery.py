from __future__ import annotations

import io
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.core.temp_paths import codeyun_temp_root
from pyxllib.autogui import View


_EXIT_SHAPE_KEYWORDS = ("返回", "关闭", "离开", "取消", "空白")


@dataclass(frozen=True)
class UnknownShapeScore:
    title: str
    score: float
    is_scene_identity: bool
    scene_identity_role: str
    scene_identity_scope: str
    pixel_tolerance: int | None
    image_match_role: str
    ocr_match_role: str


@dataclass(frozen=True)
class UnknownSceneCandidate:
    scene_id: int
    title: str
    scene_score: float
    frame_similarity: float | None
    identity_scores: list[UnknownShapeScore]
    exit_shapes: list[str]
    expected: bool


@dataclass(frozen=True)
class UnknownEvidence:
    label: str
    expected_scene_ids: list[int]
    last_scene_id: int | None
    last_score: float
    classification: str
    frame_path: str | None
    report_path: str | None
    frame_stability_score: float | None
    candidates: list[UnknownSceneCandidate]
    ocr_texts: list[str]
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _save_frame_if_possible(runner: Any, frame_data_url: str, *, label: str) -> tuple[str | None, Path | None]:
    if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
        return None, None
    root = codeyun_temp_root("fanxiu_unknown", time.strftime("%Y%m%d"))
    stem = f"{int(time.time() * 1000)}_{''.join(ch if ch.isalnum() else '_' for ch in label)[:40]}"
    frame_path = root / f"{stem}.png"
    frame_path.write_bytes(runner._decode_frame_data_url(frame_data_url))
    return str(frame_path), root / f"{stem}.json"


def _image_similarity_percent(runner: Any, left_frame_data_url: str | None, right_frame_data_url: str | None) -> float | None:
    if not (
        isinstance(left_frame_data_url, str)
        and isinstance(right_frame_data_url, str)
        and left_frame_data_url.startswith("data:image")
        and right_frame_data_url.startswith("data:image")
    ):
        return None
    try:
        from PIL import Image

        left_bytes = runner._decode_frame_data_url(left_frame_data_url)
        right_bytes = runner._decode_frame_data_url(right_frame_data_url)
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(io.BytesIO(left_bytes)) as left_image, Image.open(io.BytesIO(right_bytes)) as right_image:
            left = left_image.convert("L").resize((32, 32), resampling).tobytes()
            right = right_image.convert("L").resize((32, 32), resampling).tobytes()
        if not left or len(left) != len(right):
            return None
        total_delta = sum(abs(a - b) for a, b in zip(left, right))
        return round(max(0.0, 100.0 * (1.0 - total_delta / (255.0 * len(left)))), 1)
    except Exception:
        return None


def _image_bytes_similarity_percent(left_bytes: bytes, right_bytes: bytes) -> float | None:
    try:
        from PIL import Image

        resampling = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(io.BytesIO(left_bytes)) as left_image, Image.open(io.BytesIO(right_bytes)) as right_image:
            left = left_image.convert("L").resize((32, 32), resampling).tobytes()
            right = right_image.convert("L").resize((32, 32), resampling).tobytes()
        if not left or len(left) != len(right):
            return None
        total_delta = sum(abs(a - b) for a, b in zip(left, right))
        return round(max(0.0, 100.0 * (1.0 - total_delta / (255.0 * len(left)))), 1)
    except Exception:
        return None


def _reference_frame_bytes(image: dict[str, Any]) -> bytes | None:
    filename = str(image.get("filename") or "").strip()
    if not filename:
        return None
    for resolver_name in ("get_fanxiu_match_frame_path", "get_fanxiu_screenshot_path"):
        try:
            from backend.core.fanxiu.runtime import mumu_control

            resolver = getattr(mumu_control, resolver_name)
            path = resolver(filename)
            return Path(path).read_bytes()
        except Exception:
            continue
    return None


def _reference_frame_similarity(runner: Any, image: dict[str, Any], frame_data_url: str) -> float | None:
    if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
        return None
    reference = _reference_frame_bytes(image)
    if not reference:
        return None
    try:
        current = runner._decode_frame_data_url(frame_data_url)
    except Exception:
        return None
    return _image_bytes_similarity_percent(reference, current)


def _shape_identity_scope(runner: Any, shape: dict[str, Any]) -> str:
    try:
        return str(runner._scene_identity_scope(shape))
    except Exception:
        return str(shape.get("sceneIdentityScope") or "")


def _shape_score_detail(runner: Any, ctx: dict[str, Any], image: dict[str, Any], shape: dict[str, Any], frame: str) -> UnknownShapeScore:
    try:
        score = _safe_float(runner._scene_identity_shape_score(ctx, image, shape, frame))
    except Exception:
        try:
            score = _safe_float(runner._shape_score(ctx, image, shape, frame, ocr_fallback=False))
        except Exception:
            score = 0.0
    return UnknownShapeScore(
        title=str(shape.get("title") or shape.get("id") or ""),
        score=round(score, 1),
        is_scene_identity=bool(shape.get("isSceneIdentity")),
        scene_identity_role=str(shape.get("sceneIdentityRole") or ""),
        scene_identity_scope=_shape_identity_scope(runner, shape),
        pixel_tolerance=_safe_int(shape.get("pixelTolerance")),
        image_match_role=str(shape.get("imageMatchRole") or ""),
        ocr_match_role=str(shape.get("ocrMatchRole") or ""),
    )


def _candidate_scene_ids(runner: Any, ctx: dict[str, Any], expected_scene_ids: list[int], *, max_candidates: int) -> list[int]:
    ids: list[int] = []
    for scene_id in expected_scene_ids:
        if scene_id not in ids:
            ids.append(scene_id)
    try:
        for scene_id in runner._runtime_scene_candidate_ids(ctx):
            scene_id = int(scene_id)
            if scene_id not in ids:
                ids.append(scene_id)
    except Exception:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in images:
            try:
                scene_id = int(scene_id)
            except (TypeError, ValueError):
                continue
            if scene_id not in ids:
                ids.append(scene_id)
    return ids[:max_candidates]


def _exit_shapes(image: dict[str, Any]) -> list[str]:
    result: list[str] = []
    try:
        shapes = View(image).get_shapes(include_groups=False)
    except Exception:
        shapes = []
    for shape in shapes:
        title = str(shape.title or "")
        if title and any(keyword in title for keyword in _EXIT_SHAPE_KEYWORDS):
            result.append(title)
    return result


def _build_candidates(
    runner: Any,
    ctx: dict[str, Any],
    frame_data_url: str,
    expected_scene_ids: list[int],
    *,
    max_candidates: int,
) -> list[UnknownSceneCandidate]:
    images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
    candidates: list[UnknownSceneCandidate] = []
    for scene_id in _candidate_scene_ids(runner, ctx, expected_scene_ids, max_candidates=max_candidates):
        image = images.get(scene_id)
        if not isinstance(image, dict):
            continue
        previous_scope = ctx.get("_scene_identity_scope_filter")
        if scene_id in expected_scene_ids:
            ctx["_scene_identity_scope_filter"] = "local_or_global"
        try:
            identity_shapes = runner._scene_identity_shapes(image)
            identity_scores = [
                _shape_score_detail(runner, ctx, image, shape, frame_data_url)
                for shape in identity_shapes
            ]
            try:
                scene_score = _safe_float(runner._scene_score(ctx, image, frame_data_url))
            except Exception:
                scene_score = min((item.score for item in identity_scores), default=0.0)
            frame_similarity = _reference_frame_similarity(runner, image, frame_data_url)
        finally:
            if scene_id in expected_scene_ids:
                if previous_scope is None:
                    ctx.pop("_scene_identity_scope_filter", None)
                else:
                    ctx["_scene_identity_scope_filter"] = previous_scope
        candidates.append(UnknownSceneCandidate(
            scene_id=int(scene_id),
            title=str(image.get("title") or image.get("filename") or ""),
            scene_score=round(scene_score, 1),
            frame_similarity=frame_similarity,
            identity_scores=identity_scores,
            exit_shapes=_exit_shapes(image),
            expected=scene_id in expected_scene_ids,
        ))
    return sorted(
        candidates,
        key=lambda item: (not item.expected, -max(item.scene_score, item.frame_similarity or 0.0), item.scene_id),
    )


def _classification_and_suggestion(
    candidates: list[UnknownSceneCandidate],
    expected_scene_ids: list[int],
    *,
    frame_stability_score: float | None,
) -> tuple[str, str]:
    expected = [item for item in candidates if item.scene_id in expected_scene_ids]
    all_items = candidates
    best = max(all_items, key=lambda item: max(item.scene_score, item.frame_similarity or 0.0), default=None)
    best_expected = max(expected, key=lambda item: item.scene_score, default=None)
    if best_expected:
        scores = [shape.score for shape in best_expected.identity_scores]
        if scores and max(scores) >= 80.0 and min(scores) < 80.0:
            weak = sorted(best_expected.identity_scores, key=lambda item: item.score)[0]
            return (
                "target_identity_partial_match",
                f"目标 #{best_expected.scene_id} 有局部身份锚点命中，但「{weak.title}」仅 {weak.score:.0f}%，优先检查标注框/像素容差/OCR 条件。",
            )
        if best_expected.scene_score >= 70.0:
            return (
                "target_scene_low_confidence",
                f"目标 #{best_expected.scene_id} 接近命中 {best_expected.scene_score:.0f}%，优先验证匹配参数而不是改业务路径。",
            )
    if best and best.frame_similarity is not None and best.frame_similarity >= 80.0:
        return (
            "matched_existing_frame",
            f"当前帧与已有 #{best.scene_id}「{best.title}」全图相似 {best.frame_similarity:.0f}%，可优先复用该帧的低风险返回/关闭路径。",
        )
    if best and best.scene_score >= 80.0:
        return (
            "matched_existing_frame",
            f"当前帧更像已有 #{best.scene_id}「{best.title}」{best.scene_score:.0f}%，可优先复用该帧的低风险返回/关闭路径。",
        )
    if best and best.frame_similarity is not None and best.frame_similarity >= 65.0:
        return (
            "possible_existing_frame_variant",
            f"当前帧与已有 #{best.scene_id}「{best.title}」全图有中等相似 {best.frame_similarity:.0f}%，建议人工判断是否为变体。",
        )
    if best and best.scene_score >= 50.0:
        return (
            "possible_existing_frame_variant",
            f"当前帧与已有 #{best.scene_id}「{best.title}」有中等相似 {best.scene_score:.0f}%，建议人工判断是否为变体。",
        )
    if frame_stability_score is not None and frame_stability_score < 80.0:
        return (
            "transition_unknown",
            f"连续两帧仅 {frame_stability_score:.0f}% 相似，可能仍在过场/动画中；先等待稳定帧再保存或补标。",
        )
    return (
        "hard_unknown",
        "没有高相似已有帧；若稳定帧持续如此，应保存为临时证据并人工补标，不要猜坐标继续执行。",
    )


def build_unknown_evidence(
    runner: Any,
    ctx: dict[str, Any],
    frame_data_url: str,
    *,
    label: str,
    expected_scene_ids: list[int],
    last_scene_id: int | None,
    last_score: float,
    max_candidates: int = 24,
    previous_frame_data_url: str | None = None,
) -> UnknownEvidence:
    expected = [int(item) for item in expected_scene_ids if item is not None]
    frame_path, report_path = _save_frame_if_possible(runner, frame_data_url, label=label)
    frame_stability_score = _image_similarity_percent(runner, previous_frame_data_url, frame_data_url)
    candidates = _build_candidates(runner, ctx, frame_data_url, expected, max_candidates=max_candidates)
    try:
        ocr_lines = runner._cached_ocr_lines(ctx, frame_data_url)
    except Exception:
        ocr_lines = []
    ocr_texts = [str(line.get("text") or "") for line in ocr_lines if isinstance(line, dict) and str(line.get("text") or "").strip()][:80]
    classification, suggestion = _classification_and_suggestion(
        candidates,
        expected,
        frame_stability_score=frame_stability_score,
    )
    evidence = UnknownEvidence(
        label=label,
        expected_scene_ids=expected,
        last_scene_id=last_scene_id,
        last_score=round(_safe_float(last_score), 1),
        classification=classification,
        frame_path=frame_path,
        report_path=str(report_path) if report_path else None,
        frame_stability_score=frame_stability_score,
        candidates=candidates[:8],
        ocr_texts=ocr_texts,
        suggestion=suggestion,
    )
    if report_path is not None:
        report_path.write_text(json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence
