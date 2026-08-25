from __future__ import annotations

"""Align the #121 mail window with the ordered read-only MailMgr model.

OCR observations are deliberately treated as noisy anchors.  A successful
result means that one global sequence offset is sufficiently better supported
than every competing offset; it does not mean that every visible row was read
exactly.  The first visible row is untrusted by default because world notices
regularly cover it.
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence

from backend.core.fanxiu.runtime_gui import ocr_name_similarity


@dataclass(frozen=True)
class MailWindowGeometry:
    """Stable #121 row lattice derived from the annotated first two rows."""

    frame_width: int
    frame_height: int
    first_center_y: float
    second_center_y: float
    row_pitch: float
    row_half_height: float = 0.0
    title_center_offset: float = 0.0
    time_center_offset: float = 0.0
    list_top: float = 0.0
    list_bottom: float = 0.0

    def row_center_y(self, slot_index: int) -> float:
        return self.first_center_y + int(slot_index) * self.row_pitch

    def slot_for_y(self, y: float, *, channel: str = "center") -> int:
        offset = 0.0
        if channel == "title":
            offset = self.title_center_offset
        elif channel == "time":
            offset = self.time_center_offset
        return int(round((float(y) - self.first_center_y - offset) / self.row_pitch))

    def visible_slot_indices(self) -> tuple[int, ...]:
        """Return the annotated first row plus rows fully inside the list viewport.

        A center can still fall inside ``邮件清单2`` while the lower half of the
        row is covered by the footer.  Such a row is useful as sequence context
        only after another scroll; it is not a safe click target.
        """

        slots = [0]
        if self.list_bottom <= self.list_top:
            return tuple(slots)
        edge_tolerance = min(12.0, self.row_half_height * 0.2)
        slot = 1
        while self.row_center_y(slot) - self.row_half_height <= self.list_bottom:
            center_y = self.row_center_y(slot)
            if (
                center_y - self.row_half_height >= self.list_top - edge_tolerance
                and center_y + self.row_half_height <= self.list_bottom + edge_tolerance
            ):
                slots.append(slot)
            slot += 1
        return tuple(slots)


@dataclass(frozen=True)
class MailVisualObservation:
    """Noisy OCR evidence assigned to one fixed visual row slot."""

    slot_index: int
    center_y: float
    title_candidates: tuple[str, ...] = ()
    time_candidates: tuple[str, ...] = ()
    trusted: bool = True
    reliability: float = 1.0
    raw_fragments: tuple[str, ...] = ()


@dataclass(frozen=True)
class MailAlignmentHypothesis:
    runtime_offset: int
    score: float
    anchor_count: int
    exact_anchor_count: int
    matched_slots: tuple[int, ...]
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MailWindowAlignment:
    status: str
    reason: str
    snapshot_fingerprint: str
    runtime_offset: int | None = None
    best_score: float = 0.0
    second_score: float = 0.0
    score_margin: float = 0.0
    anchor_count: int = 0
    exact_anchor_count: int = 0
    mappings: tuple[dict[str, Any], ...] = ()
    hypotheses: tuple[MailAlignmentHypothesis, ...] = ()
    competitive_offsets: tuple[int, ...] = ()
    stale_evidence: tuple[dict[str, Any], ...] = ()

    @property
    def aligned(self) -> bool:
        return self.status == "aligned"

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["aligned"] = self.aligned
        return payload


def _iter_shapes(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if value.get("kind") == "shape" or "x" in value:
            yield value
        for key in ("shapes", "children"):
            yield from _iter_shapes(value.get(key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_shapes(item)


def _shape_by_title(image: Mapping[str, Any], title: str) -> Mapping[str, Any] | None:
    return next(
        (
            shape
            for shape in _iter_shapes(image.get("shapes") or ())
            if str(shape.get("title") or "").strip() == title
        ),
        None,
    )


def _shape_box(
    shape: Mapping[str, Any], width: int, height: int
) -> tuple[float, float, float, float]:
    return (
        float(shape.get("x") or 0) * width,
        float(shape.get("y") or 0) * height,
        float(shape.get("w") or 0) * width,
        float(shape.get("h") or 0) * height,
    )


def mail_window_geometry_from_asset(image: Mapping[str, Any]) -> MailWindowGeometry:
    """Read the #121 vertical lattice without modifying its annotation."""

    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    first = _shape_by_title(image, "第1封")
    second = _shape_by_title(image, "第2封")
    template = _shape_by_title(image, "邮件模板")
    title = _shape_by_title(image, "标题")
    time_shape = _shape_by_title(image, "时间")
    list_shape = _shape_by_title(image, "邮件清单2")
    if width <= 0 or height <= 0 or first is None or second is None:
        raise ValueError("#121 必须提供尺寸以及「第1封」「第2封」标注")

    first_box = _shape_box(first, width, height)
    second_box = _shape_box(second, width, height)
    first_center = first_box[1] + first_box[3] / 2
    second_center = second_box[1] + second_box[3] / 2
    pitch = second_center - first_center
    if pitch <= 24:
        raise ValueError(f"#121 邮件行距无效：{pitch:.2f}px")

    title_offset = time_offset = 0.0
    list_top = list_bottom = 0.0
    if template is not None:
        template_box = _shape_box(template, width, height)
        template_center = template_box[1] + template_box[3] / 2
        if title is not None:
            title_box = _shape_box(title, width, height)
            title_offset = title_box[1] + title_box[3] / 2 - template_center
        if time_shape is not None:
            time_box = _shape_box(time_shape, width, height)
            time_offset = time_box[1] + time_box[3] / 2 - template_center
    if list_shape is not None:
        list_box = _shape_box(list_shape, width, height)
        list_top = list_box[1]
        list_bottom = list_box[1] + list_box[3]

    return MailWindowGeometry(
        frame_width=width,
        frame_height=height,
        first_center_y=first_center,
        second_center_y=second_center,
        row_pitch=pitch,
        row_half_height=first_box[3] / 2,
        title_center_offset=title_offset,
        time_center_offset=time_offset,
        list_top=list_top,
        list_bottom=list_bottom,
    )


def stable_complete_mail_snapshots(*snapshots: Mapping[str, Any]) -> dict[str, Any]:
    """Require consecutive complete MailMgr reads with the same ordered facts."""

    if len(snapshots) < 2:
        return {"stable": False, "reason": "至少需要两次动态邮件快照"}
    fingerprints: list[str] = []
    for index, snapshot in enumerate(snapshots):
        if not bool(snapshot.get("complete")):
            return {"stable": False, "reason": f"第 {index + 1} 次动态邮件快照不完整"}
        items = snapshot.get("items")
        decoded_count = snapshot.get("decoded_count")
        if (
            not isinstance(items, list)
            or decoded_count is None
            or int(decoded_count) != len(items)
        ):
            return {
                "stable": False,
                "reason": f"第 {index + 1} 次动态邮件快照计数不一致",
            }
        fingerprints.append(
            str(snapshot.get("sequence_fingerprint") or "")
            or mail_snapshot_fingerprint(items)
        )
    stable = len(set(fingerprints)) == 1
    return {
        "stable": stable,
        "reason": (
            "动态邮件序列稳定"
            if stable
            else "连续动态邮件快照发生变化，必须重新观察 #121"
        ),
        "fingerprints": fingerprints,
        "fingerprint": fingerprints[-1],
    }


def _clean_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())


def _time_digits(value: Any) -> str:
    return "".join(re.findall(r"\d", str(value or "")))


def _runtime_time_key(item: Mapping[str, Any]) -> str:
    raw = item.get("create_time")
    if raw is None:
        raw = item.get("create_time_ms")
    try:
        number = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        number = None
    if number is not None:
        seconds = number / 1000 if abs(number) >= 100_000_000_000 else number
        try:
            return datetime.fromtimestamp(seconds).strftime("%Y%m%d%H%M")
        except (OSError, OverflowError, ValueError):
            pass
    digits = _time_digits(item.get("create_time_text"))
    return digits[:12] if len(digits) >= 12 else digits


def _ordered_runtime_items(items: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            rows.append(dict(item))
            continue
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                rows.append(dict(dumped))
    indexed = [item for item in rows if isinstance(item.get("runtime_index"), int)]
    if len(indexed) == len(rows) and len(
        {int(item["runtime_index"]) for item in rows}
    ) == len(rows):
        rows.sort(key=lambda item: int(item["runtime_index"]))
    for index, item in enumerate(rows):
        item.setdefault("runtime_index", index)
    return rows


def mail_snapshot_fingerprint(items: Iterable[Mapping[str, Any]]) -> str:
    """Return an order- and action-sensitive digest for one complete snapshot."""

    rows = _ordered_runtime_items(items)
    facts = [
        {
            "runtime_index": int(item.get("runtime_index") or 0),
            "id": str(item.get("id") or item.get("mail_id") or ""),
            "title": str(item.get("title") or ""),
            "create_time": (
                item.get("create_time")
                if item.get("create_time") is not None
                else item.get("create_time_ms")
            ),
            "locked": item.get("locked"),
            "reward_getted": item.get("reward_getted"),
        }
        for item in rows
    ]
    raw = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mail_snapshot_structure_fingerprint(items: Iterable[Mapping[str, Any]]) -> str:
    """Return the ordered mailbox structure without mutable claim state.

    ``reward_getted`` is intentionally excluded: a successful claim changes that
    field but does not move the visible row.  This digest is used between a
    bounded group of visually verified clicks to detect arrivals, removals,
    reordering, title changes, and lock changes without treating our own claim as
    a stale-list event.
    """

    rows = _ordered_runtime_items(items)
    facts = [
        {
            "runtime_index": int(item.get("runtime_index") or 0),
            "id": str(item.get("id") or item.get("mail_id") or ""),
            "title": str(item.get("title") or ""),
            "create_time": (
                item.get("create_time")
                if item.get("create_time") is not None
                else item.get("create_time_ms")
            ),
            "locked": item.get("locked"),
        }
        for item in rows
    ]
    raw = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_mail_visual_observations(
    fragments: Iterable[Mapping[str, Any]],
    geometry: MailWindowGeometry,
    *,
    visible_slots: Iterable[int] | None = None,
) -> list[MailVisualObservation]:
    """Bucket raw OCR fragments onto the fixed title/time row lattice."""

    allowed = (
        set(int(slot) for slot in visible_slots) if visible_slots is not None else None
    )
    buckets: dict[int, dict[str, list[str]]] = {}
    centers: dict[int, list[float]] = {}
    for fragment in fragments:
        text = str(fragment.get("text") or "").strip()
        if not text:
            continue
        y = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
        digits = _time_digits(text)
        is_time = len(digits) >= 4 and any(
            marker in text for marker in ("年", "月", "日", ":", "：")
        )
        channel = "time" if is_time else "title"
        slot = geometry.slot_for_y(y, channel=channel)
        if allowed is not None and slot not in allowed:
            continue
        bucket = buckets.setdefault(slot, {"title": [], "time": [], "raw": []})
        bucket[channel].append(text)
        bucket["raw"].append(text)
        centers.setdefault(slot, []).append(y)

    result: list[MailVisualObservation] = []
    for slot in sorted(buckets):
        bucket = buckets[slot]
        result.append(
            MailVisualObservation(
                slot_index=slot,
                center_y=sum(centers[slot]) / len(centers[slot]),
                title_candidates=tuple(dict.fromkeys(bucket["title"])),
                time_candidates=tuple(dict.fromkeys(bucket["time"])),
                trusted=slot != 0,
                reliability=0.2 if slot == 0 else 1.0,
                raw_fragments=tuple(bucket["raw"]),
            )
        )
    return result


def _best_similarity(
    candidates: Iterable[str], expected: str, *, time_value: bool = False
) -> float:
    if not time_value:
        # Runtime-GUI text matching owns noisy title identity.  MailMgr remains
        # authoritative; OCR only anchors the visible row lattice.
        return max(
            (ocr_name_similarity(expected, candidate) for candidate in candidates),
            default=0.0,
        )
    normalized_expected = (
        _time_digits(expected) if time_value else _clean_text(expected)
    )
    if not normalized_expected:
        return 0.0
    best = 0.0
    for candidate in candidates:
        normalized = _time_digits(candidate) if time_value else _clean_text(candidate)
        if not normalized:
            continue
        ratio = SequenceMatcher(None, normalized, normalized_expected).ratio()
        if normalized in normalized_expected or normalized_expected in normalized:
            ratio = max(
                ratio,
                min(len(normalized), len(normalized_expected))
                / max(len(normalized), len(normalized_expected)),
            )
        if (
            time_value
            and len(normalized) >= 4
            and normalized[-4:] == normalized_expected[-4:]
        ):
            ratio = max(ratio, 0.72)
        best = max(best, ratio)
    return best


def _observation_evidence(
    observation: MailVisualObservation,
    item: Mapping[str, Any],
    *,
    runtime_title_is_unique: bool,
) -> dict[str, Any]:
    title_score = _best_similarity(
        observation.title_candidates, str(item.get("title") or "")
    )
    time_score = _best_similarity(
        observation.time_candidates, _runtime_time_key(item), time_value=True
    )
    expected_time_digits = _time_digits(_runtime_time_key(item))
    time_exact = any(
        bool(candidate_digits)
        and (
            candidate_digits == expected_time_digits
            or (
                len(candidate_digits) >= 4
                and len(expected_time_digits) >= 4
                and candidate_digits[-4:] == expected_time_digits[-4:]
            )
        )
        for candidate in observation.time_candidates
        if (candidate_digits := _time_digits(candidate))
    )
    if observation.title_candidates and observation.time_candidates:
        combined = 0.68 * title_score + 0.32 * time_score
    elif observation.title_candidates:
        combined = 0.82 * title_score
    elif observation.time_candidates:
        combined = 0.68 * time_score
    else:
        combined = 0.0
    combined *= max(0.0, min(1.0, float(observation.reliability)))
    anchor = (
        observation.trusted
        and combined >= 0.62
        and (title_score >= 0.68 or time_score >= 0.92)
    )
    # A title-only OCR hit is an identity anchor only when that Runtime title
    # is unique.  Repeated rows such as several adjacent ``香车馈赠`` mails
    # must use their timestamp as the second channel; otherwise every nearby
    # offset looks "exact" and a controlled-scroll prediction can select the
    # wrong internal mail.
    exact = bool(
        observation.trusted
        and title_score >= 0.94
        and (
            (
                runtime_title_is_unique
                and (not observation.time_candidates or time_score >= 0.90)
            )
            or (
                not runtime_title_is_unique
                and bool(observation.time_candidates)
                and time_exact
            )
        )
    )
    return {
        "slot_index": observation.slot_index,
        "runtime_index": int(item.get("runtime_index") or 0),
        "mail_id": str(item.get("id") or item.get("mail_id") or ""),
        "title": str(item.get("title") or ""),
        "title_score": round(title_score, 4),
        "time_score": round(time_score, 4),
        "time_exact": time_exact,
        "combined_score": round(combined, 4),
        "anchor": anchor,
        "exact": exact,
    }


def detect_stale_mail_snapshot(
    runtime_items: Iterable[Mapping[str, Any]],
    observations: Iterable[MailVisualObservation],
) -> tuple[dict[str, Any], ...]:
    """Find strong visual evidence newer than the complete runtime snapshot."""

    rows = _ordered_runtime_items(runtime_items)
    runtime_keys = [key for item in rows if len(key := _runtime_time_key(item)) >= 12]
    if not runtime_keys:
        return ()
    newest = max(runtime_keys)
    evidence: list[dict[str, Any]] = []
    for observation in observations:
        if not observation.trusted or not observation.title_candidates:
            continue
        visual_keys = [
            _time_digits(value)[:12] for value in observation.time_candidates
        ]
        visual_keys = [key for key in visual_keys if len(key) >= 12]
        if not visual_keys:
            continue
        visual = max(visual_keys)
        if visual <= newest:
            continue
        best_title = max(
            (
                _best_similarity(
                    observation.title_candidates, str(item.get("title") or "")
                )
                for item in rows
            ),
            default=0.0,
        )
        evidence.append(
            {
                "slot_index": observation.slot_index,
                "visual_time": visual,
                "snapshot_newest_time": newest,
                "title_candidates": list(observation.title_candidates),
                "best_snapshot_title_score": round(best_title, 4),
            }
        )
    return tuple(evidence)


def align_mail_window(
    runtime_items: Iterable[Mapping[str, Any]],
    observations: Iterable[MailVisualObservation],
    *,
    visible_slots: Iterable[int] | None = None,
    min_anchor_count: int = 2,
    min_score_margin: float = 1.0,
    expected_runtime_offset: int | None = None,
) -> MailWindowAlignment:
    """Align noisy visible rows to one unique contiguous MailMgr subsequence."""

    rows = _ordered_runtime_items(runtime_items)
    observed = sorted(observations, key=lambda item: item.slot_index)
    fingerprint = mail_snapshot_fingerprint(rows)
    stale = detect_stale_mail_snapshot(rows, observed)
    if stale:
        return MailWindowAlignment(
            status="snapshot_stale",
            reason="视觉窗口存在晚于动态快照的新邮件证据，必须重新读取 MailMgr",
            snapshot_fingerprint=fingerprint,
            stale_evidence=stale,
        )
    trusted = [
        item
        for item in observed
        if item.trusted and (item.title_candidates or item.time_candidates)
    ]
    if not rows or not trusted:
        return MailWindowAlignment(
            status="insufficient_evidence",
            reason="没有可用于配准的动态邮件或可信视觉锚点",
            snapshot_fingerprint=fingerprint,
        )

    runtime_title_counts = Counter(
        _clean_text(str(item.get("title") or "")) for item in rows
    )

    if expected_runtime_offset is not None:
        # A controlled drag predicts list movement; it does not prove the exact
        # number of rows loaded.  Keep the caller's continuity boundary, but
        # still let nearby offsets compete.  Otherwise one OCR anchor shared by
        # adjacent, visually identical mails can "confirm" a wrong prediction.
        expected = int(expected_runtime_offset)
        offsets = set(
            range(
                max(0, expected - 6),
                min(len(rows), expected + 6) + 1,
            )
        )
    else:
        offsets = {
            runtime_index - observation.slot_index
            for runtime_index in range(len(rows))
            for observation in trusted
        }
    hypotheses: list[MailAlignmentHypothesis] = []
    for offset in offsets:
        evidence: list[dict[str, Any]] = []
        anchor_count = exact_count = 0
        score = 0.0
        matched_slots: list[int] = []
        for observation in trusted:
            runtime_index = observation.slot_index + offset
            if not 0 <= runtime_index < len(rows):
                score -= 2.0
                continue
            runtime_item = rows[runtime_index]
            item_evidence = _observation_evidence(
                observation,
                runtime_item,
                runtime_title_is_unique=(
                    runtime_title_counts[_clean_text(str(runtime_item.get("title") or ""))]
                    == 1
                ),
            )
            evidence.append(item_evidence)
            combined = float(item_evidence["combined_score"])
            if item_evidence["anchor"]:
                anchor_count += 1
                matched_slots.append(observation.slot_index)
                score += 4.0 * combined
            else:
                score -= max(0.5, 1.5 * (0.62 - combined))
            exact_count += int(bool(item_evidence["exact"]))
        hypotheses.append(
            MailAlignmentHypothesis(
                runtime_offset=offset,
                score=round(score, 4),
                anchor_count=anchor_count,
                exact_anchor_count=exact_count,
                matched_slots=tuple(matched_slots),
                evidence=tuple(evidence),
            )
        )

    hypotheses.sort(
        key=lambda item: (item.score, item.anchor_count, item.exact_anchor_count),
        reverse=True,
    )
    best = hypotheses[0]
    second = hypotheses[1] if len(hypotheses) > 1 else None
    second_score = second.score if second is not None else 0.0
    margin = best.score - second_score
    status = "aligned"
    reason = "全局顺序、模糊文本和固定槽位共同得到唯一映射"
    required_anchors = max(1, int(min_anchor_count))
    if best.anchor_count < required_anchors:
        status = "insufficient_evidence"
        reason = f"可信锚点不足：{best.anchor_count} < {required_anchors}"
    elif required_anchors == 1 and best.exact_anchor_count < 1:
        status = "insufficient_evidence"
        reason = "已知连续偏移仅允许由至少一个精确 OCR 锚点确认"
    elif (
        second is not None
        and margin < float(min_score_margin)
        and not (
            best.exact_anchor_count >= 2
            and best.exact_anchor_count > second.exact_anchor_count
        )
    ):
        status = "ambiguous"
        reason = (
            f"最佳与次佳序列偏移分差不足：{margin:.2f} < {float(min_score_margin):.2f}"
        )
    elif (
        second is not None
        and margin < float(min_score_margin)
        and best.exact_anchor_count > second.exact_anchor_count
    ):
        reason = (
            "总分接近，但最佳连续片段具有更强的精确 OCR 锚点层级："
            f"{best.exact_anchor_count} > {second.exact_anchor_count}"
        )

    requested_slots = sorted(
        set(
            int(slot)
            for slot in (
                visible_slots
                if visible_slots is not None
                else [item.slot_index for item in observed]
            )
        )
    )
    observed_slots = {item.slot_index for item in observed}
    mappings: list[dict[str, Any]] = []
    if status == "aligned":
        for slot in requested_slots:
            runtime_index = slot + best.runtime_offset
            if not 0 <= runtime_index < len(rows):
                continue
            item = rows[runtime_index]
            mappings.append(
                {
                    "slot_index": slot,
                    "runtime_index": int(item.get("runtime_index") or runtime_index),
                    "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                    "title": str(item.get("title") or ""),
                    "create_time": (
                        item.get("create_time")
                        if item.get("create_time") is not None
                        else item.get("create_time_ms")
                    ),
                    "observed": slot in observed_slots,
                    "inferred": slot not in best.matched_slots,
                }
            )

    competitive_hypotheses = tuple(
        item
        for item in hypotheses
        if best.score - item.score < float(min_score_margin)
    ) if status == "ambiguous" else ()
    return MailWindowAlignment(
        status=status,
        reason=reason,
        snapshot_fingerprint=fingerprint,
        runtime_offset=best.runtime_offset if status == "aligned" else None,
        best_score=best.score,
        second_score=second_score,
        score_margin=round(margin, 4),
        anchor_count=best.anchor_count,
        exact_anchor_count=best.exact_anchor_count,
        mappings=tuple(mappings),
        # Ambiguous alignment is resolved by projecting *every* competitive
        # hypothesis onto Runtime business actions.  Truncating this list to
        # five made the caller unable to prove action equivalence when many
        # adjacent mails shared the same title/time.
        hypotheses=competitive_hypotheses or tuple(hypotheses[:5]),
        competitive_offsets=tuple(
            item.runtime_offset for item in competitive_hypotheses
        ),
    )


def diagnose_mail_window(
    snapshot: Mapping[str, Any],
    image121: Mapping[str, Any],
    ocr_fragments: Iterable[Mapping[str, Any]],
    *,
    expected_runtime_offset: int | None = None,
) -> dict[str, Any]:
    """Produce one read-only, auditable #121 pairing diagnosis."""

    items = snapshot.get("items")
    decoded_count = snapshot.get("decoded_count")
    if (
        not bool(snapshot.get("complete"))
        or not isinstance(items, list)
        or decoded_count is None
        or int(decoded_count) != len(items)
    ):
        return {
            "ok": False,
            "status": "snapshot_incomplete",
            "reason": str(snapshot.get("reason") or "动态邮件快照不完整或计数不一致"),
        }
    geometry = mail_window_geometry_from_asset(image121)
    visible_slots = geometry.visible_slot_indices()
    observations = build_mail_visual_observations(
        ocr_fragments,
        geometry,
        visible_slots=visible_slots,
    )
    alignment = align_mail_window(
        items,
        observations,
        visible_slots=visible_slots,
        # A controlled drag predicts where the Runtime fragment should be, but
        # its pixel distance is not an identity fact.  Clicking always needs at
        # least two independent visible anchors.  The known top-of-list case is
        # handled separately by the caller and only for safe forward navigation.
        min_anchor_count=2,
        expected_runtime_offset=expected_runtime_offset,
    )
    return {
        "ok": alignment.aligned,
        "status": alignment.status,
        "reason": alignment.reason,
        "geometry": asdict(geometry),
        "visible_slots": list(visible_slots),
        "observations": [asdict(item) for item in observations],
        "alignment": alignment.model_dump(),
    }


__all__ = [
    "MailVisualObservation",
    "MailWindowAlignment",
    "MailWindowGeometry",
    "align_mail_window",
    "build_mail_visual_observations",
    "detect_stale_mail_snapshot",
    "diagnose_mail_window",
    "mail_snapshot_fingerprint",
    "mail_snapshot_structure_fingerprint",
    "mail_window_geometry_from_asset",
    "stable_complete_mail_snapshots",
]
