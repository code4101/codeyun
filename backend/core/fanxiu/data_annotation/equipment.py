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
from pyxllib.autogui import frame_size as _runtime_frame_size


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
    "宝冠": ("宝冠",),
    "羽巾": ("羽巾",),
    "华履": ("华履",),
    "锦带": ("锦带",),
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
    runtime: Any,
    *,
    frame_data_url: str | None = None,
) -> EquipmentStrengtheningObservation:
    """Read the selected equipment only from #446 description/resource OCR."""

    frame = frame_data_url or runtime.cur_frame(update=True)
    description_text = str(
        runtime.ocr_text_in_shapes(
            EQUIPMENT_STRENGTHENING_VIEW_ID,
            ("描述",),
            padding=4,
            frame_data_url=frame,
        )
        or ""
    )
    description_tokens = runtime.ocr_tokens_in_shapes(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        ("描述",),
        padding=4,
        frame_data_url=frame,
    )
    resource_text = str(
        runtime.ocr_text_in_shapes(
            EQUIPMENT_STRENGTHENING_VIEW_ID,
            ("资源",),
            padding=4,
            frame_data_url=frame,
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
    runtime: Any,
    *,
    max_attempts: int,
    retry_seconds: float,
) -> dict[str, Any]:
    last_tokens: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        frame = runtime.cur_frame(update=True)
        last_tokens = runtime.ocr_tokens_in_shapes(
            WORLD_VIEW_ID,
            ("下方菜单",),
            padding=4,
            frame_data_url=frame,
        )
        token = _find_world_equipment_token(last_tokens)
        if token is not None:
            x, y = _world_equipment_click_point(token)
            runtime.click_frame_point(WORLD_VIEW_ID, x, y)
            return {
                "attempt": attempt,
                "token": token,
                "click": [x, y],
            }
        if attempt < max(1, int(max_attempts)):
            yield from runtime.wait_action_settle(retry_seconds)
    raise RuntimeError(
        "#34[下方菜单] 未识别到「装备」「装」或「备」，"
        f"OCR={last_tokens}"
    )


def _click_strengthening_menu(
    runtime: Any,
    *,
    max_attempts: int,
    retry_seconds: float,
) -> dict[str, Any]:
    last_tokens: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        frame = runtime.cur_frame(update=True)
        last_tokens = runtime.ocr_tokens_in_shapes(
            EQUIPMENT_VIEW_ID,
            ("菜单",),
            padding=4,
            frame_data_url=frame,
            crop=True,
            options=_STRENGTHENING_OCR_OPTIONS,
        )
        point = _find_strengthening_center(last_tokens)
        if point is not None:
            runtime.click_frame_point(EQUIPMENT_VIEW_ID, *point)
            return {
                "attempt": attempt,
                "tokens": last_tokens,
                "click": list(point),
            }
        if attempt < max(1, int(max_attempts)):
            yield from runtime.wait_action_settle(retry_seconds)
    raise RuntimeError(
        "#445[菜单] 局部 OCR 未识别到竖排相邻的「强」「化」，"
        f"OCR={last_tokens}"
    )


def ensure_equipment_strengthening(
    runtime: Any,
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

    scene_id, _score, _frame = runtime.current_scene(
        (EQUIPMENT_STRENGTHENING_VIEW_ID, EQUIPMENT_VIEW_ID, WORLD_VIEW_ID),
        update=True,
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
            yield from runtime.goto_view(WORLD_VIEW_ID)
        yield from runtime.wait_view(
            WORLD_VIEW_ID,
            timeout=transition_timeout,
            label="进入装备强化：等待世界 #34",
        )
        equipment_action = yield from _click_world_equipment(
            runtime,
            max_attempts=world_ocr_attempts,
            retry_seconds=retry_seconds,
        )
        actions.append({"step": "world_to_equipment", **equipment_action})
        yield from runtime.wait_view(
            EQUIPMENT_VIEW_ID,
            timeout=transition_timeout,
            label="进入装备强化：等待装备页 #445",
        )

    strengthening_action = yield from _click_strengthening_menu(
        runtime,
        max_attempts=strengthening_ocr_attempts,
        retry_seconds=retry_seconds,
    )
    actions.append({"step": "equipment_to_strengthening", **strengthening_action})
    yield from runtime.wait_view(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        timeout=transition_timeout,
        label="进入装备强化：等待强化页 #446",
    )
    return {
        "ok": True,
        "changed": True,
        "view_id": EQUIPMENT_STRENGTHENING_VIEW_ID,
        "actions": actions,
    }


def select_equipment_strengthening(
    runtime: Any,
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

    Category must be selected first because the game resets the carousel to its
    first equipment whenever 初灵/洞玄 changes.  Card OCR is only a candidate
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
    yield from ensure_equipment_strengthening(runtime)

    runtime.click_ocr_text(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        target.category,
        in_shapes=("类别",),
        padding=4,
    )
    yield from runtime.wait_action_settle(settle_seconds)

    target_view = runtime.view(EQUIPMENT_STRENGTHENING_VIEW_ID)
    equipment_shape = runtime.resolve_shape_selector(target_view, "装备")
    alignment_geometry: dict[str, float] | None = None
    try:
        first_slot_shape = runtime.resolve_shape_selector(target_view, "装备/框1")
        second_slot_shape = runtime.resolve_shape_selector(target_view, "装备/框2")
        frame_width, frame_height = _runtime_frame_size(target_view.raw)
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
        yield from runtime.wait_action_settle(settle_seconds)
        observation = read_selected_equipment_strengthening(runtime)
        verified, failures = verify_selected_equipment_strengthening(
            observation,
            target,
        )
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
            frame = runtime.cur_frame(update=True)
            tokens = runtime.ocr_tokens_in_shapes(
                EQUIPMENT_STRENGTHENING_VIEW_ID,
                ("装备",),
                padding=4,
                frame_data_url=frame,
            )
            if alignment_geometry is not None:
                aligned = _predict_equipment_point_from_level_sequence(
                    tokens,
                    snapshot,
                    target,
                    **alignment_geometry,
                )
                if aligned is not None:
                    runtime.click_frame_point(
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
                runtime.click_frame_point(EQUIPMENT_STRENGTHENING_VIEW_ID, x, y)
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
                runtime.click_shape_center(
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
            changed = yield from runtime.scroll_shape_content(
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
    runtime: Any,
    *,
    activity_id: str,
    category: str,
    part: str,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
    settle_seconds: float = 1.0,
    poll_attempts: int = 4,
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

    before_raw = read_lingzhuang_strengthening_runtime_snapshot(
        cross_count=int(cross_count),
        game_task_activity_id=game_task_activity_id,
    )
    before = LingzhuangStrengtheningSnapshot.model_validate(before_raw)
    # Quest removes all equipment-task rows after the final 1.2w tier is done.
    # Continue the cumulative x-axis from the last persisted exact snapshot so
    # later score-round strengthening clicks can still be recorded precisely.
    if before.equipment_current is None:
        with Session(engine) as session:
            stored_before = load_lingzhuang_strengthening_snapshot(session)
        if stored_before.activity_id != activity_id or stored_before.equipment_current is None:
            raise RuntimeError("装备任务已从游戏列表移除，且没有可续接的累计玄铁快照")
        before.equipment_current = int(stored_before.equipment_current)
        before.equipment_tasks = list(stored_before.equipment_tasks)
        before.task_progress_captured_at = stored_before.task_progress_captured_at
    before_target = resolve_equipment_strengthening_target(before, category, part)

    runtime.click_shape(
        EQUIPMENT_STRENGTHENING_VIEW_ID,
        "强化",
        frame_data_url=runtime.cur_frame(update=True),
    )
    yield from runtime.wait_action_settle(float(settle_seconds))

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
                candidate.complete = bool(
                    candidate.equipment_captured_at
                    and candidate.task_progress_captured_at
                )
            after = candidate
            break
        if attempt + 1 < attempts:
            yield from runtime.wait_action_settle(0.5)
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


def complete_equipment_strengthening_tasks(
    runtime: Any,
    *,
    activity_id: str,
    target_progress: int | None = None,
    target_tier: int | None = None,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
    max_clicks: int = 200,
):
    """Follow the stable part route until the requested equipment-task target.

    With neither target specified, the final live equipment-task tier is used.
    ``target_tier`` is the one-based live reward tier; ``target_progress`` is
    the absolute material target and takes no implicit tier assumptions.
    A target is selected once per part and kept until that material can no
    longer fund the next visible batch, matching the user's averaging policy.
    """

    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        LingzhuangStrengtheningSnapshot,
        read_lingzhuang_strengthening_runtime_snapshot,
    )

    yield from ensure_equipment_strengthening(runtime)
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
                runtime,
                route_target.category,
                route_target.part,
                snapshot=live,
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
            )
        except RuntimeError as exc:
            # Selection itself spends nothing. A depleted/off-screen part must
            # not block the remaining canonical route; post-click ambiguity is
            # still handled inside strengthen_selected_equipment_once and is
            # intentionally fatal to prevent a duplicate spend.
            skipped.append({
                "part": route_target.part,
                "category": route_target.category,
                "reason": "selection_failed",
                "detail": str(exc),
            })
            continue
        while len(actions) < max(1, int(max_clicks)):
            observation = read_selected_equipment_strengthening(runtime)
            current = observation.resource_current
            required = observation.resource_required
            if current is None or required is None or required <= 0:
                raise RuntimeError(
                    f"{route_target.category}{route_target.part}强化资源分子/分母无法可靠读取，已停止"
                )
            if current < required:
                skipped.append({
                    "part": route_target.part,
                    "category": route_target.category,
                    "reason": "insufficient_for_next_batch",
                    "material_current": current,
                    "material_required": required,
                })
                break
            action = yield from strengthen_selected_equipment_once(
                runtime,
                activity_id=activity_id,
                category=route_target.category,
                part=route_target.part,
                cross_count=int(cross_count),
                game_task_activity_id=game_task_activity_id,
            )
            actions.append(action)
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
            f"路线可用玄铁耗尽，装备任务仅到 {equipment_progress} / {requested_target}",
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
    runtime: Any,
    *,
    activity_id: str,
    target_round: int = 1,
    cross_count: int = 16,
    max_clicks: int = 200,
    min_material_to_select: int = 10,
):
    """Strengthen along the stable route until one score round is complete."""

    from backend.core.fanxiu.activity.lingzhuang_strengthening import (
        LingzhuangStrengtheningSnapshot,
        read_lingzhuang_strengthening_runtime_snapshot,
    )

    yield from ensure_equipment_strengthening(runtime)
    initial = LingzhuangStrengtheningSnapshot.model_validate(
        read_lingzhuang_strengthening_runtime_snapshot(cross_count=int(cross_count))
    )
    target_by_round = {int(item.round): int(item.target) for item in initial.score_rounds}
    requested_round = int(target_round)
    if requested_round not in target_by_round:
        raise ValueError(f"积分轮次不存在：{requested_round}")
    requested_score = target_by_round[requested_round]

    def reached(snapshot: LingzhuangStrengtheningSnapshot) -> bool:
        live_round = int(snapshot.score_round or 0)
        live_score = int(snapshot.score_current or 0)
        return live_round > requested_round or (
            live_round == requested_round and live_score >= requested_score
        )

    if reached(initial):
        return {
            "ok": True,
            "target_round": requested_round,
            "target_score": requested_score,
            "score_round": int(initial.score_round or 0),
            "score_progress": int(initial.score_current or 0),
            "click_count": 0,
            "actions": [],
            "skipped": "already_complete",
        }

    route = plan_equipment_strengthening_route(initial)
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for route_target in route:
        if route_target.material_count < max(1, int(min_material_to_select)):
            skipped.append({
                "part": route_target.part,
                "category": route_target.category,
                "reason": "below_minimum_material_to_select",
                "material_current": route_target.material_count,
            })
            continue
        live = LingzhuangStrengtheningSnapshot.model_validate(
            read_lingzhuang_strengthening_runtime_snapshot(cross_count=int(cross_count))
        )
        if reached(live):
            break
        try:
            selected = yield from select_equipment_strengthening(
                runtime,
                route_target.category,
                route_target.part,
                snapshot=live,
                cross_count=int(cross_count),
            )
        except RuntimeError as exc:
            skipped.append({
                "part": route_target.part,
                "category": route_target.category,
                "reason": "selection_failed",
                "detail": str(exc),
            })
            continue
        while len(actions) < max(1, int(max_clicks)):
            observation = read_selected_equipment_strengthening(runtime)
            current = observation.resource_current
            required = observation.resource_required
            if current is None or required is None or required <= 0:
                raise RuntimeError(
                    f"{route_target.category}{route_target.part}强化资源分子/分母无法可靠读取，已停止"
                )
            if current < required:
                skipped.append({
                    "part": route_target.part,
                    "category": route_target.category,
                    "reason": "insufficient_for_next_batch",
                    "material_current": current,
                    "material_required": required,
                })
                break
            action = yield from strengthen_selected_equipment_once(
                runtime,
                activity_id=activity_id,
                category=route_target.category,
                part=route_target.part,
                cross_count=int(cross_count),
            )
            actions.append(action)
            action_round = int(action.get("score_round_after") or requested_round)
            action_score = int(action.get("score_after") or 0)
            if action_round > requested_round or (
                action_round == requested_round and action_score >= requested_score
            ):
                return {
                    "ok": True,
                    "target_round": requested_round,
                    "target_score": requested_score,
                    "score_round": action_round,
                    "score_progress": action_score,
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
        read_lingzhuang_strengthening_runtime_snapshot(cross_count=int(cross_count))
    )
    if not reached(final):
        raise RuntimeError(
            f"路线可用玄铁耗尽，积分仅到第 {int(final.score_round or 0)} 轮 "
            f"{int(final.score_current or 0)} / {requested_score}"
        )
    return {
        "ok": True,
        "target_round": requested_round,
        "target_score": requested_score,
        "score_round": int(final.score_round or 0),
        "score_progress": int(final.score_current or 0),
        "click_count": len(actions),
        "route": [asdict(item) for item in route],
        "actions": actions,
        "skipped": skipped,
    }
