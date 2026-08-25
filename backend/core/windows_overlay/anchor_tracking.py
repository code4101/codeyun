from __future__ import annotations

"""Lightweight template anchors for window-relative overlay placement."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnchorMatch:
    rect: tuple[int, int, int, int]
    score: float
    scale: float = 1.0


@dataclass(frozen=True)
class FrameChange:
    changed: bool
    distance: float


class PerceptualFrameGate:
    """Cheap sampled-luma fingerprint used before template matching."""

    def __init__(self, *, grid_size: int = 64, minimum_distance: float = 0.45) -> None:
        self.grid_size = max(16, int(grid_size))
        self.minimum_distance = max(0.0, float(minimum_distance))
        self.last_signature: Any | None = None

    def evaluate(self, frame: Any) -> FrameChange:
        import cv2
        import numpy as np

        height, width = frame.shape[:2]
        ys = np.linspace(0, height - 1, self.grid_size, dtype=np.int32)
        xs = np.linspace(0, width - 1, self.grid_size, dtype=np.int32)
        sample = frame[ys[:, None], xs[None, :]]
        if len(sample.shape) == 3:
            conversion = cv2.COLOR_BGRA2GRAY if sample.shape[2] == 4 else cv2.COLOR_BGR2GRAY
            signature = cv2.cvtColor(sample, conversion)
        else:
            signature = sample
        if self.last_signature is None or self.last_signature.shape != signature.shape:
            self.last_signature = signature.copy()
            return FrameChange(changed=True, distance=float("inf"))
        distance = float(np.mean(np.abs(signature.astype(np.int16) - self.last_signature.astype(np.int16))))
        changed = distance >= self.minimum_distance
        if changed:
            self.last_signature = signature.copy()
        return FrameChange(changed=changed, distance=distance)


class TemplateAnchorTracker:
    def __init__(
        self,
        reference_frame: Any,
        reference_rect: tuple[int, int, int, int],
        *,
        horizontal_margin: int = 120,
        match_scale: float = 0.5,
    ) -> None:
        import cv2

        left, top, right, bottom = reference_rect
        if right <= left or bottom <= top:
            raise ValueError("anchor reference_rect must have positive size")
        self.reference_rect = tuple(int(value) for value in reference_rect)
        self.horizontal_margin = max(0, int(horizontal_margin))
        self.match_scale = min(1.0, max(0.1, float(match_scale)))
        gray = self._gray(reference_frame)
        self.template = gray[top:bottom, left:right].copy()
        if self.template.size == 0:
            raise ValueError("anchor template is outside the reference frame")
        self.scaled_template = cv2.resize(
            self.template,
            None,
            fx=self.match_scale,
            fy=self.match_scale,
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _gray(frame: Any) -> Any:
        import cv2

        if len(frame.shape) == 2:
            return frame
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def locate(self, frame: Any, *, minimum_score: float = 0.72) -> AnchorMatch | None:
        import cv2

        gray = self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left, _top, right, _bottom = self.reference_rect
        search_left = max(0, left - self.horizontal_margin)
        search_right = min(frame_width, right + self.horizontal_margin)
        search = gray[:, search_left:search_right]
        scaled_search = cv2.resize(
            search,
            None,
            fx=self.match_scale,
            fy=self.match_scale,
            interpolation=cv2.INTER_AREA,
        )
        template_height, template_width = self.scaled_template.shape[:2]
        if scaled_search.shape[0] < template_height or scaled_search.shape[1] < template_width:
            return None
        scores = cv2.matchTemplate(scaled_search, self.scaled_template, cv2.TM_CCOEFF_NORMED)
        _minimum, score, _minimum_location, location = cv2.minMaxLoc(scores)
        if float(score) < float(minimum_score):
            return None
        x = search_left + round(location[0] / self.match_scale)
        y = round(location[1] / self.match_scale)
        width = right - left
        height = self.reference_rect[3] - self.reference_rect[1]
        return AnchorMatch(rect=(x, y, x + width, y + height), score=float(score))


class AdaptiveTemplateAnchorTracker:
    """Template anchor with cheap local tracking and multi-scale reacquisition.

    ``content_scale`` describes changes inside the target window, such as PDF
    zoom. ``match_scale`` only reduces matching cost and never changes output
    coordinates.
    """

    def __init__(
        self,
        reference_frame: Any,
        reference_rect: tuple[int, int, int, int],
        *,
        content_scales: tuple[float, ...] | None = None,
        match_scale: float = 0.3,
        local_margin_x: int = 180,
        local_margin_y: int = 520,
    ) -> None:
        import numpy as np

        left, top, right, bottom = (int(value) for value in reference_rect)
        if right <= left or bottom <= top:
            raise ValueError("anchor reference_rect must have positive size")
        gray = TemplateAnchorTracker._gray(reference_frame)
        self.template = gray[top:bottom, left:right].copy()
        if self.template.size == 0:
            raise ValueError("anchor template is outside the reference frame")
        if float(np.std(self.template)) < 2.0:
            raise ValueError("anchor template does not contain enough visual detail")
        self.reference_rect = (left, top, right, bottom)
        self.match_scale = min(1.0, max(0.1, float(match_scale)))
        self.local_margin_x = max(20, int(local_margin_x))
        self.local_margin_y = max(40, int(local_margin_y))
        scales = content_scales or tuple(round(0.65 + index * 0.05, 2) for index in range(13))
        self.content_scales = tuple(sorted({min(1.5, max(0.5, float(scale))) for scale in scales}))

    def locate(
        self,
        frame: Any,
        *,
        minimum_score: float = 0.82,
        expected: AnchorMatch | None = None,
    ) -> AnchorMatch | None:
        if expected is not None:
            local_scales = tuple(
                sorted({
                    min(1.5, max(0.5, expected.scale * factor))
                    for factor in (0.96, 0.98, 1.0, 1.02, 1.04)
                })
            )
            left, top, right, bottom = expected.rect
            local = self._match(
                frame,
                scales=local_scales,
                search_rect=(
                    left - self.local_margin_x,
                    top - self.local_margin_y,
                    right + self.local_margin_x,
                    bottom + self.local_margin_y,
                ),
            )
            if local is not None and local.score >= minimum_score:
                return local
        acquired = self._match(frame, scales=self.content_scales, search_rect=None)
        if acquired is None or acquired.score < minimum_score:
            return None
        return acquired

    def _match(
        self,
        frame: Any,
        *,
        scales: tuple[float, ...],
        search_rect: tuple[int, int, int, int] | None,
    ) -> AnchorMatch | None:
        import cv2

        gray = TemplateAnchorTracker._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        if search_rect is None:
            search_left, search_top, search_right, search_bottom = 0, 0, frame_width, frame_height
        else:
            search_left = max(0, int(search_rect[0]))
            search_top = max(0, int(search_rect[1]))
            search_right = min(frame_width, int(search_rect[2]))
            search_bottom = min(frame_height, int(search_rect[3]))
        if search_right <= search_left or search_bottom <= search_top:
            return None
        search = gray[search_top:search_bottom, search_left:search_right]
        reduced_search = cv2.resize(
            search,
            None,
            fx=self.match_scale,
            fy=self.match_scale,
            interpolation=cv2.INTER_AREA,
        )
        best: AnchorMatch | None = None
        reference_width = self.reference_rect[2] - self.reference_rect[0]
        reference_height = self.reference_rect[3] - self.reference_rect[1]
        for content_scale in scales:
            output_width = max(1, round(reference_width * content_scale))
            output_height = max(1, round(reference_height * content_scale))
            template = cv2.resize(
                self.template,
                (
                    max(1, round(output_width * self.match_scale)),
                    max(1, round(output_height * self.match_scale)),
                ),
                interpolation=cv2.INTER_AREA if content_scale <= 1 else cv2.INTER_CUBIC,
            )
            template_height, template_width = template.shape[:2]
            if (
                reduced_search.shape[0] < template_height
                or reduced_search.shape[1] < template_width
            ):
                continue
            scores = cv2.matchTemplate(reduced_search, template, cv2.TM_CCOEFF_NORMED)
            _minimum, score, _minimum_location, location = cv2.minMaxLoc(scores)
            x = search_left + round(location[0] / self.match_scale)
            y = search_top + round(location[1] / self.match_scale)
            candidate = AnchorMatch(
                rect=(x, y, x + output_width, y + output_height),
                score=float(score),
                scale=float(content_scale),
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best
