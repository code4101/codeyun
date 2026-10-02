from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.data_annotation.ocr_spatial import (
    find_text_matches,
    segment_ocr_tokens,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from pyxllib.autogui import frame_size as _frame_size


WORLD_VIEW_ID = 34
EQUIPMENT_VIEW_ID = 445
EQUIPMENT_STRENGTHENING_VIEW_ID = 446

_WORLD_EQUIPMENT_TARGETS = ("装备", "装", "备")
_STRENGTHENING_OCR_OPTIONS = {
    "text_det_thresh": 0.2,
    "text_det_box_thresh": 0.35,
    "text_det_unclip_ratio": 1.1,
}

_CATEGORY_KEYS = {"初灵": "initial", "洞玄": "dongxuan"}
_PART_TITLE_KEYWORDS = {
    "灵环": ("灵环",),
    "气铠": ("气铠",),
    "宝冠": ("宝冠", "头冠", "冠冕"),
    "羽巾": ("羽巾",),
    "华履": ("华履", "鞋子"),
    "锦带": ("锦带", "腰带"),
    "灵坠": ("灵坠",),
    "仙符": ("仙符", "护符"),
    "灵镯": ("灵镯",),
    "宝戒": ("宝戒",),
}
_PART_ALIASES = {"护符": "仙符"}
_PART_INDEX = {part: index for index, part in enumerate(_PART_TITLE_KEYWORDS, 1)}
_VISIBLE_CARD_X_RATIOS = (0.15, 0.34, 0.53, 0.72, 0.90)


@dataclass(frozen=True)
class EquipmentStrengtheningTarget:
    category: str
    part: str
    equipment_level: int
    material_count: int
    equipped: bool
    equipment_raw_level: int | None = None
    fingerprint_unique: bool = True


@dataclass(frozen=True)
class EquipmentStrengtheningObservation:
    description_text: str
    resource_text: str
    equipment_level: int | None
    resource_current: int | None
    resource_required: int | None


@dataclass(frozen=True)
class EquipmentStrengtheningRouteTarget:
    order: int
    part: str
    category: str
    equipment_level: int
    equipment_raw_level: int
    material_count: int


class EquipmentStrengtheningResourceExhausted(RuntimeError):
    """The selected target is valid, but the current inventory cannot reach it."""

    def __init__(
        self,
        message: str,
        *,
        target_progress: int,
        equipment_progress: int,
        cumulative_material: int | None = None,
    ) -> None:
        super().__init__(message)
        self.target_progress = int(target_progress)
        self.equipment_progress = int(equipment_progress)
        self.cumulative_material = (
            int(cumulative_material) if cumulative_material is not None else None
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    raise TypeError(f"需要字典或 Pydantic model，实际为 {type(value).__name__}")


def resolve_equipment_strengthening_target(
    snapshot: Any,
    category: str,
    part: str,
) -> EquipmentStrengtheningTarget:
    """Resolve a human target from the read-only strengthening snapshot."""

    normalized_category = str(category or "").strip()
    requested_part = str(part or "").strip()
    normalized_part = _PART_ALIASES.get(requested_part, requested_part)
    side_key = _CATEGORY_KEYS.get(normalized_category)
    if side_key is None:
        raise ValueError(f"装备类别必须是初灵或洞玄：{category!r}")
    if normalized_part not in _PART_TITLE_KEYWORDS:
        raise ValueError(f"未知装备部位：{part!r}")

    payload = _as_mapping(snapshot)
    for raw_row in payload.get("rows") or ():
        row = _as_mapping(raw_row)
        if str(row.get("part") or "").strip() != normalized_part:
            continue
        side = _as_mapping(row.get(side_key) or {})
        equipped = side.get("equipped") is True
        level = side.get("equipment_level")
        material_count = side.get("material_count")
        if not equipped or level is None:
            raise RuntimeError(f"{normalized_category}{normalized_part}当前未装备，无法在强化页选择")
        if material_count is None:
            raise RuntimeError(f"{normalized_category}{normalized_part}的玄铁数量尚未加载")
        level_value = int(level)
        raw_level = side.get("equipment_raw_level")
        material_value = int(material_count)
        fingerprint_matches = 0
        for candidate_raw in payload.get("rows") or ():
            candidate_row = _as_mapping(candidate_raw)
            # 洞玄 has a positive title marker on screen.  初灵 is verified by
            # absence of that marker, so its numeric fingerprint must also be
            # unique against every equipped 洞玄 slot to stay fail-closed when
            # the category title itself is missed by OCR.
            fingerprint_side_keys = (
                tuple(_CATEGORY_KEYS.values())
                if normalized_category == "初灵"
                else (side_key,)
            )
            for fingerprint_side_key in fingerprint_side_keys:
                candidate_side = _as_mapping(
                    candidate_row.get(fingerprint_side_key) or {}
                )
                if candidate_side.get("equipped") is not True:
                    continue
                if (
                    candidate_side.get("equipment_level") is not None
                    and int(candidate_side["equipment_level"]) == level_value
                    and candidate_side.get("material_count") is not None
                    and int(candidate_side["material_count"]) == material_value
                ):
                    fingerprint_matches += 1
        return EquipmentStrengtheningTarget(
            category=normalized_category,
            part=normalized_part,
            equipment_level=level_value,
            material_count=material_value,
            equipped=True,
            equipment_raw_level=(int(raw_level) if raw_level is not None else level_value * 9),
            fingerprint_unique=fingerprint_matches == 1,
        )
    raise RuntimeError(f"强化快照中没有装备部位：{normalized_part}")


def plan_equipment_strengthening_route(
    snapshot: Any,
) -> list[EquipmentStrengtheningRouteTarget]:
    """Choose one stable target per part, in the canonical 1..10 order."""

    payload = _as_mapping(snapshot)
    rows_by_part = {
        str(_as_mapping(raw_row).get("part") or "").strip(): _as_mapping(raw_row)
        for raw_row in payload.get("rows") or ()
    }
    route: list[EquipmentStrengtheningRouteTarget] = []
    for part, order in _PART_INDEX.items():
        row = rows_by_part.get(part)
        if row is None:
            continue
        candidates: list[tuple[int, int, int, str, Mapping[str, Any]]] = []
        for category_order, (category, side_key) in enumerate(_CATEGORY_KEYS.items()):
            side = _as_mapping(row.get(side_key) or {})
            if side.get("equipped") is not True:
                continue
            level = side.get("equipment_level")
            raw_level = side.get("equipment_raw_level")
            material_count = side.get("material_count")
            if level is None or material_count is None:
                continue
            candidates.append(
                (
                    int(level),
                    int(raw_level if raw_level is not None else int(level) * 9),
                    category_order,
                    category,
                    side,
                )
            )
        if not candidates:
            continue
        level, raw_level, _category_order, category, side = min(candidates)
        route.append(
            EquipmentStrengtheningRouteTarget(
                order=order,
                part=part,
                category=category,
                equipment_level=level,
                equipment_raw_level=raw_level,
                material_count=int(side["material_count"]),
            )
        )
    return route


def _strengthening_progress(snapshot: Any) -> tuple[int | None, int | None]:
    payload = _as_mapping(snapshot)
    equipment_current = payload.get("equipment_current")
    score_current = payload.get("score_current")
    score_round = int(payload.get("score_round") or 1)
    completed_score = sum(
        int(_as_mapping(item).get("target") or 0)
        for item in payload.get("score_rounds") or ()
        if int(_as_mapping(item).get("round") or 0) < score_round
    )
    return (
        int(equipment_current) if equipment_current is not None else None,
        completed_score + int(score_current) if score_current is not None else None,
    )


def _equipment_level_sequence(snapshot: Any, category: str) -> dict[int, str | None]:
    """Return the current category's dynamic levels in canonical part order."""

    side_key = _CATEGORY_KEYS[category]
    levels: dict[int, str | None] = {
        index: None for index in range(1, len(_PART_INDEX) + 1)
    }
    for raw_row in _as_mapping(snapshot).get("rows") or ():
        row = _as_mapping(raw_row)
        part_index = _PART_INDEX.get(str(row.get("part") or "").strip())
        if part_index is None:
            continue
        side = _as_mapping(row.get(side_key) or {})
        if side.get("equipped") is True and side.get("equipment_level") is not None:
            levels[part_index] = str(int(side["equipment_level"]))
    return levels


def _numeric_ocr_fragments(tokens: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep spatial numeric fragments as soft evidence for sequence scoring."""

    fragments: list[dict[str, Any]] = []
    for segment in segment_ocr_tokens(tokens):
        boxes = [_box(dict(token)) for token in segment]
        boxes = [box for box in boxes if box is not None]
        if not boxes:
            continue
        raw_text = "".join(str(token.get("text") or "") for token in segment)
        text = _sanitize_ocr_text(raw_text)
        digit_groups = re.findall(r"\d+", text)
        if len(digit_groups) != 1:
            continue
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[0] + box[2] for box in boxes)
        bottom = max(box[1] + box[3] for box in boxes)
        fragments.append(
            {
                "text": digit_groups[0],
                "x": left,
                "y": top,
                "w": right - left,
                "h": bottom - top,
                "center_x": (left + right) / 2,
                "center_y": (top + bottom) / 2,
            }
        )
    return fragments


def _predict_equipment_point_from_level_sequence(
    tokens: Iterable[dict[str, Any]],
    snapshot: Any,
    target: EquipmentStrengtheningTarget,
    *,
    slot_pitch: float,
    shape_left: float,
    shape_right: float,
    click_y: float,
) -> dict[str, Any] | None:
    """Globally align a noisy OCR row to the ordered dynamic equipment list.

    The annotated adjacent-card pitch is the geometry source of truth.  OCR is
    allowed to omit or corrupt individual levels; exact levels create layout
    hypotheses while all numeric fragments contribute only soft evidence.
    """

    pitch = abs(float(slot_pitch))
    if pitch <= 1:
        return None
    token_list = [dict(token) for token in tokens if isinstance(token, dict)]
    levels = _equipment_level_sequence(snapshot, target.category)
    level_parts: dict[str, list[int]] = {}
    for part_index, level in levels.items():
        if level is not None:
            level_parts.setdefault(level, []).append(part_index)

    exact_observations: list[dict[str, Any]] = []
    for level, part_indices in level_parts.items():
        for match in find_text_matches(token_list, level):
            x, y = match.point()
            exact_observations.append(
                {
                    "level": level,
                    "part_indices": tuple(part_indices),
                    "x": float(x),
                    "y": float(y),
                }
            )
    if not exact_observations:
        return None

    # Each exact OCR occurrence and each dynamically possible part index forms
    # one candidate grid origin.  Scoring the entire row then resolves duplicate
    # levels and rejects reversed/inconsistent OCR observations.
    origins = {
        round(observation["x"] - (part_index - 1) * pitch, 3)
        for observation in exact_observations
        for part_index in observation["part_indices"]
    }
    numeric_fragments = _numeric_ocr_fragments(token_list)
    tolerance = max(12.0, pitch * 0.32)
    hypotheses: list[dict[str, Any]] = []
    for origin in origins:
        matched_exact: list[dict[str, Any]] = []
        exact_score = 0.0
        used_observations: set[int] = set()
        for part_index, level in levels.items():
            if level is None:
                continue
            expected_x = origin + (part_index - 1) * pitch
            candidates = [
                (abs(float(observation["x"]) - expected_x), observation_index, observation)
                for observation_index, observation in enumerate(exact_observations)
                if observation_index not in used_observations
                and observation["level"] == level
                and abs(float(observation["x"]) - expected_x) <= tolerance
            ]
            if not candidates:
                continue
            residual, observation_index, observation = min(candidates, key=lambda item: item[0])
            used_observations.add(observation_index)
            exact_score += 4.0 * (1.0 - residual / tolerance)
            matched_exact.append(
                {
                    "part_index": part_index,
                    "level": level,
                    "x": observation["x"],
                    "residual": residual,
                }
            )

        soft_score = 0.0
        soft_matches: list[dict[str, Any]] = []
        for fragment in numeric_fragments:
            nearest_index = round((float(fragment["center_x"]) - origin) / pitch) + 1
            expected_level = levels.get(nearest_index)
            if expected_level is None:
                continue
            expected_x = origin + (nearest_index - 1) * pitch
            residual = abs(float(fragment["center_x"]) - expected_x)
            if residual > tolerance:
                continue
            similarity = SequenceMatcher(
                None,
                str(fragment["text"]),
                expected_level,
            ).ratio()
            if similarity < 0.45:
                continue
            contribution = similarity * (1.0 - residual / tolerance)
            soft_score += contribution
            soft_matches.append(
                {
                    "part_index": nearest_index,
                    "expected": expected_level,
                    "observed": fragment["text"],
                    "similarity": similarity,
                    "residual": residual,
                }
            )

        hypotheses.append(
            {
                "origin": float(origin),
                "score": exact_score + min(2.0, soft_score),
                "exact_matches": matched_exact,
                "soft_matches": soft_matches,
            }
        )

    hypotheses.sort(key=lambda item: item["score"], reverse=True)
    if not hypotheses:
        return None
    best = hypotheses[0]
    competing = next(
        (
            hypothesis
            for hypothesis in hypotheses[1:]
            if abs(float(hypothesis["origin"]) - float(best["origin"])) > pitch * 0.2
        ),
        None,
    )
    exact_count = len(best["exact_matches"])
    unique_single_anchor = (
        exact_count == 1
        and len(level_parts.get(str(best["exact_matches"][0]["level"]), ())) == 1
    )
    margin = float(best["score"]) - float(competing["score"] if competing else 0.0)
    if exact_count < 2 and not unique_single_anchor:
        return None
    if competing is not None and margin < 1.0:
        return None

    target_index = _PART_INDEX[target.part]
    predicted_x = float(best["origin"]) + (target_index - 1) * pitch
    half_card_margin = pitch * 0.45
    if not shape_left + half_card_margin <= predicted_x <= shape_right - half_card_margin:
        return None
    return {
        "x": predicted_x,
        "y": float(click_y),
        "slot_pitch": pitch,
        "score": float(best["score"]),
        "score_margin": margin,
        "exact_matches": best["exact_matches"],
        "soft_matches": best["soft_matches"],
    }


def read_selected_equipment_strengthening(
    context: Any,
    *,
    frame_data_url: str | None = None,
) -> EquipmentStrengtheningObservation:
    """Read the selected equipment only from #446 description/resource OCR."""

    frame = frame_data_url or context.cur_frame(update=True)
    description_text = str(
        context.ocr_text_in_shapes(
            EQUIPMENT_STRENGTHENING_VIEW_ID,
            ("描述",),
            padding=4,
            frame_data_url=frame,
            crop=True,
        )
        or ""
    )
    description_tokens = context.ocr_tokens_in_shapes(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        ("描述",),
        padding=4,
        frame_data_url=frame,
        crop=True,
    )
    resource_text = str(
        context.ocr_text_in_shapes(
            EQUIPMENT_STRENGTHENING_VIEW_ID,
            ("资源",),
            padding=16,
            frame_data_url=frame,
            crop=True,
        )
        or ""
    )
    ordered_description_tokens = sorted(
        description_tokens,
        key=lambda token: (
            int(token.get("line_order") or 0),
            int(token.get("order") or 0),
            float(token.get("y") or 0),
            float(token.get("x") or 0),
        ),
    )
    level_token = next(
        (
            str(token.get("text") or "")
            for token in ordered_description_tokens
            if re.fullmatch(r"\d+", str(token.get("text") or "").strip())
        ),
        "",
    )
    normalized_description = _sanitize_ocr_text(description_text)
    level_match = re.search(r"强化等级[:：]?\s*(\d+)", normalized_description)
    resource_values = parse_ocr_values(resource_text, expected_count=2)
    return EquipmentStrengtheningObservation(
        description_text=description_text,
        resource_text=resource_text,
        equipment_level=(
            int(level_token)
            if level_token
            else int(level_match.group(1)) if level_match else None
        ),
        resource_current=resource_values[0] if resource_values else None,
        resource_required=resource_values[1] if resource_values else None,
    )


def verify_selected_equipment_strengthening(
    observation: EquipmentStrengtheningObservation,
    target: EquipmentStrengtheningTarget,
) -> tuple[bool, list[str]]:
    """Verify selection with independent category, part, level and material signals."""

    text = _sanitize_ocr_text(observation.description_text)
    failures: list[str] = []
    if target.category == "洞玄" and "洞玄" not in text:
        failures.append("描述没有洞玄类别标记")
    if target.category == "初灵" and "洞玄" in text:
        failures.append("描述仍是洞玄装备")
    has_part_keyword = any(
        keyword in text for keyword in _PART_TITLE_KEYWORDS[target.part]
    )
    if not target.fingerprint_unique and not has_part_keyword:
        failures.append("等级与玄铁指纹在当前类别不唯一，描述也没有部位关键字")
    if observation.equipment_level != target.equipment_level:
        failures.append(
            f"等级不符：画面={observation.equipment_level}，快照={target.equipment_level}"
        )
    if observation.resource_current != target.material_count:
        failures.append(
            f"玄铁不符：画面={observation.resource_current}，快照={target.material_count}"
        )
    return not failures, failures


def _box(token: dict[str, Any]) -> tuple[float, float, float, float] | None:
    x = float(token.get("x") or 0)
    y = float(token.get("y") or 0)
    w = float(token.get("w") or 0)
    h = float(token.get("h") or 0)
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _find_world_equipment_token(
    tokens: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, token in enumerate(tokens):
        if _box(token) is None:
            continue
        text = _sanitize_ocr_text(str(token.get("text") or ""))
        priority = next(
            (
                target_index
                for target_index, target in enumerate(_WORLD_EQUIPMENT_TARGETS)
                if target in text
            ),
            None,
        )
        if priority is not None:
            candidates.append((priority, index, dict(token)))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _text_center(token: dict[str, Any]) -> tuple[float, float]:
    box = _box(token)
    if box is None:
        raise RuntimeError("OCR 文本缺少有效坐标")
    x, y, w, h = box
    return x + w / 2, y + h / 2


def _world_equipment_click_point(token: dict[str, Any]) -> tuple[float, float]:
    """Click one token height above the OCR text's top-center."""

    box = _box(token)
    if box is None:
        raise RuntimeError("#34 装备 OCR 文本缺少有效坐标")
    x, y, w, h = box
    return x + w / 2, y - h


def _find_strengthening_center(
    tokens: Iterable[dict[str, Any]],
) -> tuple[float, float] | None:
    valid: list[tuple[int, dict[str, Any], str]] = []
    for index, token in enumerate(tokens):
        if _box(token) is None:
            continue
        text = _sanitize_ocr_text(str(token.get("text") or ""))
        if text:
            valid.append((index, dict(token), text))

    combined = [
        (index, token)
        for index, token, text in valid
        if "强" in text and "化" in text
    ]
    if combined:
        return _text_center(min(combined, key=lambda item: item[0])[1])

    strong_tokens = [token for _index, token, text in valid if "强" in text]
    transform_tokens = [token for _index, token, text in valid if "化" in text]
    pairs: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for strong in strong_tokens:
        strong_box = _box(strong)
        if strong_box is None:
            continue
        sx, sy, sw, sh = strong_box
        strong_cx = sx + sw / 2
        strong_cy = sy + sh / 2
        for transform in transform_tokens:
            transform_box = _box(transform)
            if transform_box is None:
                continue
            tx, ty, tw, th = transform_box
            transform_cx = tx + tw / 2
            transform_cy = ty + th / 2
            max_width = max(sw, tw)
            max_height = max(sh, th)
            vertical_edge_gap = ty - (sy + sh)
            if transform_cy <= strong_cy:
                continue
            if abs(transform_cx - strong_cx) > max_width * 1.25:
                continue
            if vertical_edge_gap > max_height * 1.5:
                continue
            distance = abs(transform_cx - strong_cx) + abs(vertical_edge_gap)
            pairs.append((distance, strong, transform))
    if not pairs:
        return None

    _distance, strong, transform = min(pairs, key=lambda item: item[0])
    sx, sy, sw, sh = _box(strong) or (0.0, 0.0, 0.0, 0.0)
    tx, ty, tw, th = _box(transform) or (0.0, 0.0, 0.0, 0.0)
    left = min(sx, tx)
    top = min(sy, ty)
    right = max(sx + sw, tx + tw)
    bottom = max(sy + sh, ty + th)
    return (left + right) / 2, (top + bottom) / 2


def _click_world_equipment(
    context: Any,
    *,
    max_attempts: int,
    retry_seconds: float,
) -> dict[str, Any]:
    last_tokens: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        frame = context.cur_frame(update=True)
        last_tokens = context.ocr_tokens_in_shapes(
            WORLD_VIEW_ID,
            ("下方菜单",),
            padding=4,
            frame_data_url=frame,
        )
        token = _find_world_equipment_token(last_tokens)
        if token is not None:
            x, y = _world_equipment_click_point(token)
            context.click_frame_point(WORLD_VIEW_ID, x, y)
            return {
                "attempt": attempt,
                "token": token,
                "click": [x, y],
            }
        if attempt < max(1, int(max_attempts)):
            yield from context.wait_action_settle(retry_seconds)
    raise RuntimeError(
        "#34[下方菜单] 未识别到「装备」「装」或「备」，"
        f"OCR={last_tokens}"
    )


def _click_strengthening_menu(
    context: Any,
    *,
    max_attempts: int,
    retry_seconds: float,
) -> dict[str, Any]:
    last_tokens: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        frame = context.cur_frame(update=True)
        last_tokens = context.ocr_tokens_in_shapes(
            EQUIPMENT_VIEW_ID,
            ("菜单",),
            padding=4,
            frame_data_url=frame,
            crop=True,
            options=_STRENGTHENING_OCR_OPTIONS,
        )
        point = _find_strengthening_center(last_tokens)
        if point is not None:
            context.click_frame_point(EQUIPMENT_VIEW_ID, *point)
            return {
                "attempt": attempt,
                "tokens": last_tokens,
                "click": list(point),
            }
        if attempt < max(1, int(max_attempts)):
            yield from context.wait_action_settle(retry_seconds)
    raise RuntimeError(
        "#445[菜单] 局部 OCR 未识别到竖排相邻的「强」「化」，"
        f"OCR={last_tokens}"
    )


def ensure_equipment_strengthening(
    context: Any,
    *,
    world_ocr_attempts: int = 3,
    strengthening_ocr_attempts: int = 3,
    retry_seconds: float = 0.5,
    transition_timeout: float = 20.0,
):
    """Idempotently ensure the game is on equipment strengthening view #446.

    This is a reusable navigation generator, not a Scheduler task. Failures are
    deliberately raised in place so callers retain the current game screen.
    """

    _wait_scene_match = yield from context.wait_scene((EQUIPMENT_STRENGTHENING_VIEW_ID, EQUIPMENT_VIEW_ID, WORLD_VIEW_ID), wait=5.0, required=False)
    (scene_id, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene_id == EQUIPMENT_STRENGTHENING_VIEW_ID:
        return {
            "ok": True,
            "changed": False,
            "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
        }

    actions: list[dict[str, Any]] = []
    if scene_id != EQUIPMENT_VIEW_ID:
        if scene_id != WORLD_VIEW_ID:
            yield from context.go_scene(WORLD_VIEW_ID)
        yield from context.wait_scene(
            [WORLD_VIEW_ID],
            wait=transition_timeout,
            label="进入装备强化：等待世界 #34",
        )
        equipment_action = yield from _click_world_equipment(
            context,
            max_attempts=world_ocr_attempts,
            retry_seconds=retry_seconds,
        )
        actions.append({"step": "world_to_equipment", **equipment_action})
        yield from context.wait_scene(
            [EQUIPMENT_VIEW_ID],
            wait=transition_timeout,
            label="进入装备强化：等待装备页 #445",
        )

    strengthening_action = yield from _click_strengthening_menu(
        context,
        max_attempts=strengthening_ocr_attempts,
        retry_seconds=retry_seconds,
    )
    actions.append({"step": "equipment_to_strengthening", **strengthening_action})
    yield from context.wait_scene(
        [EQUIPMENT_STRENGTHENING_VIEW_ID],
        wait=transition_timeout,
        label="进入装备强化：等待强化页 #446",
    )
    return {
        "ok": True,
        "changed": True,
        "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
        "actions": actions,
    }


def select_equipment_strengthening(
    context: Any,
    category: str,
    part: str,
    *,
    snapshot: Any | None = None,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
    max_scrolls_per_direction: int = 12,
    settle_seconds: float = 0.8,
):
    """Select and prove one #446 equipment card without recognizing its image.

    Category is selected first; its carousel retains the prior scroll position.
    Reset the carousel to its left boundary before searching. Card OCR is a candidate
    locator; success requires description and resource verification afterward.
    """

    if snapshot is None:
        from backend.core.fanxiu.activity.lingzhuang_strengthening import (
            read_lingzhuang_strengthening_runtime_snapshot,
        )

        snapshot = read_lingzhuang_strengthening_runtime_snapshot(
            cross_count=int(cross_count),
            game_task_activity_id=game_task_activity_id,
        )
    target = resolve_equipment_strengthening_target(snapshot, category, part)
    yield from ensure_equipment_strengthening(context)

    observation = read_selected_equipment_strengthening(context)
    if verify_selected_equipment_strengthening(observation, target)[0]:
        yield from context.wait_action_settle(1)
        observation = read_selected_equipment_strengthening(context)
        if verify_selected_equipment_strengthening(observation, target)[0]:
            return dict(ok=True, view_id=EQUIPMENT_STRENGTHENING_VIEW_ID,
                        target=asdict(target), observation=asdict(observation), attempts=[],
                        skipped="already_selected")

    context.click_ocr_text(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        target.category,
        in_shapes=("类别",),
        padding=4,
    )
    yield from context.wait_action_settle(settle_seconds)

    target_view = context.view(EQUIPMENT_STRENGTHENING_VIEW_ID)
    equipment_shape = context.resolve_shape_selector(target_view, "装备")
    # 真实界面保留轮播位置，切类别不能代替复位；默认手势逐步回到左边界。
    for _ in range(4):
        changed = yield from context.scroll_shape_content(equipment_shape, direction="left")
        if not changed:
            break
    else:
        raise RuntimeError("装备轮播左边界无法确认，未选择或强化")
    alignment_geometry: dict[str, float] | None = None
    try:
        first_slot_shape = context.resolve_shape_selector(target_view, "装备/框1")
        second_slot_shape = context.resolve_shape_selector(target_view, "装备/框2")
        frame_width, frame_height = _frame_size(target_view.raw)
        equipment_left = float(equipment_shape.raw.get("x") or 0) * frame_width
        equipment_right = (
            float(equipment_shape.raw.get("x") or 0)
            + float(equipment_shape.raw.get("w") or 0)
        ) * frame_width
        first_center_x = (
            float(first_slot_shape.raw.get("x") or 0)
            + float(first_slot_shape.raw.get("w") or 0) / 2
        ) * frame_width
        second_center_x = (
            float(second_slot_shape.raw.get("x") or 0)
            + float(second_slot_shape.raw.get("w") or 0) / 2
        ) * frame_width
        first_center_y = (
            float(first_slot_shape.raw.get("y") or 0)
            + float(first_slot_shape.raw.get("h") or 0) / 2
        ) * frame_height
        second_center_y = (
            float(second_slot_shape.raw.get("y") or 0)
            + float(second_slot_shape.raw.get("h") or 0) / 2
        ) * frame_height
        alignment_geometry = {
            "slot_pitch": abs(second_center_x - first_center_x),
            "shape_left": equipment_left,
            "shape_right": equipment_right,
            "click_y": (first_center_y + second_center_y) / 2,
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        # Older assets and narrow test doubles can still use exact OCR plus
        # strictly verified geometry probing.  The current #446 asset provides
        # both annotated reference slots, so production uses sequence alignment.
        alignment_geometry = None
    expected_level = str(target.equipment_level)
    attempts: list[dict[str, Any]] = []
    last_failures: list[str] = []

    def inspect_after_click(candidate: dict[str, Any]):
        nonlocal last_failures
        yield from context.wait_action_settle(settle_seconds)
        stable_matches = 0
        # 选中框先更新、详情后更新。给当前动作有界观察窗口，期间不再点击其它卡片。
        for _ in range(5):
            yield from context.wait_scene_exact([EQUIPMENT_STRENGTHENING_VIEW_ID], timeout=15)
            observation = read_selected_equipment_strengthening(context)
            verified, failures = verify_selected_equipment_strengthening(observation, target)
            stable_matches = stable_matches + 1 if verified else 0
            if stable_matches >= 2:
                break
            yield from context.wait_action_settle(1)
        verified = stable_matches >= 2
        attempts.append(
            {
                **candidate,
                "observation": asdict(observation),
                "failures": failures,
            }
        )
        last_failures = failures
        return verified, observation

    for direction in ("right", "left"):
        for scroll_index in range(max(0, int(max_scrolls_per_direction)) + 1):
            frame = context.cur_frame(update=True)
            tokens = context.ocr_tokens_in_shapes(
                EQUIPMENT_STRENGTHENING_VIEW_ID,
                ("装备",),
                padding=4,
                frame_data_url=frame,
                crop=True,
            )
            if alignment_geometry is not None:
                aligned = _predict_equipment_point_from_level_sequence(
                    tokens,
                    snapshot,
                    target,
                    **alignment_geometry,
                )
                if aligned is not None:
                    context.click_frame_point(
                        EQUIPMENT_STRENGTHENING_VIEW_ID,
                        aligned["x"],
                        aligned["y"],
                    )
                    verified, observation = yield from inspect_after_click(
                        {
                            "method": "ordered_level_alignment",
                            "direction": direction,
                            "scroll_index": scroll_index,
                            "click": [aligned["x"], aligned["y"]],
                            "slot_pitch": aligned["slot_pitch"],
                            "score": aligned["score"],
                            "score_margin": aligned["score_margin"],
                            "exact_matches": aligned["exact_matches"],
                            "soft_matches": aligned["soft_matches"],
                        }
                    )
                    if verified:
                        return {
                            "ok": True,
                            "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
                            "target": asdict(target),
                            "observation": asdict(observation),
                            "attempts": attempts,
                        }

            matches = find_text_matches(tokens, expected_level)
            for occurrence, match in enumerate(matches):
                x, y = match.point()
                context.click_frame_point(EQUIPMENT_STRENGTHENING_VIEW_ID, x, y)
                verified, observation = yield from inspect_after_click(
                    {
                        "method": "level_ocr",
                        "direction": direction,
                        "scroll_index": scroll_index,
                        "occurrence": occurrence,
                        "click": [x, y],
                    }
                )
                if verified:
                    return {
                        "ok": True,
                        "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
                        "target": asdict(target),
                        "observation": asdict(observation),
                        "attempts": attempts,
                    }

            # Effects can completely hide a card's level.  The carousel still
            # has a stable five-column geometry, so probe visible card centers
            # and retain the same strict post-click verification.
            ratios: list[float] = []
            if direction == "right" and scroll_index == 0:
                initial_ratio = {
                    1: 0.15,
                    2: 0.34,
                    3: 0.53,
                    4: 0.72,
                    5: 0.90,
                }.get(_PART_INDEX[target.part])
                if initial_ratio is not None:
                    ratios.append(initial_ratio)
            # Reaching this block means every exact-level OCR candidate in the
            # current viewport failed verification, or OCR saw none at all.
            # Effects may hide the target number completely, including on an
            # off-screen page, so exhaust the remaining visible card centers
            # before scrolling onward.  A geometry click is never accepted by
            # itself; the dynamic description/resource fingerprint below must
            # still prove the selected equipment.
            ratios.extend(
                ratio for ratio in _VISIBLE_CARD_X_RATIOS if ratio not in ratios
            )
            for ratio in ratios:
                context.click_shape_center(
                    EQUIPMENT_STRENGTHENING_VIEW_ID,
                    equipment_shape,
                    x_ratio=ratio,
                    y_ratio=0.5,
                )
                verified, observation = yield from inspect_after_click(
                    {
                        "method": "card_geometry",
                        "direction": direction,
                        "scroll_index": scroll_index,
                        "x_ratio": ratio,
                    }
                )
                if verified:
                    return {
                        "ok": True,
                        "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
                        "target": asdict(target),
                        "observation": asdict(observation),
                        "attempts": attempts,
                    }

            if scroll_index >= max(0, int(max_scrolls_per_direction)):
                break
            changed = yield from context.scroll_shape_content(
                equipment_shape,
                direction=direction,
            )
            if not changed:
                break

    detail = "；".join(last_failures) if last_failures else "未找到对应等级卡片"
    raise RuntimeError(
        f"无法选择并验证{target.category}{target.part}（等级 {target.equipment_level}）：{detail}"
    )


def strengthen_selected_equipment_once(
    context: Any,
    *,
    activity_id: str,
    category: str,
    part: str,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
    settle_seconds: float = 1.0,
    poll_attempts: int = 4,
    max_material_cost: int | None = None,
):
    """Click once and persist exact structured before/after Runtime values.

    The click is intentionally never retried.  If post-click verification is
    ambiguous, callers must stop rather than risk spending the resource twice.
    """

    from sqlmodel import Session

    from backend.core.fanxiu.activity.lingzhuang_relationship import (
        record_lingzhuang_strengthening_action_sample,
    )
    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        LingzhuangStrengtheningSnapshot,
        collect_and_store_lingzhuang_strengthening_snapshot,
        load_lingzhuang_strengthening_snapshot,
        read_lingzhuang_strengthening_runtime_snapshot,
    )
    from backend.db import engine

    # 首次消耗可能弹出助力礼包；先经公共弹窗守护确认操作页，
    # 再读取费用及执行一次动作，不把弹窗误当强化按钮消失。
    yield from context.wait_scene_exact([446], timeout=15)
    before_raw = read_lingzhuang_strengthening_runtime_snapshot(
        cross_count=int(cross_count),
        game_task_activity_id=game_task_activity_id,
    )
    before = LingzhuangStrengtheningSnapshot.model_validate(before_raw)
    # Quest removes all equipment-task rows after the final 1.2w tier is done.
    # Continue the cumulative x-axis from the last persisted exact snapshot so
    # later score-round strengthening clicks can still be recorded precisely.
    if before.equipment_current is None or (
        before.equipment_tasks and all(task.claimed for task in before.equipment_tasks)
    ):
        with Session(engine) as session:
            stored_before = load_lingzhuang_strengthening_snapshot(session)
        if stored_before.activity_id != activity_id or stored_before.equipment_current is None:
            raise RuntimeError("装备任务已从游戏列表移除，且没有可续接的累计玄铁快照")
        before.equipment_current = max(int(before.equipment_current or 0), int(stored_before.equipment_current))
        before.equipment_tasks = list(stored_before.equipment_tasks)
        before.task_progress_captured_at = stored_before.task_progress_captured_at
    before_target = resolve_equipment_strengthening_target(before, category, part)
    visible = read_selected_equipment_strengthening(context)
    verified, failures = verify_selected_equipment_strengthening(visible, before_target)
    if not verified:
        yield from context.wait_action_settle(1)
        visible = read_selected_equipment_strengthening(context)
        verified, failures = verify_selected_equipment_strengthening(visible, before_target)
    if not verified:
        raise RuntimeError(f"强化前目标已变化，未点击：{'；'.join(failures)}")

    if max_material_cost is not None:
        cost = visible.resource_required
        if cost is None or cost <= 0 or cost > max_material_cost:
            raise RuntimeError("强化前批次费用超出剩余预算或无法读取，未点击")

    context.click_shape(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        "强化",
        frame_data_url=context.cur_frame(update=True),
    )
    yield from context.wait_action_settle(float(settle_seconds))

    after: LingzhuangStrengtheningSnapshot | None = None
    attempts = max(1, int(poll_attempts))
    for attempt in range(attempts):
        candidate = LingzhuangStrengtheningSnapshot.model_validate(
            read_lingzhuang_strengthening_runtime_snapshot(
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
            )
        )
        candidate_target = resolve_equipment_strengthening_target(candidate, category, part)
        if (
            candidate_target.material_count < before_target.material_count
            and candidate_target.equipment_raw_level > before_target.equipment_raw_level
        ):
            consumed_candidate = before_target.material_count - candidate_target.material_count
            if before.equipment_current is not None and (
                candidate.equipment_current is None
                or candidate.equipment_current < before.equipment_current + consumed_candidate
            ):
                candidate.equipment_current = before.equipment_current + consumed_candidate
                candidate.equipment_tasks = [
                    task.model_copy(update={
                        "progress": candidate.equipment_current,
                        "finished": candidate.equipment_current >= task.target,
                    })
                    for task in before.equipment_tasks
                ]
                candidate.task_progress_captured_at = candidate.captured_at
                if bool((before.evidence or {}).get("equipment_only_phase")):
                    candidate.evidence = {
                        **(candidate.evidence or {}),
                        "equipment_only_phase": True,
                    }
                candidate.complete = bool(
                    candidate.equipment_captured_at
                    and candidate.task_progress_captured_at
                )
            after = candidate
            break
        if attempt + 1 < attempts:
            yield from context.wait_action_settle(0.5)
    if after is None:
        raise RuntimeError(
            f"点击{category}{part}强化后未读取到玄铁与等级同步变化；"
            "为避免重复扣除，已停止且不会自动重试"
        )

    after_target = resolve_equipment_strengthening_target(after, category, part)
    consumed = before_target.material_count - after_target.material_count
    with Session(engine) as session:
        dataset = record_lingzhuang_strengthening_action_sample(
            session,
            activity_id=activity_id,
            before=before,
            after=after,
            part=part,
            category=category,
        )
        stored = collect_and_store_lingzhuang_strengthening_snapshot(
            session,
            activity_id=activity_id,
            observed_snapshot=after,
        )
    if max_material_cost is not None and consumed > max_material_cost:
        raise RuntimeError(
            f"强化实际消耗 {consumed} 超过点击前预算 {max_material_cost}，已保留样本并停止"
        )
    return {
        "ok": True,
        "activity_id": activity_id,
        "category": category,
        "part": part,
        "consumed": consumed,
        "material_before": before_target.material_count,
        "material_after": after_target.material_count,
        "equipment_raw_level_before": before_target.equipment_raw_level,
        "equipment_raw_level_after": after_target.equipment_raw_level,
        "equipment_task_before": before.equipment_current,
        "equipment_task_after": after.equipment_current,
        "score_before": _strengthening_progress(before)[1],
        "score_after": _strengthening_progress(after)[1],
        "cumulative_material": int(dataset.samples[-1].x),
        "stored_captured_at": stored.captured_at,
    }


def choose_equipment_strengthening_batch(context: Any, *, prefer_large: bool):
    """只切换十连选项，按实际费用保留较大或较小批次；不强化、不消费材料。

    用切换前后显示费用验证粒度，不假定进入页面时复选框的初始状态。
    若已是小批次，切换导致费用增加，则恢复原状态并验证费用恢复。
    """
    before = read_selected_equipment_strengthening(context)
    if before.resource_required is None or before.resource_required <= 0:
        raise RuntimeError("切换强化批次前无法读取费用")
    yield from context.wait_click(EQUIPMENT_STRENGTHENING_VIEW_ID, "十连强化")
    yield from context.wait_action_settle(1.0)
    context.clear_frame()
    after = read_selected_equipment_strengthening(context)
    if after.resource_required is None or after.resource_required <= 0:
        raise RuntimeError("切换十连后费用无法读取，未执行强化")
    if ((after.resource_required > before.resource_required) if prefer_large
        else (after.resource_required < before.resource_required)):
        return after
    yield from context.wait_click(EQUIPMENT_STRENGTHENING_VIEW_ID, "十连强化")
    yield from context.wait_action_settle(1.0)
    context.clear_frame()
    restored = read_selected_equipment_strengthening(context)
    if restored.resource_required != before.resource_required:
        raise RuntimeError("十连选项恢复后费用不一致，未执行强化")
    return restored


def reduce_equipment_strengthening_batch(context: Any):
    """基础任务临近目标时改用小批次。"""
    return (yield from choose_equipment_strengthening_batch(context, prefer_large=False))


def strengthening_overshoot_limit(target: int, percent: int = 5) -> int:
    """目标外最多允许的材料数，向下取整；0 表示严格不超。"""
    if target <= 0 or not 0 <= percent <= 100:
        raise ValueError("强化目标须为正数，超量百分比须在 0..100")
    return target * percent // 100


def complete_equipment_strengthening_tasks(
    context: Any,
    *,
    activity_id: str,
    target_progress: int | None = None,
    target_tier: int | None = None,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
    max_clicks: int = 200,
    max_overshoot_percent: int = 5,
):
    """Follow the stable part route until the requested equipment-task target.

    With neither target specified, the final live equipment-task tier is used.
    ``target_tier`` is the one-based live reward tier; ``target_progress`` is
    the absolute material target and takes no implicit tier assumptions.
    A target is selected once per part and kept until that material can no
    longer fund the next visible batch, matching the user's averaging policy.
    Before every click, the visible batch cost must fit target + overshoot cap;
    oversized batches move to the next canonical part. Never spend past the cap
    merely to guarantee completion. Already-complete attempts spend nothing.
    """

    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        LingzhuangStrengtheningSnapshot,
        read_lingzhuang_strengthening_runtime_snapshot,
    )

    yield from ensure_equipment_strengthening(context)
    initial = LingzhuangStrengtheningSnapshot.model_validate(
        read_lingzhuang_strengthening_runtime_snapshot(
            cross_count=int(cross_count),
            game_task_activity_id=game_task_activity_id,
        )
    )
    ordered_tasks = sorted(
        initial.equipment_tasks,
        key=lambda item: (int(item.order), int(item.target)),
    )
    available_targets = [int(item.target) for item in ordered_tasks]
    if not available_targets:
        raise RuntimeError("装备任务进度尚未加载，不能确定完成目标")
    if target_progress is not None and target_tier is not None:
        raise ValueError("装备任务目标只能指定 target_progress 或 target_tier 其中一个")
    final_target = max(available_targets)
    requested_tier: int | None = None
    if target_tier is not None:
        requested_tier = int(target_tier)
        if requested_tier <= 0 or requested_tier > len(ordered_tasks):
            raise ValueError(
                f"装备任务档位必须在 1..{len(ordered_tasks)}：{requested_tier}"
            )
        requested_target = int(ordered_tasks[requested_tier - 1].target)
    else:
        requested_target = (
            final_target if target_progress is None else int(target_progress)
        )
    if requested_target <= 0 or requested_target > final_target:
        raise ValueError(f"装备任务目标必须在 1..{final_target}：{requested_target}")
    if int(initial.equipment_current or 0) >= requested_target:
        return {
            "ok": True,
            "target_tier": requested_tier,
            "target_progress": requested_target,
            "equipment_progress": int(initial.equipment_current or 0),
            "click_count": 0,
            "actions": [],
            "skipped": "already_complete",
        }

    overshoot_limit = strengthening_overshoot_limit(requested_target, max_overshoot_percent)
    route = plan_equipment_strengthening_route(initial)
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for route_target in route:
        if route_target.material_count <= 0:
            skipped.append({"part": route_target.part, "reason": "no_material"})
            continue
        live = LingzhuangStrengtheningSnapshot.model_validate(
            read_lingzhuang_strengthening_runtime_snapshot(
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
            )
        )
        if int(live.equipment_current or 0) >= requested_target:
            break
        try:
            selected = yield from select_equipment_strengthening(
                context,
                route_target.category,
                route_target.part,
                snapshot=live,
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
            )
        except RuntimeError as exc:
            # 定位异常不能转成资源不足并把本期写成已完成。
            raise RuntimeError(f"基础强化选择失败，保留现场：{exc}") from exc
        progress = int(live.equipment_current or 0)
        while len(actions) < max(1, int(max_clicks)):
            yield from context.wait_scene_exact([446], timeout=15)
            observation = read_selected_equipment_strengthening(context)
            current = observation.resource_current
            required = observation.resource_required
            if current is None or required is None or required <= 0:
                raise RuntimeError(
                    f"{route_target.category}{route_target.part}强化资源分子/分母无法可靠读取，已停止"
                )
            if required > requested_target - progress:
                observation = yield from reduce_equipment_strengthening_batch(context)
                current = observation.resource_current
                required = observation.resource_required
                if current is None or required is None or required <= 0:
                    raise RuntimeError("缩小强化批次后费用无法可靠读取，已停止")
            if current < required:
                skipped.append({
                    "part": route_target.part,
                    "category": route_target.category,
                    "reason": "insufficient_for_next_batch",
                    "material_current": current,
                    "material_required": required,
                })
                break
            budget = requested_target + overshoot_limit - progress
            if required > budget:
                skipped.append({
                    "part": route_target.part, "category": route_target.category,
                    "reason": "batch_exceeds_target_budget",
                    "material_required": required, "remaining_budget": budget,
                })
                break  # 尝试后续部位的小额批次，不为凑档无限超支。
            action = yield from strengthen_selected_equipment_once(
                context,
                activity_id=activity_id,
                category=route_target.category,
                part=route_target.part,
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
                max_material_cost=budget,
            )
            actions.append(action)
            progress = int(action.get("equipment_task_after") or 0)
            if int(action.get("equipment_task_after") or 0) >= requested_target:
                return {
                    "ok": True,
                    "target_tier": requested_tier,
                    "target_progress": requested_target,
                    "equipment_progress": int(action["equipment_task_after"]),
                    "cumulative_material": int(action["cumulative_material"]),
                    "click_count": len(actions),
                    "route": [asdict(item) for item in route],
                    "actions": actions,
                    "skipped": skipped,
                    "last_selection": selected,
                }
        if len(actions) >= max(1, int(max_clicks)):
            raise RuntimeError(f"达到强化点击安全上限 {max_clicks}，已停止")

    final = LingzhuangStrengtheningSnapshot.model_validate(
        read_lingzhuang_strengthening_runtime_snapshot(
            cross_count=int(cross_count),
            game_task_activity_id=game_task_activity_id,
        )
    )
    if int(final.equipment_current or 0) < requested_target:
        equipment_progress = int(final.equipment_current or 0)
        cumulative_material = (
            int(actions[-1]["cumulative_material"])
            if actions and actions[-1].get("cumulative_material") is not None
            else None
        )
        raise EquipmentStrengtheningResourceExhausted(
            f"当前可用批次无法在超量上限内继续，装备任务仅到 {equipment_progress} / {requested_target}",
            target_progress=requested_target,
            equipment_progress=equipment_progress,
            cumulative_material=cumulative_material,
        )
    return {
        "ok": True,
        "target_tier": requested_tier,
        "target_progress": requested_target,
        "equipment_progress": int(final.equipment_current or 0),
        "click_count": len(actions),
        "route": [asdict(item) for item in route],
        "actions": actions,
        "skipped": skipped,
    }


def complete_lingzhuang_score_round(
    context: Any, *, activity_id: str, target_round: int = 1,
    cross_count: int = 16, game_task_activity_id: int | None = None,
    max_clicks: int = 200, min_material_to_select: int = 10,
):
    """完成一个本期积分整轮；调用方负责整轮预算及领奖，积分为累计值。"""
    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        read_lingzhuang_strengthening_runtime_snapshot,
    )
    yield from ensure_equipment_strengthening(context)
    initial = read_lingzhuang_strengthening_runtime_snapshot(
        cross_count=cross_count, game_task_activity_id=game_task_activity_id,
    )
    rounds = {int(row["round"]): int(row["target"]) for row in initial["score_rounds"]}
    if target_round not in rounds:
        raise ValueError(f"积分轮次不存在：{target_round}")
    target_score = sum(value for number, value in rounds.items() if number <= target_round)
    before_score = int(_strengthening_progress(initial)[1] or 0)
    actions, skipped = [], []
    score = before_score
    if score >= target_score:
        return dict(ok=True, target_round=target_round, target_score=target_score,
                    score_progress=score, consumed=0, score_gained=0, actions=[], skipped="already_complete")
    for target in plan_equipment_strengthening_route(initial):
        if target.material_count < min_material_to_select:
            continue
        live = read_lingzhuang_strengthening_runtime_snapshot(
            cross_count=cross_count, game_task_activity_id=game_task_activity_id,
        )
        # 选择失败属于定位异常，不能伪装成资源不足后继续烧其它部位。
        yield from select_equipment_strengthening(context, target.category, target.part,
            snapshot=live, cross_count=cross_count, game_task_activity_id=game_task_activity_id)
        # 基础任务末档会切到单次；积分轮重新使用十连，仍由显示费用验证状态。
        yield from choose_equipment_strengthening_batch(context, prefer_large=True)
        while len(actions) < max_clicks:
            yield from context.wait_scene_exact([446], timeout=15)
            observation = read_selected_equipment_strengthening(context)
            if observation.resource_current is None or not observation.resource_required:
                raise RuntimeError("积分强化费用无法可靠读取，未点击")
            if observation.resource_current < observation.resource_required:
                observation = yield from reduce_equipment_strengthening_batch(context)
                if observation.resource_current is None or not observation.resource_required:
                    raise RuntimeError("积分强化缩小批次后费用无法读取，未点击")
                if observation.resource_current < observation.resource_required:
                    skipped.append(dict(part=target.part, reason="insufficient_for_next_batch"))
                    break
            action = yield from strengthen_selected_equipment_once(context,
                activity_id=activity_id, category=target.category, part=target.part,
                cross_count=cross_count, game_task_activity_id=game_task_activity_id)
            actions.append(action)
            if action.get("score_after") is None:
                raise RuntimeError("强化后灵装积分缺失，停止以保留现场")
            score = int(action["score_after"])
            if score >= target_score:
                return dict(ok=True, target_round=target_round, target_score=target_score,
                            score_progress=score, score_gained=score-before_score,
                            consumed=sum(row["consumed"] for row in actions), actions=actions, skipped=skipped)
        if len(actions) >= max_clicks:
            raise RuntimeError(f"达到积分强化点击安全上限 {max_clicks}")
    return dict(ok=False, outcome="insufficient_resource", target_round=target_round,
                target_score=target_score, score_progress=score, score_gained=score-before_score,
                consumed=sum(row["consumed"] for row in actions), actions=actions, skipped=skipped)
