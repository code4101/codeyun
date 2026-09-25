"""邮件有序窗口的只读身份对齐。

输入当前 Runtime 序列、场景资产和同帧 OCR，输出可操作行对应的邮件身份。
本模块不访问游戏、不采集画面、不落盘；画面新鲜度与页首位置由调用方保证。
标题、时间证据不足或存在等强候选时拒绝映射，不替业务层选择领取策略。
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from . import ocr_name_similarity
from .mail import (
    align_mail_window,
    best_mail_time_relation,
    build_mail_visual_observations,
    mail_runtime_time_key,
    mail_time_is_match,
    mail_window_geometry_from_asset,
)


class MailWindowAmbiguous(RuntimeError):
    """Identical title/time rows cannot establish a unique mail identity."""


def map_first_mail_from_ordered_rows(
    snapshot: dict[str, Any],
    image121: dict[str, Any],
    fragments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Infer Runtime #0 exclusively from the ordered OCR rows 2/3/4."""

    items = snapshot.get("items")
    if (
        not snapshot.get("complete")
        or not isinstance(items, list)
        or int(snapshot.get("decoded_count") or -1) != len(items)
        or len(items) < 4
    ):
        raise RuntimeError("邮件_选择性领取：首屏领取缺少完整 Runtime #0..#3 序列")
    geometry = mail_window_geometry_from_asset(image121)
    observations = build_mail_visual_observations(
        fragments,
        geometry,
        visible_slots=(1, 2, 3),
    )
    by_slot = {item.slot_index: item for item in observations}
    evidence: list[dict[str, Any]] = []
    known_title_anchor_count = 0
    for slot in (1, 2, 3):
        observation = by_slot.get(slot)
        runtime_item = items[slot]
        if observation is None:
            raise RuntimeError(
                f"邮件_选择性领取：首屏第 {slot + 1} 行 OCR 缺失，不能反推第1行"
            )
        expected_title = str(runtime_item.get("title") or "")
        title_score = max(
            (
                ocr_name_similarity(expected_title, candidate)
                for candidate in observation.title_candidates
            ),
            default=0.0,
        )
        expected_time = mail_runtime_time_key(runtime_item)
        time_matched = mail_time_is_match(
            best_mail_time_relation(observation.time_candidates, expected_time)
        )
        runtime_title_unknown = expected_title.startswith("未知邮件类型")
        if not runtime_title_unknown and title_score >= 0.68:
            known_title_anchor_count += 1
        if (title_score < 0.68 and not runtime_title_unknown) or not time_matched:
            raise RuntimeError(
                f"邮件_选择性领取：首屏第 {slot + 1} 行未按序匹配 Runtime #{slot}；"
                f"title_score={title_score:.2f} time_matched={time_matched} "
                f"ocr_titles={list(observation.title_candidates)} "
                f"ocr_times={list(observation.time_candidates)} "
                f"runtime_title={expected_title} runtime_time={expected_time}"
            )
        evidence.append(
            {
                "slot_index": slot,
                "runtime_index": slot,
                "title_score": round(title_score, 4),
                "time_matched": True,
                "runtime_title_unknown": runtime_title_unknown,
            }
        )
    if known_title_anchor_count < 1:
        raise RuntimeError(
            "邮件_选择性领取：首屏第2/3/4行仅有未知 Runtime 标题与时间证据，"
            "缺少至少一条真实标题锚点，不能反推第1行"
        )
    first = items[0]
    return {
        "slot_index": 0,
        "runtime_index": int(first.get("runtime_index") or 0),
        "mail_id": str(first.get("id") or first.get("mail_id") or ""),
        "title": str(first.get("title") or ""),
        "create_time_text": str(first.get("create_time_text") or ""),
        "evidence": evidence,
    }


def map_ordered_mail_window(
    snapshot: dict[str, Any],
    image121: dict[str, Any],
    fragments: list[dict[str, Any]],
    *,
    previous_offset: int,
    known_top: bool,
) -> dict[str, Any]:
    """Map one freshly OCRed GUI window to the ordered Runtime sequence."""

    items = list(snapshot.get("items") or [])
    geometry = mail_window_geometry_from_asset(image121)
    # The footer overlays the fifth lattice row on the real #121 screen.
    # Only rows 1..4 (slots 0..3) are fully actionable; a partially visible
    # fifth title must trigger a downward scroll, never a click.
    visible_slots = [slot for slot in geometry.visible_slot_indices() if int(slot) <= 3]
    observations = []
    if known_top:
        try:
            map_first_mail_from_ordered_rows(snapshot, image121, fragments)
            anchor_count = 3
        except RuntimeError:
            # Some client mail types render completely blank rows. Require
            # two independent visible anchors instead of inventing their
            # missing titles/times. Row 0 is usable only when its exact,
            # globally unique title and time both match current MailMgr.
            if not snapshot.get("complete") or snapshot.get("decoded_count") != len(items):
                raise
            observations = build_mail_visual_observations(
                fragments, geometry, visible_slots=visible_slots,
            )
            if items:
                title = str(items[0].get("title") or "")
                stamp = mail_runtime_time_key(items[0])
                unique_title = bool(title) and sum(str(x.get("title") or "") == title for x in items) == 1
                observations = [
                    replace(o, trusted=True, reliability=1.0)
                    if o.slot_index == 0 and unique_title
                    and any(ocr_name_similarity(title, candidate) >= 0.99 for candidate in o.title_candidates)
                    and mail_time_is_match(
                        best_mail_time_relation(o.time_candidates, stamp)
                    )
                    else o for o in observations
                ]
            alignment = align_mail_window(
                items, observations, visible_slots=visible_slots,
                min_anchor_count=2, expected_runtime_offset=0,
            )
            if not alignment.aligned or alignment.runtime_offset != 0:
                raise
            anchor_count = alignment.anchor_count
        offset = 0
    else:
        observations = build_mail_visual_observations(
            fragments,
            geometry,
            visible_slots=visible_slots,
        )
        candidates: list[tuple[int, int, list[dict[str, Any]]]] = []
        # Returning from a claimed detail can restore the list at the
        # previous pre-scroll offset, or one row above it after the list
        # settles.  A strictly increasing lower bound then rejects a fully
        # aligned window and aborts the batch.  Accept that one-row rebound;
        # the caller only scrolls again when every remaining target is
        # beyond this verified visible window.
        for offset_candidate in range(max(0, int(previous_offset) - 1), len(items)):
            evidence: list[dict[str, Any]] = []
            for observation in observations:
                runtime_index = offset_candidate + int(observation.slot_index)
                if not 0 <= runtime_index < len(items):
                    continue
                runtime_item = items[runtime_index]
                expected_title = str(runtime_item.get("title") or "")
                title_score = max(
                    (
                        ocr_name_similarity(expected_title, candidate)
                        for candidate in observation.title_candidates
                    ),
                    default=0.0,
                )
                expected_time = mail_runtime_time_key(runtime_item)
                time_matched = mail_time_is_match(
                    best_mail_time_relation(
                        observation.time_candidates, expected_time
                    )
                )
                runtime_title_unknown = expected_title.startswith("未知邮件类型")
                if time_matched and (title_score >= 0.68 or runtime_title_unknown):
                    evidence.append(
                        {
                            "slot_index": int(observation.slot_index),
                            "runtime_index": runtime_index,
                            "title_score": round(title_score, 4),
                            "runtime_title_unknown": runtime_title_unknown,
                        }
                    )
            if (
                len(evidence) >= 2
                and any(float(item["title_score"]) >= 0.68 for item in evidence)
            ):
                candidates.append((len(evidence), offset_candidate, evidence))
        if candidates:
            strongest = max(item[0] for item in candidates)
            strongest_candidates = [item for item in candidates if item[0] == strongest]
            if len(strongest_candidates) != 1:
                raise MailWindowAmbiguous(
                    "邮件_选择性领取：同名同时间窗口存在多个等强序列位置，禁止猜测最小偏移；"
                    f"offsets={[item[1] for item in strongest_candidates]}"
                )
            _count, offset, _evidence = strongest_candidates[0]
            anchor_count = strongest
        else:
            # A controlled scroll gives us a continuity boundary.  OCR can
            # occasionally return only one complete row after the inertial
            # list settles; requiring two rows then rejects an otherwise
            # exact, unique title/time anchor.  Reuse the shared alignment
            # law, which accepts one anchor only when it is exact and the
            # competing offsets are unambiguous.  Repeated identical rows
            # therefore remain fail-closed.
            exact_alignment = align_mail_window(
                items,
                observations,
                visible_slots=visible_slots,
                min_anchor_count=1,
                # One exact unique title separates the best offset by
                # 0.68 when every neighbouring midnight mail shares the
                # same minute.  Keep the margin below that exact-title
                # contribution; duplicated title/time rows still tie at
                # zero and remain ambiguous.
                min_score_margin=0.5,
                expected_runtime_offset=max(0, int(previous_offset)),
            )
            if not exact_alignment.aligned:
                observed_summary = [
                    {
                        "slot": int(observation.slot_index),
                        "titles": list(observation.title_candidates),
                        "times": list(observation.time_candidates),
                    }
                    for observation in observations
                ]
                raise RuntimeError(
                    "邮件_选择性领取：滚动后当前窗口既没有至少两行按序匹配 Runtime，"
                    "也没有唯一精确单行锚点；"
                    f"alignment={exact_alignment.status}:{exact_alignment.reason} "
                    f"observed={observed_summary}"
                )
            offset = int(exact_alignment.runtime_offset or 0)
            anchor_count = int(exact_alignment.anchor_count)
    mappings = []
    for slot in visible_slots:
        runtime_index = offset + int(slot)
        if not 0 <= runtime_index < len(items):
            continue
        item = items[runtime_index]
        observation = next(
            (
                candidate
                for candidate in observations
                if int(candidate.slot_index) == int(slot)
            ),
            None,
        )
        observed_title = ""
        if observation is not None and observation.title_candidates:
            runtime_title = str(item.get("title") or "")
            if runtime_title.startswith("未知邮件类型"):
                observed_title = max(
                    observation.title_candidates,
                    key=lambda candidate: len(re.sub(r"\s+", "", candidate)),
                )
            else:
                observed_title = max(
                    observation.title_candidates,
                    key=lambda candidate: ocr_name_similarity(runtime_title, candidate),
                )
        mappings.append(
            {
                "slot_index": int(slot),
                "runtime_index": int(item.get("runtime_index") or runtime_index),
                "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                "title": str(item.get("title") or ""),
                "observed_title": observed_title,
                "create_time_text": str(item.get("create_time_text") or ""),
            }
        )
    return {
        "runtime_offset": offset,
        "anchor_count": anchor_count,
        "mappings": mappings,
    }
