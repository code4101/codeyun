from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from backend.core.fanxiu.data_annotation.ocr_spatial import segment_ocr_tokens


@dataclass(frozen=True)
class ActivityStoreNumericTarget:
    value: int
    text: str
    is_cash: bool
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass(frozen=True)
class ActivityStoreRegionScan:
    targets: tuple[ActivityStoreNumericTarget, ...]


@dataclass(frozen=True)
class ActivityStoreOperationResult:
    clicked_values: tuple[int, ...]
    remaining_targets: tuple[ActivityStoreNumericTarget, ...]
    completed: bool = True


def _token_box_union(tokens: Iterable[dict[str, Any]]) -> dict[str, float] | None:
    boxes = [
        (
            float(token.get("x") or 0),
            float(token.get("y") or 0),
            float(token.get("w") or 0),
            float(token.get("h") or 0),
        )
        for token in tokens
        if isinstance(token, dict)
    ]
    boxes = [box for box in boxes if box[2] > 0 and box[3] > 0]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def scan_activity_store_region(tokens: Sequence[dict[str, Any]]) -> ActivityStoreRegionScan:
    """Find click-safe integer prices and classify whether each is followed by ``元``.

    The caller is responsible for passing OCR tokens already clipped to the
    store's price/action region.  A numeric match is clickable only when its
    exact OCR token boxes are available; this never estimates a substring box
    inside a larger token.
    """

    targets: list[ActivityStoreNumericTarget] = []
    for segment in segment_ocr_tokens(tokens):
        normalized_parts = [
            unicodedata.normalize("NFKC", str(token.get("text") or ""))
            for token in segment
        ]
        segment_text = "".join(normalized_parts)
        token_ranges: list[tuple[int, int, dict[str, Any]]] = []
        cursor = 0
        for token, token_text in zip(segment, normalized_parts):
            token_ranges.append((cursor, cursor + len(token_text), token))
            cursor += len(token_text)
        boundaries = {
            boundary
            for start, end, _token in token_ranges
            for boundary in (start, end)
        }
        for match in re.finditer(r"[0-9]+", segment_text):
            value = int(match.group(0))
            suffix = segment_text[match.end() :]
            is_cash = re.match(r"\s*元", suffix) is not None
            if match.start() not in boundaries or match.end() not in boundaries:
                continue
            selected = [
                token
                for start, end, token in token_ranges
                if end > match.start() and start < match.end()
            ]
            box = _token_box_union(selected)
            if box is None:
                continue
            targets.append(
                ActivityStoreNumericTarget(
                    value=value,
                    text=match.group(0),
                    is_cash=is_cash,
                    **box,
                )
            )
    targets.sort(key=lambda target: (target.y, target.x))
    return ActivityStoreRegionScan(targets=tuple(targets))


def _scan_signature(scan: ActivityStoreRegionScan) -> tuple[Any, ...]:
    return (
        tuple(
            (
                target.value,
                target.is_cash,
                round(target.x, 0),
                round(target.y, 0),
                round(target.w, 0),
                round(target.h, 0),
            )
            for target in scan.targets
        ),
    )


def _scans_stably_equivalent(
    previous: ActivityStoreRegionScan | None,
    current: ActivityStoreRegionScan,
    *,
    coordinate_tolerance: float = 6.0,
) -> bool:
    """Treat small OCR-box jitter as one stable rendered store state.

    Live PaddleOCR boxes commonly move by a few pixels between otherwise
    identical frames.  Exact rounded-coordinate equality made the stability
    gate time out forever on animated Revenue store pages.  Values, cash
    classification and item ordering remain exact; only box geometry gets a
    bounded tolerance.
    """

    if previous is None or len(previous.targets) != len(current.targets):
        return False
    tolerance = max(0.0, float(coordinate_tolerance))
    for left, right in zip(previous.targets, current.targets):
        if (left.value, left.is_cash) != (right.value, right.is_cash):
            return False
        if any(
            abs(a - b) > tolerance
            for a, b in (
                (left.x, right.x),
                (left.y, right.y),
                (left.w, right.w),
                (left.h, right.h),
            )
        ):
            return False
    return True


def _read_stable_store_scan(
    runtime: Any,
    *,
    scene_id: int,
    region_title: str,
    stability_timeout_seconds: float,
    stability_poll_seconds: float,
) -> tuple[str, ActivityStoreRegionScan]:
    deadline = time.monotonic() + max(0.5, float(stability_timeout_seconds))
    previous_scan: ActivityStoreRegionScan | None = None
    while True:
        current_scene, score, frame = runtime.current_scene([int(scene_id)], update=True)
        if int(current_scene or 0) == int(scene_id) and float(score or 0) >= 80.0:
            tokens = runtime.ocr_tokens_in_shapes(
                int(scene_id),
                [str(region_title)],
                frame_data_url=frame,
                padding=0,
            )
            scan = scan_activity_store_region(tokens)
            if _scans_stably_equivalent(previous_scan, scan):
                return frame, scan
            previous_scan = scan
        else:
            previous_scan = None
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"#{scene_id}[{region_title}] 在 {stability_timeout_seconds:.1f}s 内未形成连续稳定帧"
            )
        time.sleep(max(0.05, float(stability_poll_seconds)))


def _wait_store_after_purchase(
    runtime: Any,
    *,
    scene_id: int,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while True:
        current_scene, score, _frame = runtime.current_scene([int(scene_id), 227], update=True)
        if int(current_scene or 0) == int(scene_id) and float(score or 0) >= 80.0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"点击活动商店目标后未在 {timeout_seconds:.1f}s 内返回 #{scene_id}")
        time.sleep(max(0.05, float(poll_seconds)))


def operate_activity_store_region(
    runtime: Any,
    *,
    scene_id: int,
    select_targets: Callable[
        [ActivityStoreRegionScan], Sequence[ActivityStoreNumericTarget]
    ],
    region_title: str = "区域",
    max_clicks: int = 20,
    stability_timeout_seconds: float = 6.0,
    purchase_timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
) -> ActivityStoreOperationResult:
    """Apply an explicit business selector to stable numeric store targets.

    The infrastructure has no default spending policy.  Every caller must
    provide ``select_targets`` and therefore explicitly decide which observed
    targets, including cash and non-cash prices, are authorized for this
    business operation.  An empty selection is a successful idempotent stop.
    """

    clicked_values: list[int] = []
    previous_signature: tuple[Any, ...] | None = None
    click_limit = max(1, int(max_clicks))
    while len(clicked_values) < click_limit:
        _frame, scan = _read_stable_store_scan(
            runtime,
            scene_id=int(scene_id),
            region_title=str(region_title),
            stability_timeout_seconds=stability_timeout_seconds,
            stability_poll_seconds=poll_seconds,
        )
        selected = tuple(select_targets(scan))
        unknown = [target for target in selected if target not in scan.targets]
        if unknown:
            raise ValueError("活动商店业务选择器返回了当前稳定帧中不存在的目标")
        if not selected:
            return ActivityStoreOperationResult(
                clicked_values=tuple(clicked_values),
                remaining_targets=scan.targets,
            )
        signature = _scan_signature(scan)
        if previous_signature is not None and signature == previous_signature:
            raise RuntimeError(
                f"#{scene_id}[{region_title}] 点击后目标未收敛，拒绝重复点击：{signature[0]}"
            )
        target = selected[0]
        click_x, click_y = target.center
        runtime.click_frame_point(int(scene_id), click_x, click_y)
        clicked_values.append(target.value)
        previous_signature = signature
        _wait_store_after_purchase(
            runtime,
            scene_id=int(scene_id),
            timeout_seconds=purchase_timeout_seconds,
            poll_seconds=poll_seconds,
        )
    raise RuntimeError(
        f"#{scene_id}[{region_title}] 超过最多 {click_limit} 次点击仍未收敛"
    )
