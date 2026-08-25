from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path
from ctypes import wintypes
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.devices.window_capture_preview import WindowCandidate, capture_by_printwindow, find_window
from backend.core.ocr.preview import run_local_paddle_ocr_preview
from backend.core.temp_paths import codeyun_temp_root
from backend.core.windows_overlay.anchor_tracking import (
    AdaptiveTemplateAnchorTracker,
    AnchorMatch,
    PerceptualFrameGate,
    TemplateAnchorTracker,
)
from backend.core.windows_overlay.protocol import default_scene_path, write_scene_document


FALLBACK_TITLE = "Prefab：利用基于像素的界面结构逆向工程实现高级交互"
FALLBACK_ABSTRACT = (
    "不同用户界面工具包之间存在巨大差异，使研究者难以在新的和已有应用中实现重要的交互技术，"
    "从而限制了人机交互研究的影响。本文提出 Prefab：它从所有图形界面最终都会绘制像素这一共同点出发，"
    "通过逆向分析屏幕像素恢复界面结构。系统将控件布局建模与控件外观识别分离，并在不同工具包和窗口系统构建的"
    "复杂应用中实现了目标感知指向、过渡效果和参数预览等增强能力。"
)
TRANSPARENT_COLOR = "#010203"
TITLE_OVERLAY_HEIGHT = 42
TITLE_OVERLAY_GAP = 8
DOCUMENT_SAFE_TOP = 150
SCENE_TTL_MS = 750
SCENE_REFRESH_SECONDS = 0.25
PRODUCER_ID = "prefab-translation-demo"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


def _watch_stop_event_name(hwnd: int) -> str:
    return f"Local\\CodeYun.PrefabTranslation.Stop.{hwnd}"


def _kernel32() -> Any:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _create_watch_handles(hwnd: int) -> tuple[int | None, int | None, bool]:
    if os.name != "nt":
        return None, None, False
    kernel32 = _kernel32()
    handle = kernel32.CreateMutexW(None, False, f"Local\\CodeYun.PrefabTranslation.{hwnd}")
    already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    stop_event = kernel32.CreateEventW(None, True, False, _watch_stop_event_name(hwnd))
    if stop_event and not already_running:
        kernel32.ResetEvent(stop_event)
    return handle, stop_event, already_running


def stop_watch_translation(hwnd: int) -> bool:
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, _watch_stop_event_name(hwnd))
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def _shape_text(shape: dict[str, Any]) -> str:
    try:
        return str(json.loads(str(shape.get("label") or "{}"))["text"]).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ""


def _shape_rect(shape: dict[str, Any]) -> tuple[float, float, float, float]:
    points = shape.get("points") or []
    xs = [float(point[0]) for point in points if isinstance(point, list) and len(point) >= 2]
    ys = [float(point[1]) for point in points if isinstance(point, list) and len(point) >= 2]
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def extract_article_regions(payload: dict[str, Any]) -> dict[str, Any]:
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    shapes = [shape for shape in document.get("shapes") or [] if isinstance(shape, dict)]
    rows = [
        {"text": _shape_text(shape), "rect": _shape_rect(shape)}
        for shape in shapes
        if _shape_text(shape)
    ]
    title_rows = [row for row in rows if 300 <= row["rect"][1] <= 455 and row["rect"][2] - row["rect"][0] > 400]
    abstract_heading = next((row for row in rows if row["text"].upper() == "ABSTRACT"), None)
    keyword_heading = next((row for row in rows if row["text"].lower().startswith("author keywords")), None)
    if abstract_heading and keyword_heading:
        abstract_top = abstract_heading["rect"][3]
        abstract_bottom = keyword_heading["rect"][1]
        abstract_rows = [
            row
            for row in rows
            if abstract_top <= row["rect"][1] < abstract_bottom
            and row["rect"][0] < float(document.get("imageWidth") or 1440) / 2
        ]
    else:
        abstract_rows = []
    abstract_rows.sort(key=lambda row: (row["rect"][1], row["rect"][0]))
    title_rows.sort(key=lambda row: (row["rect"][1], row["rect"][0]))
    return {
        "width": int(document.get("imageWidth") or 1440),
        "height": int(document.get("imageHeight") or 2512),
        "title": " ".join(row["text"] for row in title_rows),
        "abstract": " ".join(row["text"] for row in abstract_rows),
        "title_rect": _union_rect(title_rows, fallback=(100, 325, 1340, 450)),
        "abstract_heading_rect": _union_rect(
            [abstract_heading] if abstract_heading else [],
            fallback=(64, 620, 190, 655),
        ),
        "abstract_rect": _union_rect(abstract_rows, fallback=(55, 660, 705, 1235)),
    }


def _union_rect(rows: list[dict[str, Any]], *, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not rows:
        return fallback
    return (
        round(min(row["rect"][0] for row in rows)),
        round(min(row["rect"][1] for row in rows)),
        round(max(row["rect"][2] for row in rows)),
        round(max(row["rect"][3] for row in rows)),
    )


def translate_to_chinese(text: str, *, fallback: str) -> str:
    if not text.strip():
        return fallback
    query = urlencode({
        "client": "gtx",
        "sl": "en",
        "tl": "zh-CN",
        "dt": "t",
        "q": text,
    })
    request = Request(
        "https://translate.googleapis.com/translate_a/single?" + query,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated = "".join(
            str(part[0])
            for part in (payload[0] if isinstance(payload, list) and payload else [])
            if isinstance(part, list) and part and part[0]
        ).strip()
        return translated or fallback
    except Exception:
        return fallback


def build_title_scene(
    *,
    hwnd: int,
    width: int,
    height: int,
    title_rect: tuple[int, int, int, int] | None,
    translated_title: str,
    abstract_heading_rect: tuple[int, int, int, int] | None = None,
    translated_abstract: str = "",
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    if title_rect:
        title_left, title_top, title_right, _title_bottom = title_rect
        overlay_top = title_top - TITLE_OVERLAY_GAP - TITLE_OVERLAY_HEIGHT
    else:
        overlay_top = -1
    if title_rect and overlay_top >= DOCUMENT_SAFE_TOP:
        elements.append({
            "id": "translated-title",
            "type": "text",
            "x": title_left,
            "y": overlay_top,
            "width": title_right - title_left,
            "height": TITLE_OVERLAY_HEIGHT,
            "z_index": 20,
            "text": translated_title,
            "style": {
                "color": "#111111",
                "background": TRANSPARENT_COLOR,
                "font_size": 16,
                "font_weight": "normal",
                "padding": 2,
            },
        })
    if abstract_heading_rect and translated_abstract:
        heading_left, heading_top, heading_right, heading_bottom = abstract_heading_rect
        marker_size = 30
        marker_x = heading_right + 10
        marker_y = heading_top + (heading_bottom - heading_top - marker_size) / 2
        if marker_y >= DOCUMENT_SAFE_TOP and marker_y + marker_size <= height:
            elements.append({
                "id": "abstract-translation-help",
                "type": "popover",
                "x": marker_x,
                "y": marker_y,
                "width": marker_size,
                "height": marker_size,
                "z_index": 30,
                "marker": "?",
                "title": "摘要翻译",
                "text": translated_abstract,
                "style": {
                    "color": "#FFFFFF",
                    "background": "#1677FF",
                    "stroke": "#FFFFFF",
                    "stroke_width": 2,
                    "font_size": 14,
                    "font_weight": "bold",
                    "padding": 0,
                },
                "popup": {
                    "width": 500,
                    "offset_x": 12,
                    "offset_y": -4,
                    "color": "#1F2937",
                    "background": "#FFFFFF",
                    "stroke": "#B8C0CC",
                    "font_size": 13,
                    "padding": 16,
                },
            })
    return {
        "protocol_version": 1,
        "revision": int(time.time() * 1000),
        "channel": "translation.demo",
        "producer_id": PRODUCER_ID,
        "published_at": time.time(),
        "ttl_ms": SCENE_TTL_MS,
        "target": {
            "hwnd": hwnd,
            "title_contains": "Prefab: Implementing",
            "area": "client",
            "only_when_foreground": False,
        },
        "viewport": {
            "width": width,
            "height": height,
            "coordinate_mode": "exact",
        },
        "elements": elements,
    }


def build_empty_scene(*, hwnd: int, width: int, height: int) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "revision": int(time.time() * 1000),
        "channel": "translation.demo",
        "producer_id": PRODUCER_ID,
        "published_at": time.time(),
        "ttl_ms": SCENE_TTL_MS,
        "target": {
            "hwnd": hwnd,
            "title_contains": "Prefab: Implementing",
            "area": "client",
            "only_when_foreground": False,
        },
        "viewport": {
            "width": width,
            "height": height,
            "coordinate_mode": "exact",
        },
        "elements": [],
    }


def _validated_anchor_pair(
    title_match: AnchorMatch | None,
    abstract_match: AnchorMatch | None,
    reference_regions: dict[str, Any],
) -> tuple[AnchorMatch | None, AnchorMatch | None]:
    """Reject independent matches that cannot describe one rigid document page."""
    if title_match is None and abstract_match is None:
        return None, None
    if title_match is None:
        return None, abstract_match if abstract_match and abstract_match.score >= 0.88 else None
    if abstract_match is None:
        return title_match if title_match.score >= 0.92 else None, None

    reference_title = reference_regions["title_rect"]
    reference_abstract = reference_regions["abstract_heading_rect"]
    expected_dx = (reference_abstract[0] - reference_title[0]) * (
        title_match.scale + abstract_match.scale
    ) / 2
    expected_dy = (reference_abstract[1] - reference_title[1]) * (
        title_match.scale + abstract_match.scale
    ) / 2
    actual_dx = abstract_match.rect[0] - title_match.rect[0]
    actual_dy = abstract_match.rect[1] - title_match.rect[1]
    coherent = (
        abs(title_match.scale - abstract_match.scale) <= 0.08
        and abs(actual_dx - expected_dx) <= 45
        and abs(actual_dy - expected_dy) <= 45
    )
    if coherent:
        return title_match, abstract_match
    if title_match.score >= 0.95 and abstract_match.score < 0.9:
        return title_match, None
    if abstract_match.score >= 0.93 and title_match.score < 0.92:
        return None, abstract_match
    return None, None


def watch_translation(
    *,
    target: Any,
    scene_path: Path,
    reference_frame: Any,
    reference_regions: dict[str, Any],
    translated_title: str,
    poll_seconds: float,
    stop_event_handle: int | None = None,
) -> None:
    import win32gui

    title_tracker = AdaptiveTemplateAnchorTracker(
        reference_frame,
        reference_regions["title_rect"],
        match_scale=0.25,
        local_margin_x=220,
        local_margin_y=620,
    )
    abstract_tracker = AdaptiveTemplateAnchorTracker(
        reference_frame,
        reference_regions["abstract_heading_rect"],
        match_scale=0.5,
        local_margin_x=180,
        local_margin_y=620,
    )
    frame_gate = PerceptualFrameGate(grid_size=64, minimum_distance=0.12)
    title_hint: AnchorMatch | None = None
    abstract_hint: AnchorMatch | None = None
    current_title: AnchorMatch | None = None
    current_abstract: AnchorMatch | None = None
    last_match_at = 0.0
    last_publish_at = 0.0
    active_until = 0.0
    last_width = int(reference_regions["width"])
    last_height = int(reference_regions["height"])
    status_path = codeyun_temp_root("windows-overlay-translation-demo") / "tracking-status.json"
    stats_started = time.perf_counter()
    stats_frames = 0
    stats_capture_seconds = 0.0
    stats_hash_seconds = 0.0
    stats_match_seconds = 0.0
    stats_match_frames = 0
    stats_skipped_frames = 0
    scene_write_errors = 0
    last_error: str | None = None

    def publish_scene(*, force_empty: bool = False) -> None:
        nonlocal last_publish_at, scene_write_errors, last_error
        if not force_empty and (current_title or current_abstract):
            scene = build_title_scene(
                hwnd=target.hwnd,
                width=last_width,
                height=last_height,
                title_rect=current_title.rect if current_title else None,
                translated_title=translated_title,
                abstract_heading_rect=current_abstract.rect if current_abstract else None,
                translated_abstract=FALLBACK_ABSTRACT,
            )
        else:
            scene = build_empty_scene(hwnd=target.hwnd, width=last_width, height=last_height)
        try:
            write_scene_document(scene, scene_path)
            last_publish_at = time.perf_counter()
            last_error = None
        except OSError as exc:
            scene_write_errors += 1
            last_error = f"{type(exc).__name__}: {exc}"

    def finish_cycle(
        cycle_started: float,
        capture_seconds: float,
        hash_seconds: float,
        match_seconds: float,
        *,
        matched: bool = False,
        skipped: bool = False,
    ) -> None:
        nonlocal stats_started, stats_frames, stats_capture_seconds, stats_hash_seconds
        nonlocal stats_match_seconds, stats_match_frames, stats_skipped_frames
        stats_frames += 1
        stats_capture_seconds += capture_seconds
        stats_hash_seconds += hash_seconds
        stats_match_seconds += match_seconds
        stats_match_frames += int(matched)
        stats_skipped_frames += int(skipped)
        now = time.perf_counter()
        stats_elapsed = now - stats_started
        if stats_elapsed >= 1.0:
            status = {
                "fps": round(stats_frames / stats_elapsed, 2),
                "capture_ms": round(stats_capture_seconds * 1000 / stats_frames, 2),
                "hash_ms": round(stats_hash_seconds * 1000 / stats_frames, 2),
                "match_ms": round(stats_match_seconds * 1000 / max(1, stats_match_frames), 2),
                "match_frames": stats_match_frames,
                "skipped_unchanged_frames": stats_skipped_frames,
                "skip_ratio": round(stats_skipped_frames / stats_frames, 3),
                "title": (
                    {"rect": current_title.rect, "score": round(current_title.score, 4), "scale": current_title.scale}
                    if current_title else None
                ),
                "abstract": (
                    {"rect": current_abstract.rect, "score": round(current_abstract.score, 4), "scale": current_abstract.scale}
                    if current_abstract else None
                ),
                "scene_write_errors": scene_write_errors,
                "last_error": last_error,
                "updated_at": time.time(),
            }
            try:
                status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            stats_started = now
            stats_frames = 0
            stats_capture_seconds = 0.0
            stats_hash_seconds = 0.0
            stats_match_seconds = 0.0
            stats_match_frames = 0
            stats_skipped_frames = 0
        remaining = poll_seconds - (time.perf_counter() - cycle_started)
        if remaining > 0:
            time.sleep(remaining)

    publish_scene(force_empty=True)
    while win32gui.IsWindow(target.hwnd) and not (
        stop_event_handle
        and _kernel32().WaitForSingleObject(stop_event_handle, 0) == WAIT_OBJECT_0
    ):
        cycle_started = time.perf_counter()
        capture_started = time.perf_counter()
        frame = capture_by_printwindow(target.hwnd, "client")
        capture_seconds = time.perf_counter() - capture_started
        now = time.perf_counter()
        if frame is None:
            current_title = None
            current_abstract = None
            if now - last_publish_at >= SCENE_REFRESH_SECONDS:
                publish_scene(force_empty=True)
            finish_cycle(cycle_started, capture_seconds, 0.0, 0.0)
            continue
        last_height, last_width = frame.shape[:2]
        if "Prefab: Implementing" not in win32gui.GetWindowText(target.hwnd):
            current_title = None
            current_abstract = None
            title_hint = None
            abstract_hint = None
            if now - last_publish_at >= SCENE_REFRESH_SECONDS:
                publish_scene(force_empty=True)
            finish_cycle(cycle_started, capture_seconds, 0.0, 0.0)
            continue

        hash_started = time.perf_counter()
        frame_change = frame_gate.evaluate(frame)
        hash_seconds = time.perf_counter() - hash_started
        if frame_change.changed:
            active_until = now + 0.65
        lost = current_title is None and current_abstract is None
        rematch_interval = 0.25 if lost else 0.9
        should_match = (
            frame_change.changed
            or (now < active_until and now - last_match_at >= poll_seconds * 0.8)
            or now - last_match_at >= rematch_interval
        )
        match_seconds = 0.0
        if should_match:
            match_started = time.perf_counter()
            raw_abstract = abstract_tracker.locate(frame, minimum_score=0.84, expected=abstract_hint)
            # When the augmentation is fully off-screen, the compact ABSTRACT
            # heading is the cheap page-1 sentinel. Avoid repeatedly scanning
            # the full frame with the much larger title template.
            raw_title = (
                title_tracker.locate(frame, minimum_score=0.84, expected=title_hint)
                if not lost or raw_abstract is not None
                else None
            )
            current_title, current_abstract = _validated_anchor_pair(
                raw_title,
                raw_abstract,
                reference_regions,
            )
            title_hint = current_title
            abstract_hint = current_abstract
            match_seconds = time.perf_counter() - match_started
            last_match_at = time.perf_counter()
        if should_match or now - last_publish_at >= SCENE_REFRESH_SECONDS:
            publish_scene()
        finish_cycle(
            cycle_started,
            capture_seconds,
            hash_seconds,
            match_seconds,
            matched=should_match,
            skipped=not should_match,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用当前 Prefab 英文论文验证 Windows 通用翻译图层")
    parser.add_argument("--title", default="Prefab: Implementing")
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--scene", type=Path, default=default_scene_path())
    parser.add_argument("--ocr-json", type=Path)
    parser.add_argument("--reference-image", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--stop-watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.08)
    args = parser.parse_args(argv)

    if args.hwnd:
        import win32gui

        if not win32gui.IsWindow(args.hwnd):
            raise RuntimeError(f"目标窗口不存在: hwnd={args.hwnd}")
        target = WindowCandidate(
            hwnd=args.hwnd,
            title=win32gui.GetWindowText(args.hwnd),
            class_name=win32gui.GetClassName(args.hwnd),
            rect=tuple(int(value) for value in win32gui.GetWindowRect(args.hwnd)),
        )
    else:
        target = find_window(args.title)
    if args.stop_watch:
        return 0 if stop_watch_translation(target.hwnd) else 1
    work_dir = codeyun_temp_root("windows-overlay-translation-demo")
    capture_path = args.reference_image or work_dir / "anchor-reference.png"
    ocr_path = args.ocr_json or work_dir / "anchor-ocr.json"
    import cv2

    if args.ocr_json:
        if not args.reference_image:
            raise RuntimeError("使用 --ocr-json 时必须同时提供生成该 OCR 的 --reference-image")
        ocr_payload = json.loads(args.ocr_json.read_text(encoding="utf-8"))
        reference_frame = cv2.imread(os.fspath(args.reference_image), cv2.IMREAD_UNCHANGED)
        if reference_frame is None:
            raise RuntimeError(f"锚点参考帧读取失败: {args.reference_image}")
    else:
        reference_frame = capture_by_printwindow(target.hwnd, "client")
        if reference_frame is None:
            raise RuntimeError("目标窗口捕获失败")
        cv2.imwrite(os.fspath(capture_path), reference_frame)
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        ocr_payload = run_local_paddle_ocr_preview(
            capture_path,
            options={
                "device": "cpu",
                "lang": "en",
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
        )
        ocr_path.write_text(json.dumps(ocr_payload, ensure_ascii=False), encoding="utf-8")
    regions = extract_article_regions(ocr_payload)
    if not regions["title"]:
        raise RuntimeError("参考帧中没有识别到英文论文标题")
    # This runtime demo targets the known Prefab paper. Keep its visible title
    # deterministic instead of letting a transient online encoding issue leak
    # into the overlay; generic translation belongs in a separate producer.
    translated_title = FALLBACK_TITLE
    if args.watch:
        mutex, stop_event, already_running = _create_watch_handles(target.hwnd)
        if already_running:
            print(json.dumps({"ok": True, "already_running": True, "target_hwnd": target.hwnd}))
            return 0
        try:
            watch_translation(
                target=target,
                scene_path=args.scene,
                reference_frame=reference_frame,
                reference_regions=regions,
                translated_title=translated_title,
                poll_seconds=max(0.05, args.poll_seconds),
                stop_event_handle=stop_event,
            )
        finally:
            kernel32 = _kernel32()
            if stop_event:
                kernel32.CloseHandle(stop_event)
            if mutex:
                kernel32.CloseHandle(mutex)
        return 0
    current_frame = capture_by_printwindow(target.hwnd, "client")
    if current_frame is None:
        raise RuntimeError("目标窗口捕获失败")
    tracker = TemplateAnchorTracker(reference_frame, regions["title_rect"])
    match = tracker.locate(current_frame)
    height, width = current_frame.shape[:2]
    abstract_heading_rect = None
    if match:
        reference_title = regions["title_rect"]
        dx = match.rect[0] - reference_title[0]
        dy = match.rect[1] - reference_title[1]
        heading = regions["abstract_heading_rect"]
        abstract_heading_rect = (
            heading[0] + dx,
            heading[1] + dy,
            heading[2] + dx,
            heading[3] + dy,
        )
    document = (
        build_title_scene(
            hwnd=target.hwnd,
            width=width,
            height=height,
            title_rect=match.rect,
            translated_title=translated_title,
            abstract_heading_rect=abstract_heading_rect,
            translated_abstract=FALLBACK_ABSTRACT,
        )
        if match
        else build_empty_scene(hwnd=target.hwnd, width=width, height=height)
    )
    scene = write_scene_document(document, args.scene)
    print(json.dumps({
        "ok": True,
        "target_hwnd": target.hwnd,
        "target_title": target.title,
        "scene_path": os.fspath(args.scene),
        "revision": scene["revision"],
        "ocr_title": regions["title"],
        "anchor_found": bool(match),
        "anchor_score": round(match.score, 4) if match else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
