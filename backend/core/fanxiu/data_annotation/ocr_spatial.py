from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from rapidfuzz import fuzz


MIN_TOKEN_SHAPE_OVERLAP_RATIO = 0.30
DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO = 0.75


class OcrTextMatchAmbiguousError(ValueError):
    """Raised when a click-safe OCR lookup has more than one candidate."""


@dataclass(frozen=True)
class OcrTextMatch:
    """A click-safe text match backed by the OCR tokens that produced its box."""

    target: str
    text: str
    x: float
    y: float
    w: float
    h: float
    tokens: tuple[dict[str, Any], ...]
    score: float = 100.0

    @property
    def box(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def point(
        self,
        *,
        anchor: Literal["center", "top_left", "top_center", "bottom_center"] = "center",
        offset: tuple[float, float] = (0.0, 0.0),
        offset_unit: Literal["pixel", "height"] = "pixel",
    ) -> tuple[float, float]:
        """Resolve a click point without mixing text matching with business offset."""

        anchor_x = self.x if anchor == "top_left" else self.x + self.w / 2
        anchor_y = {
            "center": self.y + self.h / 2,
            "top_left": self.y,
            "top_center": self.y,
            "bottom_center": self.y + self.h,
        }[anchor]
        scale = self.h if offset_unit == "height" else 1.0
        return (
            anchor_x + float(offset[0]) * scale,
            anchor_y + float(offset[1]) * scale,
        )


def _box(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    w = float(item.get("w") or 0)
    h = float(item.get("h") or 0)
    if w <= 0 or h <= 0:
        return None
    return x, y, x + w, y + h


def _intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _overlap_ratio(
    token_box: tuple[float, float, float, float],
    query_box: tuple[float, float, float, float],
) -> float:
    overlap = _intersection(token_box, query_box)
    if overlap is None:
        return 0.0
    token_area = max(1.0, (token_box[2] - token_box[0]) * (token_box[3] - token_box[1]))
    return ((overlap[2] - overlap[0]) * (overlap[3] - overlap[1])) / token_area


def _union_boxes(items: Iterable[dict[str, Any]]) -> dict[str, float] | None:
    boxes = [_box(item) for item in items]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return {"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)}


def _group_token_rows(tokens: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group character tokens into visual rows using geometry only."""

    candidates = [token for token in tokens if isinstance(token, dict) and _box(token) is not None]
    candidates.sort(key=lambda item: (float(item.get("y") or 0) + float(item.get("h") or 0) / 2, float(item.get("x") or 0)))
    rows: list[list[dict[str, Any]]] = []
    for token in candidates:
        center_y = float(token.get("y") or 0) + float(token.get("h") or 0) / 2
        height = max(1.0, float(token.get("h") or 0))
        best_row: list[dict[str, Any]] | None = None
        best_distance = float("inf")
        for row in rows:
            row_center_y = sum(float(item.get("y") or 0) + float(item.get("h") or 0) / 2 for item in row) / len(row)
            row_height = max(float(item.get("h") or 1) for item in row)
            distance = abs(center_y - row_center_y)
            if distance <= max(height, row_height) * 0.5 and distance < best_distance:
                best_row = row
                best_distance = distance
        if best_row is None:
            rows.append([token])
        else:
            best_row.append(token)
    for row in rows:
        row.sort(key=lambda item: float(item.get("x") or 0))
    rows.sort(
        key=lambda row: (
            min(float(item.get("y") or 0) for item in row),
            min(float(item.get("x") or 0) for item in row),
        )
    )
    return rows


def order_ocr_tokens(tokens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [token for token in tokens if isinstance(token, dict) and _box(token) is not None]
    return [token for row in _group_token_rows(candidates) for token in row]


def group_ocr_tokens(tokens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group only tokens that explicitly share a Paddle parent line.

    Unlinked legacy tokens stay separate.  This is intentionally conservative:
    geometry cannot recreate Paddle's detector grouping and must not pretend to.
    ROI callers should use :func:`query_spatial_ocr`, where local aggregation is
    an explicit business operation.
    """

    fragments: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    for fallback_index, token in enumerate(order_ocr_tokens(tokens)):
        parent_id = token.get("parent_line_id")
        key = str(parent_id) if parent_id is not None else f"legacy-token-{fallback_index}"
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(token)
    for key in group_order:
        members = groups[key]
        box = _union_boxes(members)
        if box is None:
            continue
        linked = not key.startswith("legacy-token-")
        fragments.append({
            "text": "".join(str(item.get("text") or "") for item in members),
            **box,
            "source": "linked_tokens" if linked else "unlinked_token_fallback",
            "parent_line_id": key if linked else None,
            "fallback": not linked,
        })
    return fragments


def query_ocr_lines(lines: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter authoritative Paddle lines geometrically without rebuilding them."""

    query_box = _box(query)
    if query_box is None:
        return []
    return [
        line for line in lines
        if isinstance(line, dict)
        and (line_box := _box(line)) is not None
        and _overlap_ratio(line_box, query_box) >= MIN_TOKEN_SHAPE_OVERLAP_RATIO
    ]


def query_spatial_ocr(tokens: list[dict[str, Any]], query: dict[str, Any]) -> dict[str, Any]:
    """Select tokens by shape first, then derive text fragments."""

    query_box = _box(query)
    if query_box is None:
        return {"text": "", "fragments": [], "tokens": []}
    selected = [
        token
        for token in tokens
        if isinstance(token, dict)
        and (token_box := _box(token)) is not None
        and _overlap_ratio(token_box, query_box) >= MIN_TOKEN_SHAPE_OVERLAP_RATIO
    ]
    ordered = order_ocr_tokens(selected)
    if ordered and all(token.get("parent_line_id") is not None for token in ordered):
        fragments = group_ocr_tokens(ordered)
    else:
        # This aggregation is deliberately confined to an explicit local ROI.
        fragments = []
        for row in _group_token_rows(ordered):
            box = _union_boxes(row)
            if box is not None:
                fragments.append({
                    "text": "".join(str(item.get("text") or "") for item in row),
                    **box,
                    "source": "roi_tokens_unlinked",
                    "fallback": True,
                })
    return {
        "text": "".join(str(fragment.get("text") or "") for fragment in fragments),
        "fragments": fragments,
        "tokens": ordered,
    }


def _token_streams(tokens: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Build visual reading streams without treating a Paddle line as one word."""

    candidates = [token for token in tokens if isinstance(token, dict) and _box(token) is not None]
    linked: dict[str, list[dict[str, Any]]] = {}
    unlinked: list[dict[str, Any]] = []
    for token in candidates:
        parent_id = token.get("parent_line_id")
        if parent_id is None:
            unlinked.append(token)
        else:
            linked.setdefault(str(parent_id), []).append(token)

    streams = [
        sorted(
            group,
            key=lambda item: (
                float(item.get("x") or 0),
                int(item.get("order") or 0),
            ),
        )
        for group in linked.values()
    ]
    streams.extend(_group_token_rows(unlinked))
    streams.sort(
        key=lambda group: (
            min(float(item.get("y") or 0) for item in group),
            min(float(item.get("x") or 0) for item in group),
        )
    )
    return streams


def _split_stream_by_geometry(
    stream: list[dict[str, Any]],
    *,
    max_gap_height_ratio: float,
) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    for token in stream:
        token_box = _box(token)
        if token_box is None:
            continue
        if not segments:
            segments.append([token])
            continue
        previous = segments[-1][-1]
        previous_box = _box(previous)
        if previous_box is None:
            segments.append([token])
            continue
        previous_height = previous_box[3] - previous_box[1]
        token_height = token_box[3] - token_box[1]
        vertical_overlap = max(
            0.0,
            min(previous_box[3], token_box[3]) - max(previous_box[1], token_box[1]),
        )
        aligned = vertical_overlap / max(1.0, min(previous_height, token_height)) >= 0.5
        gap = token_box[0] - previous_box[2]
        adjacent = (
            token_box[0] >= previous_box[0]
            and gap <= max(previous_height, token_height) * max_gap_height_ratio
        )
        if not aligned or not adjacent:
            segments.append([token])
        else:
            segments[-1].append(token)
    return segments


def segment_ocr_tokens(
    tokens: Iterable[dict[str, Any]],
    *,
    max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
) -> list[list[dict[str, Any]]]:
    """Split OCR tokens into contiguous visual text groups.

    The result is geometric rather than linguistic: Paddle line identity keeps
    rows separate, while horizontal gaps split nearby controls into reusable
    token groups.
    """

    ratio = max(0.0, float(max_gap_height_ratio))
    return [
        segment
        for stream in _token_streams(tokens)
        for segment in _split_stream_by_geometry(
            stream,
            max_gap_height_ratio=ratio,
        )
    ]


def find_text_matches(
    tokens: Iterable[dict[str, Any]],
    target: str,
    *,
    max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
) -> list[OcrTextMatch]:
    """Find every exact, click-safe target occurrence from real OCR token boxes.

    A match may span consecutive tokens, but never crosses a Paddle line or a
    geometric gap. Partial use of a multi-character token is rejected because
    its character box would otherwise have to be guessed.
    """

    normalized_target = str(target or "")
    if not normalized_target:
        return []
    matches: list[OcrTextMatch] = []
    for segment in segment_ocr_tokens(
        tokens,
        max_gap_height_ratio=max_gap_height_ratio,
    ):
        segment_text = "".join(str(token.get("text") or "") for token in segment)
        token_ranges: list[tuple[int, int, dict[str, Any]]] = []
        cursor = 0
        for token in segment:
            token_text = str(token.get("text") or "")
            token_ranges.append((cursor, cursor + len(token_text), token))
            cursor += len(token_text)
        search_from = 0
        while (start := segment_text.find(normalized_target, search_from)) >= 0:
            end = start + len(normalized_target)
            selected = [
                token
                for token_start, token_end, token in token_ranges
                if token_end > start and token_start < end
            ]
            boundaries = {
                boundary
                for token_start, token_end, _token in token_ranges
                for boundary in (token_start, token_end)
            }
            if start in boundaries and end in boundaries:
                box = _union_boxes(selected)
                if box is not None:
                    matches.append(
                        OcrTextMatch(
                            target=normalized_target,
                            text=segment_text[start:end],
                            tokens=tuple(selected),
                            **box,
                        )
                    )
            search_from = start + 1
    return matches


def _normalize_fuzzy_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(character for character in normalized if character.isalnum())


def find_fuzzy_text_matches(
    tokens: Iterable[dict[str, Any]],
    target: str,
    *,
    min_score: float = 70.0,
    min_length_ratio: float = 0.60,
    max_length_ratio: float = 1.50,
    max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
) -> list[OcrTextMatch]:
    """Find bounded fuzzy candidates while retaining their real token boxes.

    Candidate windows never cross a Paddle line or a geometric gap.  Only
    whole OCR tokens are used, so a fuzzy result never invents a character box
    inside a multi-character token.  Per visual segment only the best-scoring
    window(s) survive; this prevents overlapping near-matches around one label
    from being mistaken for several independent controls.
    """

    normalized_target = _normalize_fuzzy_text(target)
    if not normalized_target:
        return []
    target_length = len(normalized_target)
    minimum_length = max(1, math.ceil(target_length * max(0.0, float(min_length_ratio))))
    maximum_length = max(
        minimum_length,
        math.ceil(target_length * max(float(min_length_ratio), float(max_length_ratio))),
    )
    threshold = max(0.0, min(100.0, float(min_score)))
    matches: list[OcrTextMatch] = []
    for segment in segment_ocr_tokens(
        tokens,
        max_gap_height_ratio=max_gap_height_ratio,
    ):
        segment_matches: list[OcrTextMatch] = []
        for start in range(len(segment)):
            selected: list[dict[str, Any]] = []
            for end in range(start, len(segment)):
                selected.append(segment[end])
                candidate_text = "".join(str(token.get("text") or "") for token in selected)
                normalized_candidate = _normalize_fuzzy_text(candidate_text)
                candidate_length = len(normalized_candidate)
                if candidate_length > maximum_length:
                    break
                if candidate_length < minimum_length:
                    continue
                score = float(fuzz.ratio(normalized_candidate, normalized_target))
                if score < threshold:
                    continue
                box = _union_boxes(selected)
                if box is None:
                    continue
                segment_matches.append(
                    OcrTextMatch(
                        target=str(target or ""),
                        text=candidate_text,
                        tokens=tuple(selected),
                        score=score,
                        **box,
                    )
                )
        if segment_matches:
            best_score = max(match.score for match in segment_matches)
            matches.extend(
                match for match in segment_matches
                if math.isclose(match.score, best_score, abs_tol=1e-6)
            )
    return matches


def select_text_match(
    matches: Iterable[OcrTextMatch],
    target: str,
    *,
    occurrence: int | None = None,
) -> OcrTextMatch | None:
    """Select one match explicitly; ambiguity is an error, not a first-hit rule."""

    candidates = list(matches)
    if occurrence is not None:
        index = int(occurrence)
        if index < 0 or index >= len(candidates):
            return None
        return candidates[index]
    if len(candidates) > 1:
        boxes = ", ".join(
            f"({match.x:.0f},{match.y:.0f},{match.w:.0f},{match.h:.0f})"
            for match in candidates
        )
        raise OcrTextMatchAmbiguousError(
            f"OCR 文本「{target}」存在 {len(candidates)} 个命中：{boxes}；"
            "请传 occurrence 或先缩小 shape/ROI"
        )
    return candidates[0] if candidates else None


def select_fuzzy_text_match(
    matches: Iterable[OcrTextMatch],
    target: str,
    *,
    occurrence: int | None = None,
    ambiguity_margin: float = 5.0,
) -> OcrTextMatch | None:
    """Select the best fuzzy candidate and reject materially tied controls."""

    candidates = list(matches)
    if occurrence is not None:
        index = int(occurrence)
        if index < 0 or index >= len(candidates):
            return None
        return candidates[index]
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda match: match.score, reverse=True)
    best = ranked[0]
    tied = [
        match for match in ranked[1:]
        if best.score - match.score <= max(0.0, float(ambiguity_margin))
    ]
    if tied:
        details = ", ".join(
            f"{match.text!r}@({match.x:.0f},{match.y:.0f})={match.score:.1f}"
            for match in [best, *tied]
        )
        raise OcrTextMatchAmbiguousError(
            f"OCR 文本「{target}」存在多个近似候选：{details}；"
            "请缩小 shape/ROI、提高阈值或传 occurrence"
        )
    return best


def locate_text_box(tokens: list[dict[str, Any]], target: str) -> dict[str, float] | None:
    """Locate a substring from ordered OCR tokens using real token boxes."""

    target = str(target or "")
    ordered = order_ocr_tokens(tokens)
    if not target:
        return None
    groups: list[list[dict[str, Any]]] = []
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for token in ordered:
        parent_id = token.get("parent_line_id")
        if parent_id is None:
            if not groups or groups[-1] is not by_parent.get("__legacy__"):
                legacy = by_parent.setdefault("__legacy__", [])
                groups.append(legacy)
            by_parent["__legacy__"].append(token)
        else:
            key = str(parent_id)
            if key not in by_parent:
                by_parent[key] = []
                groups.append(by_parent[key])
            by_parent[key].append(token)
    for group in groups:
        result = _locate_text_box_in_ordered_tokens(group, target)
        if result is not None:
            return result
    return None


def _locate_text_box_in_ordered_tokens(
    ordered: list[dict[str, Any]], target: str,
) -> dict[str, float] | None:
    text = "".join(str(token.get("text") or "") for token in ordered)
    start = text.find(target)
    if start < 0:
        return None
    end = start + len(target)
    cursor = 0
    pieces: list[dict[str, float]] = []
    for token in ordered:
        token_text = str(token.get("text") or "")
        token_start, token_end = cursor, cursor + len(token_text)
        cursor = token_end
        overlap_start = max(start, token_start)
        overlap_end = min(end, token_end)
        if overlap_end <= overlap_start:
            continue
        token_box = _box(token)
        if token_box is None:
            continue
        left, top, right, bottom = token_box
        if overlap_start > token_start or overlap_end < token_end:
            width = max(1.0, right - left)
            length = max(1, len(token_text))
            left, right = (
                left + width * ((overlap_start - token_start) / length),
                left + width * ((overlap_end - token_start) / length),
            )
        pieces.append({"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)})
    return _union_boxes(pieces)


def union_fragment_box(fragments: list[dict[str, Any]]) -> dict[str, float] | None:
    box = _union_boxes(fragment for fragment in fragments if isinstance(fragment, dict))
    if box is None or not all(math.isfinite(value) for value in box.values()):
        return None
    return box
