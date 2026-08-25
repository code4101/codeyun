from __future__ import annotations

import cv2
import numpy as np

from backend.core.windows_overlay.anchor_tracking import (
    AdaptiveTemplateAnchorTracker,
    PerceptualFrameGate,
    TemplateAnchorTracker,
)
from scripts.windows_overlay_translation_demo import build_title_scene


def _frame(*, anchor_x: int, anchor_y: int) -> np.ndarray:
    frame = np.full((420, 640, 3), 245, dtype=np.uint8)
    cv2.rectangle(frame, (anchor_x, anchor_y), (anchor_x + 300, anchor_y + 54), (20, 20, 20), 3)
    cv2.putText(
        frame,
        "PIXEL-BASED INTERFACE",
        (anchor_x + 10, anchor_y + 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (10, 10, 10),
        2,
        cv2.LINE_AA,
    )
    return frame


def test_template_anchor_follows_vertical_scroll() -> None:
    reference = _frame(anchor_x=120, anchor_y=240)
    tracker = TemplateAnchorTracker(reference, (120, 240, 421, 295))

    match = tracker.locate(_frame(anchor_x=120, anchor_y=92), minimum_score=0.8)

    assert match is not None
    assert match.rect == (120, 92, 421, 147)
    assert match.score > 0.95


def test_template_anchor_hides_when_target_is_absent() -> None:
    reference = _frame(anchor_x=120, anchor_y=240)
    tracker = TemplateAnchorTracker(reference, (120, 240, 421, 295))
    blank = np.full_like(reference, 245)

    assert tracker.locate(blank, minimum_score=0.8) is None


def test_adaptive_template_anchor_reacquires_after_zoom_and_scroll() -> None:
    reference = _frame(anchor_x=120, anchor_y=240)
    source = reference[240:295, 120:421]
    current = np.full_like(reference, 245)
    scaled = cv2.resize(source, (241, 44), interpolation=cv2.INTER_AREA)
    current[82:126, 210:451] = scaled
    tracker = AdaptiveTemplateAnchorTracker(
        reference,
        (120, 240, 421, 295),
        content_scales=(0.75, 0.8, 0.85, 1.0),
        match_scale=0.5,
    )

    match = tracker.locate(current, minimum_score=0.8)

    assert match is not None
    assert match.rect == (210, 82, 451, 126)
    assert match.scale == 0.8
    assert match.score > 0.95


def test_adaptive_template_anchor_does_not_reuse_stale_position() -> None:
    reference = _frame(anchor_x=120, anchor_y=240)
    tracker = AdaptiveTemplateAnchorTracker(reference, (120, 240, 421, 295), match_scale=0.5)
    previous = tracker.locate(reference, minimum_score=0.8)
    blank = np.full_like(reference, 245)

    assert previous is not None
    assert tracker.locate(blank, minimum_score=0.8, expected=previous) is None


def test_perceptual_frame_gate_skips_unchanged_frame_and_detects_scroll() -> None:
    reference = _frame(anchor_x=120, anchor_y=240)
    gate = PerceptualFrameGate(minimum_distance=0.2)

    assert gate.evaluate(reference).changed is True
    assert gate.evaluate(reference.copy()).changed is False
    assert gate.evaluate(_frame(anchor_x=120, anchor_y=205)).changed is True


def test_translated_title_is_transparent_and_relative_to_anchor() -> None:
    scene = build_title_scene(
        hwnd=123,
        width=640,
        height=420,
        title_rect=(120, 240, 421, 295),
        translated_title="中文标题",
    )

    title = scene["elements"][0]
    assert title["x"] == 120
    assert title["y"] == 190
    assert title["style"]["background"] == "#010203"
    assert title["style"]["font_weight"] == "normal"
