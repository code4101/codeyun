from __future__ import annotations

"""Fail-closed adapter for one persisted-note storage-bag choice box."""

from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from backend.core.fanxiu.instrumentation.storage_bag_partner import (
    read_storage_bag_partner_snapshot,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import (
    parse_storage_bag_choice_note,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    STORAGE_BAG_SCENE,
    plan_current_random_box_click,
)
from backend.core.fanxiu.runtime_gui import (
    StorageBagItemClickPlan,
    normalize_ocr_name,
    ocr_name_similarity,
    plan_storage_bag_scroll,
    verify_storage_bag_item_detail,
)


CHOICE_BOX_SCENE = 586
CHOICE_DETAIL_SCENE = 587


class StorageBagChoiceBoxBlocked(RuntimeError):
    """The choice-box action cannot be uniquely proven safe."""


@dataclass(frozen=True)
class StorageBagChoiceBoxRequest:
    base_id: int
    instance_id: str
    name: str
    quantity: int
    note: str
    open_quantity: int | None = None


@dataclass(frozen=True)
class StorageBagChoiceReward:
    slot: int
    base_id: int
    name: str
    count_per_box: int
    is_partner: bool = False
    partner_reason: str = ""
    linked_partner_id: int | None = None


@dataclass(frozen=True)
class StorageBagChoiceCandidateEvidence:
    slot: int
    observed_name: str
    available: bool


@dataclass(frozen=True)
class StorageBagChoiceBoxDelta:
    opened_count: int
    reward_base_id: int
    reward_quantity: int
    before_fingerprint: str
    after_fingerprint: str


@dataclass(frozen=True)
class StorageBagChoiceBoxExecution:
    request: StorageBagChoiceBoxRequest
    selected_reward: StorageBagChoiceReward
    delta: StorageBagChoiceBoxDelta
    detail_observed_name: str
    detail_similarity: float
    scroll_count: int
    partner_outcome: StorageBagPartnerOutcomeProof | None = None


@dataclass(frozen=True)
class StorageBagPartnerOutcomeProof:
    target_partner_id: int
    outcome: str
    before_fingerprint: str
    after_fingerprint: str
    explanation: str = ""
    fragment_base_id: int | None = None
    fragment_quantity: int = 0


SnapshotReader = Callable[[], Mapping[str, Any]]
ClickPlanner = Callable[[Any, Mapping[str, Any], StorageBagChoiceBoxRequest], StorageBagItemClickPlan]
AssetValidator = Callable[[Any, Sequence[int]], None]
TargetDetailScanner = Callable[
    [Any, StorageBagChoiceReward],
    Generator[Any, Any, str],
]
SelectionVerifier = Callable[[Any, int, int], bool]
CountReader = Callable[[Any], int]
AvailabilityReader = Callable[[Any, Sequence[int]], Mapping[int, bool]]
VisibleSlotReader = Callable[[Any], Sequence[int]]
PartnerSnapshotReader = Callable[[], Mapping[str, Any]]
PartnerOutcomeVerifier = Callable[
    [
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        StorageBagChoiceBoxRequest,
        StorageBagChoiceReward,
        Mapping[str, Mapping[str, Any]],
    ],
    StorageBagPartnerOutcomeProof,
]


def _snapshot_identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    try:
        identity = (
            int(evidence.get("pid") or 0),
            int(evidence.get("process_start_ticks") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise StorageBagChoiceBoxBlocked("储物袋 Runtime 进程身份无效") from exc
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
        or not str(snapshot.get("fingerprint") or "")
        or min(identity) <= 0
    ):
        raise StorageBagChoiceBoxBlocked("储物袋 Runtime 不完整、来源错误或缺少进程/指纹证据")
    return identity


def _runtime_instances(snapshot: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    instances: dict[str, tuple[int, int]] = {}
    for row in snapshot.get("items") or []:
        if not isinstance(row, Mapping) or row.get("is_padding"):
            continue
        instance_id = str(row.get("instance_id") or "")
        base_id = int(row.get("base_id") or 0)
        quantity = int(row.get("num") or 0)
        if not instance_id or base_id <= 0 or quantity < 0 or instance_id in instances:
            raise StorageBagChoiceBoxBlocked("储物袋 Runtime 含无效或重复实例")
        instances[instance_id] = (base_id, quantity)
    return instances


def requested_choice_box_open_quantity(
    request: StorageBagChoiceBoxRequest,
) -> int:
    """Keep inventory identity separate from the minimal quantity to consume."""

    target = (
        request.quantity
        if request.open_quantity is None
        else int(request.open_quantity)
    )
    if request.quantity <= 0 or not 1 <= target <= request.quantity:
        raise StorageBagChoiceBoxBlocked(
            "自选匣目标开启数量必须位于 1..Runtime持有数量"
        )
    return target


def _partner_metadata(
    card: Mapping[str, Any], raw_reward: Mapping[str, Any]
) -> tuple[bool, str, int | None]:
    partner_detail = raw_reward.get("partner_detail") or card.get("partner_detail")
    detail_partner_id = 0
    if isinstance(partner_detail, Mapping):
        try:
            detail_partner_id = int(partner_detail.get("id") or 0)
        except (TypeError, ValueError):
            detail_partner_id = 0
    try:
        linked_partner_id = int(
            raw_reward.get("linked_partner_id")
            or card.get("linked_partner_id")
            or detail_partner_id
            or 0
        )
    except (TypeError, ValueError):
        linked_partner_id = 0
    resolved_partner_id = linked_partner_id if linked_partner_id > 0 else None
    if raw_reward.get("is_partner") is True or card.get("is_partner") is True:
        return True, "Catalog 显式 is_partner", resolved_partner_id
    kind = str(
        raw_reward.get("candidate_kind")
        or raw_reward.get("business_type")
        or card.get("candidate_kind")
        or card.get("business_type")
        or ""
    ).strip().lower()
    if kind in {"partner", "xianlv", "仙侣"}:
        return True, f"Catalog 候选类型 {kind}", resolved_partner_id
    if linked_partner_id > 0:
        return True, f"Catalog linked_partner_id={linked_partner_id}", linked_partner_id
    if isinstance(partner_detail, Mapping) and partner_detail:
        return True, "Catalog partner_detail", resolved_partner_id
    return False, "Catalog 未证明该候选属于仙侣", None


def choice_rewards_from_catalog(
    box_card: Mapping[str, Any],
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[StorageBagChoiceReward, ...]:
    rewards: list[StorageBagChoiceReward] = []
    for slot, raw in enumerate(box_card.get("optional_gift_rewards") or [], start=1):
        if not isinstance(raw, Mapping):
            raise StorageBagChoiceBoxBlocked("自选匣 Catalog 候选行无效")
        base_id = int(raw.get("id") or 0)
        name = str(raw.get("name") or "").strip()
        count = int(raw.get("count") or 0)
        if base_id <= 0 or not name or count <= 0:
            raise StorageBagChoiceBoxBlocked("自选匣 Catalog 候选缺少 id/名称/单箱数量")
        card = (catalog_cards_by_id or {}).get(str(base_id)) or {}
        is_partner, partner_reason, linked_partner_id = _partner_metadata(card, raw)
        rewards.append(
            StorageBagChoiceReward(
                slot=slot,
                base_id=base_id,
                name=name,
                count_per_box=count,
                is_partner=is_partner,
                partner_reason=partner_reason,
                linked_partner_id=linked_partner_id,
            )
        )
    if not rewards:
        raise StorageBagChoiceBoxBlocked("自选匣 Catalog 没有有序候选奖励")
    if len({reward.base_id for reward in rewards}) != len(rewards):
        raise StorageBagChoiceBoxBlocked("自选匣 Catalog 候选奖励 ID 重复")
    return tuple(rewards)


def parse_persisted_choice_note(note: str) -> tuple[str, str]:
    """Accept only the documented DB-note grammar, without prose substrings."""

    compact = re.sub(r"\s+", "", str(note or ""))
    if compact == "选第1个可以选的仙侣":
        return "first_available_partner", ""
    try:
        kind, value = parse_storage_bag_choice_note(compact)
    except ValueError as exc:
        raise StorageBagChoiceBoxBlocked(str(exc)) from exc
    if kind == "first_available" and not re.fullmatch(
        r"选?第(?:1|一)个(?:可以|可)选(?:的)?", compact
    ):
        raise StorageBagChoiceBoxBlocked("自选匣备注含未授权的附加文本")
    if kind == "named" and not re.fullmatch(r"选(?:择)?.+", compact):
        raise StorageBagChoiceBoxBlocked("自选匣命名选择备注不符合受控语法")
    return kind, value


def _shape_box(shape: Mapping[str, Any]) -> tuple[float, float, float, float]:
    try:
        return tuple(float(shape[key]) for key in ("x", "y", "w", "h"))  # type: ignore[return-value]
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageBagChoiceBoxBlocked("#586 候选 shape 几何无效") from exc


def discover_visible_choice_slots(runtime: Any, *, maximum_slots: int = 12) -> tuple[int, ...]:
    """Discover one contiguous formally annotated #586 candidate prefix."""

    slots: list[int] = []
    for slot in range(1, max(1, int(maximum_slots)) + 1):
        try:
            runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}")
        except (KeyError, RuntimeError, ValueError):
            break
        slots.append(slot)
    if not slots:
        raise StorageBagChoiceBoxBlocked("#586 没有正式候选 slot shape")
    return tuple(slots)


def validate_choice_box_asset_contract(runtime: Any, visible_slots: Sequence[int]) -> None:
    """Require separate semantics before any choice-box GUI action."""

    for title in ("详情标题", "当前数量", "增加数量", "确定"):
        runtime.shape(CHOICE_BOX_SCENE, title)
    for title in ("详情标题", "右侧暗幕返回"):
        runtime.shape(CHOICE_DETAIL_SCENE, title)
    expected_slots = tuple(range(1, len(visible_slots) + 1))
    if tuple(visible_slots) != expected_slots:
        raise StorageBagChoiceBoxBlocked("#586 可见候选 slots 必须是从1开始的连续前缀")
    for slot in visible_slots:
        container = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}").raw
        open_detail = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/打开详情").raw
        checkbox = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/右上选择框").raw
        available = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/可选状态").raw
        selected = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/绿色勾选").raw
        cx, cy, cw, ch = _shape_box(container)
        bx, by, bw, bh = _shape_box(checkbox)
        for child in (open_detail, checkbox, available, selected):
            x, y, width, height = _shape_box(child)
            if x < cx or y < cy or x + width > cx + cw or y + height > cy + ch:
                raise StorageBagChoiceBoxBlocked(f"#586 候选{slot} 子 shape 超出整卡容器")
        if bx + bw / 2 < cx + cw * 0.62 or by + bh / 2 > cy + ch * 0.45:
            raise StorageBagChoiceBoxBlocked(f"#586 候选{slot} 选择框不是整卡右上区域")


def _ordered_text(tokens: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(tokens, key=lambda token: (float(token.get("y") or 0), float(token.get("x") or 0)))
    return "".join(str(token.get("text") or "").strip() for token in ordered)


def green_pixel_ratio(frame) -> float:
    """Return a generic saturated-green ratio for one annotated ROI (BGR)."""

    import cv2
    import numpy as np

    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return 0.0
    bgr = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array((35, 80, 70), dtype=np.uint8),
        np.array((95, 255, 255), dtype=np.uint8),
    )
    return round(float((mask > 0).mean()), 6)


def luminance_availability(
    mean_luminance: float,
    dark_fraction: float,
    *,
    minimum_mean: float = 120.0,
    maximum_dark_fraction: float = 0.25,
) -> bool:
    """Classify one card-body ROI using conservative real-frame boundaries."""

    return (
        float(mean_luminance) >= minimum_mean
        and 0.0 <= float(dark_fraction) <= maximum_dark_fraction
    )


def read_choice_availability(runtime: Any, visible_slots: Sequence[int]) -> dict[int, bool]:
    """Read all #586 card-body ROIs from one fresh frame."""

    import cv2
    import numpy as np

    frame_data_url = runtime.cur_frame(update=True)
    raw = runtime.runner._decode_frame_data_url(frame_data_url)
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise StorageBagChoiceBoxBlocked("#586 可选状态帧解码失败")
    height, width = frame.shape[:2]
    result: dict[int, bool] = {}
    for slot in visible_slots:
        shape = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/可选状态").raw
        x = max(0, round(width * float(shape.get("x") or 0)))
        y = max(0, round(height * float(shape.get("y") or 0)))
        right = min(width, round(width * (float(shape.get("x") or 0) + float(shape.get("w") or 0))))
        bottom = min(height, round(height * (float(shape.get("y") or 0) + float(shape.get("h") or 0))))
        if right <= x or bottom <= y:
            raise StorageBagChoiceBoxBlocked(f"#586 候选{slot}/可选状态 ROI 无效")
        gray = cv2.cvtColor(frame[y:bottom, x:right], cv2.COLOR_BGR2GRAY)
        mean_luminance = float(gray.mean())
        dark_fraction = float((gray < 80).mean())
        result[slot] = luminance_availability(mean_luminance, dark_fraction)
    return result


def unique_green_selection(
    ratios: Mapping[int, float],
    selected_slot: int,
    *,
    selected_minimum: float = 0.08,
    unselected_maximum: float = 0.03,
    minimum_margin: float = 0.05,
) -> bool:
    """Prove one selected slot from independent green-ratio observations."""

    if selected_slot not in ratios or not ratios:
        return False
    selected = float(ratios[selected_slot])
    others = [float(value) for slot, value in ratios.items() if slot != selected_slot]
    strongest_other = max(others, default=0.0)
    return (
        selected >= selected_minimum
        and all(value <= unselected_maximum for value in others)
        and selected - strongest_other >= minimum_margin
    )


def verify_unique_choice_selection(runtime: Any, selected_slot: int, candidate_count: int) -> bool:
    import cv2
    import numpy as np

    frame_data_url = runtime.cur_frame(update=True)
    raw = runtime.runner._decode_frame_data_url(frame_data_url)
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return False
    height, width = frame.shape[:2]
    ratios: dict[int, float] = {}
    for slot in range(1, candidate_count + 1):
        shape = runtime.shape(CHOICE_BOX_SCENE, f"候选{slot}/绿色勾选").raw
        x = max(0, round(width * float(shape.get("x") or 0)))
        y = max(0, round(height * float(shape.get("y") or 0)))
        right = min(width, round(width * (float(shape.get("x") or 0) + float(shape.get("w") or 0))))
        bottom = min(height, round(height * (float(shape.get("y") or 0) + float(shape.get("h") or 0))))
        if right <= x or bottom <= y:
            return False
        ratios[slot] = green_pixel_ratio(frame[y:bottom, x:right])
    return unique_green_selection(ratios, selected_slot)


def read_choice_count(runtime: Any) -> int:
    frame = runtime.cur_frame(update=True)
    tokens = runtime.ocr_tokens_in_shapes(
        CHOICE_BOX_SCENE,
        ("当前数量",),
        padding=4,
        frame_data_url=frame,
        crop=True,
    )
    values = parse_ocr_values(_ordered_text(tokens))
    if values is None or len(values) != 1 or values[0] <= 0:
        raise StorageBagChoiceBoxBlocked("#586 当前数量 OCR 不是唯一正整数")
    return int(values[0])


def choose_reward_from_note(
    note: str,
    rewards: Sequence[StorageBagChoiceReward],
    availability: Mapping[int, bool],
    visible_slots: Sequence[int],
) -> StorageBagChoiceReward:
    kind, value = parse_persisted_choice_note(note)
    visible_slots = tuple(int(slot) for slot in visible_slots)
    if visible_slots != tuple(range(1, len(visible_slots) + 1)):
        raise StorageBagChoiceBoxBlocked("#586 可见候选 slots 不是连续前缀")
    if set(availability) != set(visible_slots):
        raise StorageBagChoiceBoxBlocked("#586 可见候选可选态证据缺失或多出")
    reward_by_slot = {reward.slot: reward for reward in rewards}
    if len(reward_by_slot) != len(rewards):
        raise StorageBagChoiceBoxBlocked("Catalog 候选 slot 重复")

    def require_visible(reward: StorageBagChoiceReward) -> None:
        if reward.slot not in visible_slots:
            raise StorageBagChoiceBoxBlocked("备注目标超出当前正式标注的可见候选范围")

    if kind == "named":
        key = normalize_ocr_name(value)
        matches = [reward for reward in rewards if normalize_ocr_name(reward.name) == key]
        if len(matches) != 1:
            raise StorageBagChoiceBoxBlocked("备注目标未唯一精确映射到 Catalog 候选名称")
        selected = matches[0]
        require_visible(selected)
        if availability[selected.slot] is not True:
            raise StorageBagChoiceBoxBlocked("备注指定候选当前没有独立可选证据")
        return selected
    if kind == "first_available_partner":
        proven_partners = [reward for reward in rewards if reward.is_partner]
        if not proven_partners:
            raise StorageBagChoiceBoxBlocked("Catalog 未权威证明任何候选属于仙侣")
        available_partners = [
            reward
            for reward in proven_partners
            if reward.slot in visible_slots and availability[reward.slot] is True
        ]
        if not available_partners:
            if any(reward.slot not in visible_slots for reward in proven_partners):
                raise StorageBagChoiceBoxBlocked("当前可见范围没有可选仙侣，后续候选尚无滚动适配")
            raise StorageBagChoiceBoxBlocked("当前没有带独立可选证据的仙侣候选")
        return available_partners[0]
    available = [
        reward_by_slot[slot]
        for slot in visible_slots
        if slot in reward_by_slot and availability[slot] is True
    ]
    if not available:
        raise StorageBagChoiceBoxBlocked("#586 没有带独立可选证据的候选")
    return available[0]


def validate_target_detail_identity(
    rewards: Sequence[StorageBagChoiceReward],
    selected: StorageBagChoiceReward,
    observed: str,
) -> None:
    """Prove the one policy-selected target against every Catalog candidate."""

    exact_slots = [
        reward.slot
        for reward in rewards
        if normalize_ocr_name(reward.name) == normalize_ocr_name(observed)
    ]
    scores = sorted(
        ((reward.slot, ocr_name_similarity(reward.name, observed)) for reward in rewards),
        key=lambda item: (-item[1], item[0]),
    )
    if (
        not observed
        or (
            exact_slots != [selected.slot]
            and (
                not scores
                or scores[0][0] != selected.slot
                or scores[0][1] < 0.62
                or (len(scores) > 1 and scores[0][1] - scores[1][1] < 0.08)
            )
        )
    ):
        raise StorageBagChoiceBoxBlocked(
            f"候选{selected.slot} 详情身份未唯一映射到 Catalog：{observed!r}"
        )


def scan_selected_choice_detail(
    runtime: Any,
    selected: StorageBagChoiceReward,
) -> Generator[Any, Any, str]:
    """Open only the selected card's read-only #587 detail and return safely."""

    yield from runtime.wait_click(
        CHOICE_BOX_SCENE,
        f"候选{selected.slot}/打开详情",
        timeout=8.0,
    )
    yield from runtime.wait_view(
        CHOICE_DETAIL_SCENE,
        timeout=8.0,
        label=f"储物袋自选匣：等待候选{selected.slot} #587详情",
    )
    frame = runtime.cur_frame(update=True)
    tokens = runtime.ocr_tokens_in_shapes(
        CHOICE_DETAIL_SCENE,
        ("详情标题",),
        padding=6,
        frame_data_url=frame,
        crop=True,
    )
    observed = _ordered_text(tokens)
    yield from runtime.wait_click(
        CHOICE_DETAIL_SCENE,
        "右侧暗幕返回",
        timeout=8.0,
    )
    yield from runtime.wait_view(
        CHOICE_BOX_SCENE,
        timeout=8.0,
        label="储物袋自选匣：候选详情返回 #586",
    )
    return observed


def _partner_snapshot_identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    try:
        identity = (
            int(evidence.get("pid") or 0),
            int(evidence.get("process_start_ticks") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise StorageBagChoiceBoxBlocked("仙侣权威快照进程身份无效") from exc
    source = str(snapshot.get("source") or "")
    fingerprint = str(snapshot.get("fingerprint") or "")
    if (
        snapshot.get("complete") is not True
        or "runtime_memory" not in source
        or not fingerprint
        or min(identity) <= 0
        or not isinstance(snapshot.get("partners"), Sequence)
    ):
        raise StorageBagChoiceBoxBlocked(
            "仙侣快照必须是完整 runtime_memory 权威清单并含进程/指纹证据"
        )
    return identity


def _partner_ids(snapshot: Mapping[str, Any]) -> set[int]:
    result: set[int] = set()
    seen: set[int] = set()
    for row in snapshot.get("partners") or []:
        if not isinstance(row, Mapping):
            raise StorageBagChoiceBoxBlocked("仙侣权威快照含无效行")
        try:
            partner_id = int(row.get("id") or 0)
        except (TypeError, ValueError) as exc:
            raise StorageBagChoiceBoxBlocked("仙侣权威快照含无效 ID") from exc
        if partner_id <= 0 or partner_id in seen:
            raise StorageBagChoiceBoxBlocked("仙侣权威快照含无效或重复 ID")
        seen.add(partner_id)
        if not isinstance(row.get("owned"), bool):
            raise StorageBagChoiceBoxBlocked("PartnerInfoVoList 行缺少布尔 owned 权威状态")
        if row.get("owned") is True:
            result.add(partner_id)
    return result


def verify_authoritative_partner_outcome(
    before_partner: Mapping[str, Any],
    after_partner: Mapping[str, Any],
    before_storage: Mapping[str, Any],
    after_storage: Mapping[str, Any],
    request: StorageBagChoiceBoxRequest,
    reward: StorageBagChoiceReward,
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
) -> StorageBagPartnerOutcomeProof:
    """Verify activation or exact Catalog-derived partner-fragment conversion."""

    target_id = int(reward.linked_partner_id or 0)
    opened_count = requested_choice_box_open_quantity(request)
    if target_id <= 0 or opened_count <= 0:
        raise StorageBagChoiceBoxBlocked("仙侣奖励缺少可核验目标 ID 或开启数量")
    before_identity = _partner_snapshot_identity(before_partner)
    after_identity = _partner_snapshot_identity(after_partner)
    if before_identity != after_identity:
        raise StorageBagChoiceBoxBlocked("仙侣动作前后权威快照不属于同一游戏进程")
    before_fingerprint = str(before_partner.get("fingerprint") or "")
    after_fingerprint = str(after_partner.get("fingerprint") or "")
    before_ids = _partner_ids(before_partner)
    after_ids = _partner_ids(after_partner)
    if target_id not in before_ids and target_id in after_ids:
        if before_fingerprint == after_fingerprint:
            raise StorageBagChoiceBoxBlocked("仙侣激活后权威快照指纹没有变化")
        _verify_exact_storage_changes(
            before_storage,
            after_storage,
            expected={request.base_id: -opened_count},
        )
        return StorageBagPartnerOutcomeProof(
            target_id,
            "activated",
            before_fingerprint,
            after_fingerprint,
            "目标仙侣由未拥有转换为已拥有",
        )
    if target_id in before_ids and target_id in after_ids:
        fragment_base_id = _resolve_partner_fragment_base_id(
            catalog_cards_by_id, target_id
        )
        fragment_quantity = reward.count_per_box * opened_count
        _verify_exact_storage_changes(
            before_storage,
            after_storage,
            expected={
                request.base_id: -opened_count,
                fragment_base_id: fragment_quantity,
            },
        )
        return StorageBagPartnerOutcomeProof(
            target_id,
            "duplicate_converted",
            before_fingerprint,
            after_fingerprint,
            f"Catalog PartnerFragment|{target_id} -> {fragment_base_id} ×{fragment_quantity}",
            fragment_base_id,
            fragment_quantity,
        )
    raise StorageBagChoiceBoxBlocked("目标仙侣状态没有发生允许的转换")


def _resolve_partner_fragment_base_id(
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]], target_partner_id: int
) -> int:
    expected_effect = f"PartnerFragment|{int(target_partner_id)}"
    matches: list[int] = []
    for raw_key, card in catalog_cards_by_id.items():
        effect_value = str(
            card.get("effect_value") or card.get("effectValue") or ""
        ).strip()
        if effect_value != expected_effect:
            continue
        try:
            base_id = int(card.get("base_id") or card.get("id") or raw_key)
        except (TypeError, ValueError):
            base_id = 0
        if base_id > 0:
            matches.append(base_id)
    if len(set(matches)) != 1:
        raise StorageBagChoiceBoxBlocked(
            f"Catalog 未唯一解析 {expected_effect} 对应的碎片 base_id"
        )
    return matches[0]


def _storage_base_totals(snapshot: Mapping[str, Any]) -> dict[int, int]:
    totals: dict[int, int] = {}
    for base_id, quantity in _runtime_instances(snapshot).values():
        totals[base_id] = totals.get(base_id, 0) + quantity
    return totals


def _verify_exact_storage_changes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected: Mapping[int, int],
) -> None:
    before_totals = _storage_base_totals(before)
    after_totals = _storage_base_totals(after)
    for base_id in set(before_totals) | set(after_totals) | set(expected):
        delta = after_totals.get(base_id, 0) - before_totals.get(base_id, 0)
        if delta != int(expected.get(base_id, 0)):
            raise StorageBagChoiceBoxBlocked(
                f"仙侣结果背包物品 {base_id} 增量 {delta} != 权威期望 {expected.get(base_id, 0)}"
            )


def derive_partner_choice_box_consumption_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    request: StorageBagChoiceBoxRequest,
) -> StorageBagChoiceBoxDelta:
    """Verify box consumption only; partner proof is a separate mandatory gate."""

    before_instances = _runtime_instances(before)
    after_instances = _runtime_instances(after)
    target_before = before_instances.get(request.instance_id)
    target_after = after_instances.get(request.instance_id)
    if target_before != (request.base_id, request.quantity):
        raise StorageBagChoiceBoxBlocked("动作前 Runtime 目标实例与请求不一致")
    if target_after is not None and target_after[0] != request.base_id:
        raise StorageBagChoiceBoxBlocked("动作后目标 instance_id 被复用为其它物品")
    opened = request.quantity - (target_after[1] if target_after else 0)
    expected_opened = requested_choice_box_open_quantity(request)
    if opened != expected_opened:
        raise StorageBagChoiceBoxBlocked(
            f"确定后目标仙侣自选匣减少 {opened} != 计划 {expected_opened}"
        )
    return StorageBagChoiceBoxDelta(
        opened,
        0,
        0,
        str(before.get("fingerprint") or ""),
        str(after.get("fingerprint") or ""),
    )


def _validate_partner_outcome_proof(
    proof: StorageBagPartnerOutcomeProof,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    reward: StorageBagChoiceReward,
) -> None:
    if not isinstance(proof, StorageBagPartnerOutcomeProof):
        raise StorageBagChoiceBoxBlocked("仙侣结果 verifier 没有返回正式 proof")
    target_id = int(reward.linked_partner_id or 0)
    if proof.target_partner_id != target_id:
        raise StorageBagChoiceBoxBlocked("仙侣结果 proof 目标与 Catalog 不一致")
    if proof.before_fingerprint != str(before.get("fingerprint") or "") or proof.after_fingerprint != str(after.get("fingerprint") or ""):
        raise StorageBagChoiceBoxBlocked("仙侣结果 proof 没有绑定动作前后权威快照")
    if proof.outcome not in {"activated", "duplicate_converted"}:
        raise StorageBagChoiceBoxBlocked("仙侣结果 proof 类型不受支持")
    if proof.outcome == "duplicate_converted" and not proof.explanation.strip():
        raise StorageBagChoiceBoxBlocked("仙侣重复转化 proof 缺少可解释结果")
    if proof.outcome == "duplicate_converted" and (
        not proof.fragment_base_id or proof.fragment_quantity <= 0
    ):
        raise StorageBagChoiceBoxBlocked("仙侣重复转化 proof 缺少精确碎片增量")


def derive_choice_box_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    request: StorageBagChoiceBoxRequest,
    reward: StorageBagChoiceReward,
) -> StorageBagChoiceBoxDelta:
    before_instances = _runtime_instances(before)
    after_instances = _runtime_instances(after)
    target_before = before_instances.get(request.instance_id)
    target_after = after_instances.get(request.instance_id)
    if target_before != (request.base_id, request.quantity):
        raise StorageBagChoiceBoxBlocked("动作前 Runtime 目标实例与请求不一致")
    if target_after is not None and target_after[0] != request.base_id:
        raise StorageBagChoiceBoxBlocked("动作后目标 instance_id 被复用为其它物品")
    opened = request.quantity - (target_after[1] if target_after else 0)
    expected_opened = requested_choice_box_open_quantity(request)
    if opened != expected_opened:
        raise StorageBagChoiceBoxBlocked(
            f"确定后目标自选匣减少 {opened} != 计划 {expected_opened}"
        )

    def by_base(instances: Mapping[str, tuple[int, int]]) -> dict[int, int]:
        result: dict[int, int] = {}
        for base_id, quantity in instances.values():
            result[base_id] = result.get(base_id, 0) + quantity
        return result

    before_base = by_base(before_instances)
    after_base = by_base(after_instances)
    expected_reward = reward.count_per_box * opened
    reward_delta = after_base.get(reward.base_id, 0) - before_base.get(reward.base_id, 0)
    if reward_delta != expected_reward:
        raise StorageBagChoiceBoxBlocked(
            f"选择奖励 Runtime 增量 {reward_delta} != Catalog 期望 {expected_reward}"
        )
    for base_id in set(before_base) | set(after_base):
        delta = after_base.get(base_id, 0) - before_base.get(base_id, 0)
        expected = -opened if base_id == request.base_id else expected_reward if base_id == reward.base_id else 0
        if delta != expected:
            raise StorageBagChoiceBoxBlocked(f"非目标物品 {base_id} 同时变化，无法唯一归因")
    return StorageBagChoiceBoxDelta(
        opened,
        reward.base_id,
        reward_delta,
        str(before.get("fingerprint") or ""),
        str(after.get("fingerprint") or ""),
    )


class StorageBagChoiceBoxGuiAdapter:
    """Reusable #525→#586 choice executor; never writes yield averages."""

    def __init__(
        self,
        *,
        runtime: Any,
        snapshot_reader: SnapshotReader,
        catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
        target_detail_scanner: TargetDetailScanner = scan_selected_choice_detail,
        visible_slot_reader: VisibleSlotReader = discover_visible_choice_slots,
        click_planner: ClickPlanner = plan_current_random_box_click,
        asset_validator: AssetValidator = validate_choice_box_asset_contract,
        availability_reader: AvailabilityReader = read_choice_availability,
        selection_verifier: SelectionVerifier = verify_unique_choice_selection,
        count_reader: CountReader = read_choice_count,
        partner_snapshot_reader: PartnerSnapshotReader | None = read_storage_bag_partner_snapshot,
        partner_outcome_verifier: PartnerOutcomeVerifier | None = verify_authoritative_partner_outcome,
        alignment_retries: int = 2,
        max_scrolls: int = 12,
        max_increment_steps: int = 200,
        after_snapshot_retries: int = 4,
    ) -> None:
        self.runtime = runtime
        self.snapshot_reader = snapshot_reader
        self.catalog_cards_by_id = catalog_cards_by_id
        self.click_planner = click_planner
        self.asset_validator = asset_validator
        self.target_detail_scanner = target_detail_scanner
        self.visible_slot_reader = visible_slot_reader
        self.availability_reader = availability_reader
        self.selection_verifier = selection_verifier
        self.count_reader = count_reader
        self.partner_snapshot_reader = partner_snapshot_reader
        self.partner_outcome_verifier = partner_outcome_verifier
        self.alignment_retries = max(0, min(4, int(alignment_retries)))
        self.max_scrolls = max(0, min(30, int(max_scrolls)))
        self.max_increment_steps = max(0, min(500, int(max_increment_steps)))
        self.after_snapshot_retries = max(1, min(10, int(after_snapshot_retries)))

    def execute(
        self, request: StorageBagChoiceBoxRequest
    ) -> Generator[Any, Any, StorageBagChoiceBoxExecution]:
        if request.base_id <= 0 or not request.instance_id or not request.name.strip() or request.quantity <= 0:
            raise StorageBagChoiceBoxBlocked("自选匣请求缺少 base_id/instance_id/名称/数量")
        open_quantity = requested_choice_box_open_quantity(request)
        parse_persisted_choice_note(request.note)
        box_card = self.catalog_cards_by_id.get(str(request.base_id)) or {}
        rewards = choice_rewards_from_catalog(box_card, self.catalog_cards_by_id)
        annotated_slots = tuple(
            int(slot) for slot in self.visible_slot_reader(self.runtime)
        )
        visible_slots = annotated_slots[: min(len(annotated_slots), len(rewards))]
        if not visible_slots:
            raise StorageBagChoiceBoxBlocked("#586 没有正式标注的可见候选")
        # This gate intentionally runs before even reading/clicking #525.
        self.asset_validator(self.runtime, visible_slots)

        before = dict(self.snapshot_reader())
        identity = _snapshot_identity(before)
        target = _runtime_instances(before).get(request.instance_id)
        if target != (request.base_id, request.quantity):
            raise StorageBagChoiceBoxBlocked("动作前 Runtime 没有与请求一致的唯一目标实例")

        retries = 0
        scrolls = 0
        while True:
            plan = self.click_planner(self.runtime, before, request)
            if plan.ready:
                break
            if plan.status in {"insufficient_observations", "ambiguous_offset"}:
                if retries >= self.alignment_retries:
                    raise StorageBagChoiceBoxBlocked(f"#525 对齐有限重试后仍不唯一：{plan.status}")
                retries += 1
                yield from self.runtime.wait_action_settle(0.2)
                continue
            if plan.status == "target_not_visible":
                if scrolls >= self.max_scrolls or plan.runtime_index is None or plan.viewport_runtime_start is None or not plan.observations:
                    raise StorageBagChoiceBoxBlocked("#525 不具备安全有界滚动证据")
                directive = plan_storage_bag_scroll(
                    target_runtime_index=plan.runtime_index,
                    viewport_runtime_start=plan.viewport_runtime_start,
                    visible_cell_count=max(item.visible_index for item in plan.observations) + 1,
                )
                if directive.direction == "none":
                    raise StorageBagChoiceBoxBlocked("#525 滚动规划与不可见判定矛盾")
                self.runtime.drag_shape_content(
                    STORAGE_BAG_SCENE,
                    "窗口",
                    direction=directive.direction,
                    ratio=0.72 if directive.mode == "coarse" else 0.38,
                    duration=0.45,
                )
                scrolls += 1
                retries = 0
                yield from self.runtime.wait_action_settle(0.25)
                continue
            raise StorageBagChoiceBoxBlocked(f"#525 目标定位失败：{plan.status}")

        self.runtime.click_frame_point(STORAGE_BAG_SCENE, *plan.point)
        yield from self.runtime.wait_view(
            CHOICE_BOX_SCENE,
            timeout=8.0,
            label="储物袋自选匣：等待 #586",
        )
        detail_frame = self.runtime.cur_frame(update=True)
        detail_tokens = self.runtime.ocr_tokens_in_shapes(
            CHOICE_BOX_SCENE,
            ("详情标题",),
            padding=6,
            frame_data_url=detail_frame,
            crop=True,
        )
        detail = verify_storage_bag_item_detail(
            plan,
            expected_name=request.name,
            detail_title_texts=(_ordered_text(detail_tokens),),
        )
        if not detail.confirmed:
            raise StorageBagChoiceBoxBlocked(f"#586 详情标题复核失败：{detail.reason}")

        availability = dict(self.availability_reader(self.runtime, visible_slots))
        selected = choose_reward_from_note(
            request.note, rewards, availability, visible_slots
        )
        observed_target = yield from self.target_detail_scanner(self.runtime, selected)
        validate_target_detail_identity(rewards, selected, observed_target)
        yield from self.runtime.wait_click(
            CHOICE_BOX_SCENE,
            f"候选{selected.slot}/右上选择框",
            timeout=8.0,
        )
        if not self.selection_verifier(self.runtime, selected.slot, len(visible_slots)):
            raise StorageBagChoiceBoxBlocked("#586 绿色勾选没有独立证明目标被唯一选中")

        current = self.count_reader(self.runtime)
        if not 1 <= current <= open_quantity:
            raise StorageBagChoiceBoxBlocked(
                f"#586 初始数量 {current} 不在 1..计划开启{open_quantity}"
            )
        steps = open_quantity - current
        if steps < 0 or steps > self.max_increment_steps:
            raise StorageBagChoiceBoxBlocked("#586 增加数量步数超出有界预算")
        for _step in range(steps):
            yield from self.runtime.wait_click(CHOICE_BOX_SCENE, "增加数量", timeout=8.0)
            yield from self.runtime.wait_view(
                CHOICE_BOX_SCENE,
                timeout=8.0,
                label="储物袋自选匣：增加数量后复验 fresh #586",
            )
        if not self.selection_verifier(self.runtime, selected.slot, len(visible_slots)):
            raise StorageBagChoiceBoxBlocked("确定前绿色勾选唯一证据失效")
        if self.count_reader(self.runtime) != open_quantity:
            raise StorageBagChoiceBoxBlocked("确定前最终 OCR 数量不等于计划开启数量")

        partner_before: dict[str, Any] | None = None
        if selected.is_partner:
            if selected.linked_partner_id is None:
                raise StorageBagChoiceBoxBlocked(
                    "仙侣奖励缺少 linked_partner_id，禁止点击确定"
                )
            if self.partner_snapshot_reader is None or self.partner_outcome_verifier is None:
                raise StorageBagChoiceBoxBlocked(
                    "仙侣奖励没有显式权威 outcome verifier，禁止点击确定"
                )
            partner_before = dict(self.partner_snapshot_reader())
            _partner_snapshot_identity(partner_before)
            if selected.linked_partner_id in _partner_ids(partner_before):
                _resolve_partner_fragment_base_id(
                    self.catalog_cards_by_id, selected.linked_partner_id
                )

        yield from self.runtime.wait_click(CHOICE_BOX_SCENE, "确定", timeout=8.0)
        yield from self.runtime.wait_view(
            STORAGE_BAG_SCENE,
            timeout=8.0,
            label="储物袋自选匣：确定后等待 #525",
        )
        after: dict[str, Any] | None = None
        for attempt in range(self.after_snapshot_retries):
            candidate = dict(self.snapshot_reader())
            try:
                candidate_identity = _snapshot_identity(candidate)
            except StorageBagChoiceBoxBlocked:
                candidate_identity = (-1, -1)
            if candidate_identity == identity and candidate.get("fingerprint") != before.get("fingerprint"):
                after = candidate
                break
            if attempt + 1 < self.after_snapshot_retries:
                yield from self.runtime.wait_action_settle(0.2)
        if after is None:
            raise StorageBagChoiceBoxBlocked("确定后未取得同进程、完整且已变化的 Runtime 快照")
        partner_outcome: StorageBagPartnerOutcomeProof | None = None
        if selected.is_partner:
            assert partner_before is not None
            assert self.partner_snapshot_reader is not None
            assert self.partner_outcome_verifier is not None
            last_partner_error: StorageBagChoiceBoxBlocked | None = None
            for attempt in range(self.after_snapshot_retries):
                partner_after = dict(self.partner_snapshot_reader())
                try:
                    if _partner_snapshot_identity(partner_before) != _partner_snapshot_identity(partner_after):
                        raise StorageBagChoiceBoxBlocked(
                            "仙侣动作前后权威快照不属于同一游戏进程"
                        )
                    candidate_proof = self.partner_outcome_verifier(
                        partner_before,
                        partner_after,
                        before,
                        after,
                        request,
                        selected,
                        self.catalog_cards_by_id,
                    )
                    _validate_partner_outcome_proof(
                        candidate_proof, partner_before, partner_after, selected
                    )
                    partner_outcome = candidate_proof
                    break
                except StorageBagChoiceBoxBlocked as exc:
                    last_partner_error = exc
                if attempt + 1 < self.after_snapshot_retries:
                    yield from self.runtime.wait_action_settle(0.2)
            if partner_outcome is None:
                raise StorageBagChoiceBoxBlocked(
                    f"确定后仙侣结果未被权威证明：{last_partner_error or 'unknown'}"
                )
            delta = derive_partner_choice_box_consumption_delta(
                before, after, request=request
            )
        else:
            delta = derive_choice_box_delta(
                before, after, request=request, reward=selected
            )
        return StorageBagChoiceBoxExecution(
            request,
            selected,
            delta,
            detail.observed_name,
            detail.similarity,
            scrolls,
            partner_outcome,
        )


__all__ = [
    "CHOICE_BOX_SCENE",
    "CHOICE_DETAIL_SCENE",
    "StorageBagChoiceBoxBlocked",
    "StorageBagChoiceBoxDelta",
    "StorageBagChoiceBoxExecution",
    "StorageBagChoiceBoxGuiAdapter",
    "StorageBagChoiceBoxRequest",
    "StorageBagChoiceCandidateEvidence",
    "StorageBagChoiceReward",
    "StorageBagPartnerOutcomeProof",
    "choice_rewards_from_catalog",
    "choose_reward_from_note",
    "derive_choice_box_delta",
    "derive_partner_choice_box_consumption_delta",
    "discover_visible_choice_slots",
    "green_pixel_ratio",
    "luminance_availability",
    "parse_persisted_choice_note",
    "read_choice_availability",
    "scan_selected_choice_detail",
    "unique_green_selection",
    "validate_target_detail_identity",
    "read_choice_count",
    "validate_choice_box_asset_contract",
    "verify_unique_choice_selection",
    "verify_authoritative_partner_outcome",
]
