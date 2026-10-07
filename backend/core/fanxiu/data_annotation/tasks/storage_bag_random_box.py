from __future__ import annotations

"""Fail-closed GUI adapter for one storage-bag random-box Runtime instance.

The game Runtime owns item identity, order, quantities, and rewards.  This
module only maps that already-known instance to the current #525 grid, drives
the formally annotated #583/#584 flow, and verifies the resulting Runtime
delta.  It deliberately has no dependency on the resource-auto-use planner.
"""

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
import hashlib
import math
import re
from types import GeneratorType
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.runtime_gui import (
    StorageBagGrid,
    StorageBagItemClickPlan,
    plan_storage_bag_item_click,
    plan_storage_bag_scroll,
    quantity_observations_from_ocr,
    register_storage_bag_viewport,
    verify_storage_bag_item_detail,
    visible_storage_bag_cells,
)
from backend.core.fanxiu.storage_bag_usage import (
    StorageBagVerifiedOpenDelta,
    derive_storage_bag_open_delta,
    record_storage_bag_open_event,
)


STORAGE_BAG_SCENE = 525
RANDOM_BOX_DETAIL_SCENE = 583
FIXED_BOX_DETAIL_SCENE = 585
USE_QUANTITY_SCENE = 584
TRANSIENT_REWARD_SCENE = 578
STORAGE_BAG_PLAN_FRAME_STABILITY_THRESHOLD = 95.0
# #584 数量闭环的加减按钮动作上限。正常弹窗默认 1、目标是一整叠，比例定位先把
# 数量推到接近上限，剩下的差值用按钮收紧；40 步足够覆盖已验证的整叠规模。
# 拉满后用“增加”点几下确认的步数：已经到顶时这些点击是无副作用的空操作。
STORAGE_BAG_USE_QUANTITY_MAX_STEPS = 3
# 2026-09-22 真实 #525 实测：框架默认手势（ratio 0.5、duration 1.5s）确实推进列表，
# 6 次推进约 27 格；任务原先自定的 0.45s 快速手势会被游戏整格忽略。滚动统一走默认
# 手势，允许次数按“当前剩余格数 / 单次实测下界”推导，只在原地打转时才耗尽。
DEFAULT_DRAG_SLOTS_FLOOR = 4
SCROLL_ALLOWANCE_MARGIN = 2
# 粗推进：同一份配准最多连续承担几次默认手势。批量数必须按“单把实测位移上界”反算，
# 否则估算出的批量会冲过目标再回滚。2026-09-22 在真实 #525 上双向实测：默认手势
# （ratio 0.5、duration 1.5s）每把推进约 9 格（上下行一致），故取 10 格为上界，
# 批量覆盖剩余距离的 75–90%，保留防越界余量。
COARSE_DRAG_BATCH_MAX = 6
DRAG_SLOTS_UPPER_BOUND = 10
# 拖拽本身只负责手势（框架默认 1.5s），惯性要靠调用方等待。全仓拖拽后的标准等待是
# ``wait_action_settle`` 0.8–1.0s；#525 列表更长更滑，取 1.2s 留余量。等待不足会让
# 窗口还在滑行时就发出下一把拖拽，实测能把一次定位从 7 次滚屏放大到 46 次。
STORAGE_BAG_DRAG_SETTLE_SECONDS = 1.2


def coarse_drag_batch(remaining_slots: int) -> int:
    """How many default drags one registration may carry without overshooting.

    Re-registering after every single drag costs a full-frame OCR plus sequence
    alignment (~5.6s on real #525), so a coarse approach batches several default
    gestures per registration.  The count is derived from the conservative
    *upper* bound of one gesture so the target still ends up at or below the
    window; a larger batch would push it past the top and force a corrective
    scroll back, which costs more than the registration it saves.
    """

    remaining = max(0, int(remaining_slots))
    return max(1, min(COARSE_DRAG_BATCH_MAX, remaining // DRAG_SLOTS_UPPER_BOUND))


def bounded_scroll_allowance(remaining_slots: int) -> int:
    """Derive the per-anchor drag allowance from the measured displacement floor.

    A fixed drag count silently fails once the Runtime list grows past
    ``count * displacement``; the allowance therefore scales with the real
    remaining distance at the current anchor and stays conservative about how
    far one framework default gesture actually moves the window.  It is a
    budget for repeatedly re-registering the same anchor, never for the whole
    scroll sequence: the caller re-anchors every time the distance shrinks.
    """

    remaining = max(0, int(remaining_slots))
    return max(1, -(-remaining // DEFAULT_DRAG_SLOTS_FLOOR) + SCROLL_ALLOWANCE_MARGIN)


class StorageBagRandomBoxBlocked(RuntimeError):
    """The adapter could not prove a unique, authorized next action."""


@dataclass(frozen=True)
class StorageBagRandomBoxRequest:
    base_id: int
    instance_id: str
    name: str
    quantity: int
    open_quantity: int | None = None

    @property
    def requested_open_count(self) -> int:
        """库存 quantity 用于身份校验；open_quantity 独立限定本次消耗。"""
        count = self.quantity if self.open_quantity is None else self.open_quantity
        if isinstance(count, bool) or not isinstance(count, int) or not 0 < count <= self.quantity:
            raise ValueError("开箱数量必须在已核实库存以内")
        return count


@dataclass(frozen=True)
class StorageBagRandomBoxExecution:
    request: StorageBagRandomBoxRequest
    delta: StorageBagVerifiedOpenDelta
    action_key: str
    operation_template: str
    detail_observed_name: str
    detail_similarity: float
    scroll_count: int
    wallet_before: tuple[tuple[int, int], ...] = ()
    wallet_after: tuple[tuple[int, int], ...] = ()
    inventory_before: tuple[tuple[int, int], ...] = ()
    inventory_after: tuple[tuple[int, int], ...] = ()
    observed_at_before: float | None = None
    observed_at_after: float | None = None
    process_identity: tuple[int, int] | None = None
    ring_inventory_before: Mapping[str, Any] | None = None
    ring_inventory_after: Mapping[str, Any] | None = None

    def evidence(self) -> dict[str, Any]:
        result = {
            "adapter": f"storage_bag_{self.operation_template}_v1",
            "instance_id": self.request.instance_id,
            "expected_quantity": self.request.quantity,
            "requested_open_count": self.request.requested_open_count,
            "detail_observed_name": self.detail_observed_name,
            "detail_similarity": self.detail_similarity,
            "scroll_count": self.scroll_count,
            "wallet_before": dict(self.wallet_before),
            "wallet_after": dict(self.wallet_after),
        }
        if self.inventory_before:
            result.update(inventory_before=dict(self.inventory_before),
                          inventory_after=dict(self.inventory_after),
                          observed_at_before=self.observed_at_before,
                          observed_at_after=self.observed_at_after)
        if self.process_identity:
            result.update(pid=self.process_identity[0], process_start_ticks=self.process_identity[1])
        if self.ring_inventory_before is not None:
            result.update(ring_inventory_before=dict(self.ring_inventory_before),
                          ring_inventory_after=dict(self.ring_inventory_after or {}))
        return result


SnapshotReader = Callable[[], Mapping[str, Any]]
WalletSnapshotReader = Callable[[int], Mapping[str, Any]]
ClickPlanner = Callable[[Any, Mapping[str, Any], StorageBagRandomBoxRequest], StorageBagItemClickPlan]
ExecutionRecorder = Callable[[StorageBagRandomBoxExecution], Any]


def _view_id(value: Any) -> int | None:
    raw = getattr(value, "id", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _snapshot_process_identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    try:
        pid = int(evidence.get("pid") or 0)
        start_ticks = int(evidence.get("process_start_ticks") or 0)
    except (TypeError, ValueError) as exc:
        raise StorageBagRandomBoxBlocked("储物袋 Runtime 进程身份无效") from exc
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
        or not str(snapshot.get("fingerprint") or "")
        or pid <= 0
        or start_ticks <= 0
    ):
        raise StorageBagRandomBoxBlocked("储物袋 Runtime 不完整、来源错误或缺少进程/指纹证据")
    return pid, start_ticks


def _target_runtime_item(
    snapshot: Mapping[str, Any], request: StorageBagRandomBoxRequest
) -> Mapping[str, Any]:
    matches = [
        row
        for row in snapshot.get("items") or []
        if isinstance(row, Mapping)
        and not row.get("is_padding")
        and str(row.get("instance_id") or "") == request.instance_id
        and int(row.get("base_id") or 0) == request.base_id
    ]
    if len(matches) != 1:
        raise StorageBagRandomBoxBlocked("动作前 Runtime 没有唯一的目标 instance_id/base_id")
    if int(matches[0].get("num") or 0) != request.quantity or request.quantity <= 0:
        raise StorageBagRandomBoxBlocked("计划数量与动作前目标 Runtime 数量不一致")
    return matches[0]


def wallet_reward_targets(
    box_card: Mapping[str, Any],
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[int, str]:
    """Map configured reward rows to the wallet Runtime types they actually mutate."""

    result: dict[int, str] = {}
    for reward in box_card.get("optional_gift_rewards") or []:
        if not isinstance(reward, Mapping):
            continue
        reward_id = int(reward.get("id") or 0)
        reward_card = catalog_cards_by_id.get(str(reward_id)) or {}
        effect_value = str(reward_card.get("effect_value") or "").strip()
        currency_type: int | None = None
        wallet_match = re.fullmatch(r"WALLET\|(\d+)", effect_value)
        if reward_id == 1012 and effect_value == "1002_6":
            # Item 1012 is the six-yuan recharge-voucher consumable.  Its
            # effect opcode is 1002, but the balance it mutates is the
            # WalletType.Voucher currency (1001), as proven by ChargeMgr and
            # the live WalletMgr ledger.  The opcode is not a currency id.
            currency_type = 1001
        elif wallet_match:
            currency_type = int(wallet_match.group(1))
        elif int(reward_card.get("type") or 0) == 9:
            currency_match = re.match(r"(\d+)(?:_|$)", effect_value)
            if currency_match:
                currency_type = int(currency_match.group(1))
        if currency_type is None or currency_type <= 0:
            continue
        name = str(reward.get("name") or reward_card.get("name") or "").strip()
        previous = result.get(currency_type)
        if previous and name and previous != name:
            raise StorageBagRandomBoxBlocked(
                f"奖励配置把钱包类型 {currency_type} 映射到多个名称"
            )
        result[currency_type] = previous or name
    return result


def _read_wallet_amounts(
    reader: WalletSnapshotReader,
    targets: Mapping[int, str],
    *,
    process_identity: tuple[int, int],
) -> dict[int, int]:
    amounts: dict[int, int] = {}
    for currency_type in sorted(targets):
        snapshot = reader(currency_type)
        evidence = snapshot.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        identity = (
            int(evidence.get("pid") or 0),
            int(evidence.get("process_start_ticks") or 0),
        )
        if (
            snapshot.get("source") != "runtime_memory"
            or int(snapshot.get("currency_type") or 0) != currency_type
            or identity != process_identity
        ):
            raise StorageBagRandomBoxBlocked(
                f"钱包类型 {currency_type} 快照来源或进程身份不一致"
            )
        amount = int(snapshot.get("exchange_currency") or 0)
        if amount < 0:
            raise StorageBagRandomBoxBlocked(f"钱包类型 {currency_type} 数量为负")
        amounts[currency_type] = amount
    return amounts


def _wallet_reward_deltas(
    before: Mapping[int, int],
    after: Mapping[int, int],
    targets: Mapping[int, str],
) -> list[dict[str, Any]]:
    rewards: list[dict[str, Any]] = []
    if set(before) != set(targets) or set(after) != set(targets):
        raise StorageBagRandomBoxBlocked("动作前后钱包奖励类型集合不一致")
    for currency_type in sorted(targets):
        delta = int(after[currency_type]) - int(before[currency_type])
        if delta < 0:
            raise StorageBagRandomBoxBlocked(
                f"非消费型钱包奖励 {currency_type} 在开箱时反而减少"
            )
        if delta > 0:
            rewards.append(
                {
                    "reward_key": f"wallet:{currency_type}",
                    "item_id": currency_type,
                    "name": targets[currency_type],
                    "quantity": delta,
                }
            )
    return rewards


def _decode_frame(frame_data_url: str):
    import cv2
    import numpy as np

    if not isinstance(frame_data_url, str) or "," not in frame_data_url:
        raise StorageBagRandomBoxBlocked("储物袋网格缺少可解码帧")
    import base64

    raw = base64.b64decode(frame_data_url.split(",", 1)[1])
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise StorageBagRandomBoxBlocked("储物袋网格帧解码失败")
    return frame


def require_stable_storage_bag_plan_frame(
    plan: StorageBagItemClickPlan,
    *,
    frame_similarity: float,
    minimum_similarity: float = STORAGE_BAG_PLAN_FRAME_STABILITY_THRESHOLD,
) -> StorageBagItemClickPlan:
    """Invalidate coordinates derived from a storage-bag frame that moved.

    Full-frame OCR is materially slower than one MuMu frame capture.  A list
    can therefore keep coasting after a drag while OCR is being decoded: the
    Runtime/quantity sequence remains correct for the captured frame, but its
    grid coordinate is already stale by the time the caller clicks it.  Only
    plans that carry a viewport coordinate need this temporal proof.
    """

    if plan.viewport_runtime_start is None:
        return plan
    similarity = float(frame_similarity)
    threshold = float(minimum_similarity)
    if similarity >= threshold:
        return plan
    return StorageBagItemClickPlan(
        "insufficient_observations",
        (
            "#525 数量序列完成后窗口内容仍在移动，"
            f"前后帧相似度 {similarity:.1f}% < {threshold:.1f}%；"
            "旧帧网格坐标已失效，拒绝点击并要求重新配准"
        ),
        runtime_index=plan.runtime_index,
        runtime_item=plan.runtime_item,
        viewport_runtime_start=plan.viewport_runtime_start,
        candidate_starts=plan.candidate_starts,
        observations=plan.observations,
    )


def plan_current_random_box_click(
    context: Any,
    snapshot: Mapping[str, Any],
    request: StorageBagRandomBoxRequest,
    *,
    known_viewport_start: int | None = None,
) -> Generator[Any, Any, StorageBagItemClickPlan]:
    """Re-register one fresh #525 frame and resolve the exact instance."""

    view = context.view(STORAGE_BAG_SCENE)
    _wait_scene_match = yield from context.wait_scene([STORAGE_BAG_SCENE], label='储物袋随机箱：定位前识别 #525', wait=5.0, required=False)
    (scene_id, _score, current_data_url) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene_id != STORAGE_BAG_SCENE:
        raise StorageBagRandomBoxBlocked("定位前全图模型未把当前帧唯一识别为 #525")
    reference_data_url = str(view.raw.get("dataUrl") or "")
    if not reference_data_url:
        # Formal trees normally store reference frames as sibling PNG files,
        # not multi-megabyte data URLs inside asset-tree.json.
        reference_data_url = context.runner._scene_frame_data_url_from_reference(
            context.ctx, view.raw
        )
    current = _decode_frame(current_data_url)
    reference = _decode_frame(reference_data_url)
    height, width = current.shape[:2]
    if reference.shape[:2] != current.shape[:2]:
        raise StorageBagRandomBoxBlocked("#525 参考帧与当前帧尺寸不一致")
    shape_names = ("窗口", "第1行第1个", "第1行第2个", "第2行第1个", "行间隙")
    shapes = {name: context.shape(STORAGE_BAG_SCENE, name).raw for name in shape_names}
    grid = StorageBagGrid.from_shapes(shapes, frame_width=width, frame_height=height)
    viewport = register_storage_bag_viewport(
        reference,
        current,
        grid=grid,
        row_gap_shape=shapes["行间隙"],
    )
    if not viewport.aligned:
        return StorageBagItemClickPlan(
            "insufficient_observations", viewport.reason
        )
    cells = visible_storage_bag_cells(grid, viewport)
    tokens = context.full_frame_ocr_tokens(frame_data_url=current_data_url)
    observations = quantity_observations_from_ocr(cells, tokens)
    plan = plan_storage_bag_item_click(
        snapshot,
        target_base_id=request.base_id,
        target_instance_id=request.instance_id,
        cells=cells,
        observations=observations,
        known_viewport_start=known_viewport_start,
    )
    if plan.status in {"insufficient_observations", "ambiguous_offset"}:
        # The default Chinese-font OCRv4 pass misses small white stack counts.
        # Use OCRv5 at native resolution only for this numeric fallback; its
        # output still needs quantity geometry and exact Runtime matching.
        lines = context.ocr_lines_in_shapes(
            STORAGE_BAG_SCENE, ["窗口"], frame_data_url=current_data_url,
            options={"text_det_limit_side_len": max(height, width),
                     "text_det_limit_type": "max", "ocr_version": "PP-OCRv5"},
        )
        observations = quantity_observations_from_ocr(cells, lines)
        plan = plan_storage_bag_item_click(
            snapshot, target_base_id=request.base_id,
            target_instance_id=request.instance_id, cells=cells,
            observations=observations,
            known_viewport_start=known_viewport_start,
        )
    # A quantity sequence proves the mapping only for ``current_data_url``.
    # Re-sample the annotated window after the expensive OCR pass.  If the
    # list coasted during OCR, the old point must never escape as ``ready``;
    # the adapter's existing bounded alignment retry will plan from the fresh
    # settled frame instead.
    if plan.viewport_runtime_start is None:
        return plan
    try:
        before_signature = context.image_signature_bytes_in_shape(
            STORAGE_BAG_SCENE,
            "窗口",
            frame_data_url=current_data_url,
        )
        _wait_scene_match = yield from context.wait_scene([STORAGE_BAG_SCENE], label='储物袋随机箱：点击前复验 #525', wait=5.0, required=False)
        (verification_scene_id, _score, verification_data_url) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if verification_scene_id != STORAGE_BAG_SCENE:
            raise StorageBagRandomBoxBlocked("点击前复验未识别为 #525")
        after_signature = context.image_signature_bytes_in_shape(
            STORAGE_BAG_SCENE,
            "窗口",
            frame_data_url=verification_data_url,
        )
        similarity = context.image_signature_similarity(
            before_signature,
            after_signature,
        )
    except Exception as exc:
        return StorageBagItemClickPlan(
            "insufficient_observations",
            f"#525 点击前窗口稳定性复验失败：{exc}",
            runtime_index=plan.runtime_index,
            runtime_item=plan.runtime_item,
            viewport_runtime_start=plan.viewport_runtime_start,
            candidate_starts=plan.candidate_starts,
            observations=plan.observations,
        )
    return require_stable_storage_bag_plan_frame(
        plan,
        frame_similarity=similarity,
    )


def read_live_use_quantity(context: Any, *, retries: int = 4):
    """Read #584's count from full-frame OCR tokens inside the ``当前数量`` box.

    The dialog draws the count in a stylised font: on a real frame the ROI crop
    OCR (and its enlarged fallback) can return nothing while the full-frame pass
    still detects the integer (2026-09-22 measured).  The closed loop therefore
    reads the full frame and keeps only tokens whose centre is inside the
    annotated box.  Anything other than one unique positive integer is
    re-sampled a bounded number of times, then fails closed.
    """

    box = context.shape_box(USE_QUANTITY_SCENE, "当前数量")
    left = float(box.get("x") or 0.0)
    top = float(box.get("y") or 0.0)
    right = left + float(box.get("w") or 0.0)
    bottom = top + float(box.get("h") or 0.0)
    hits: list[int] = []
    for _ in range(max(1, int(retries))):
        frame = context.cur_frame(update=True)
        tokens = context.full_frame_ocr_tokens(frame_data_url=frame)
        hits = []
        for token in tokens:
            text = str(token.get("text") or "").strip()
            if not text.isdigit():
                continue
            centre_x = float(token.get("x") or 0.0) + float(token.get("w") or 0.0) / 2
            centre_y = float(token.get("y") or 0.0) + float(token.get("h") or 0.0) / 2
            if left <= centre_x <= right and top <= centre_y <= bottom:
                hits.append(int(text))
        if hits and len(set(hits)) == 1:
            return hits[0]
        yield from context.wait_action_settle(0.4)
    raise StorageBagRandomBoxBlocked(f"#584 当前数量整帧读数不唯一：{hits}")


def drag_use_quantity_slider_to_end(context: Any) -> None:
    """从唯一实时滑块拖向右端；不把模板参考位置当作当前手势起点。"""

    start_x, start_y = context.shape_center(
        USE_QUANTITY_SCENE, "数量滑杆拖柄", live=True, strict_live=True,
    )
    anchor = context.shape_box(USE_QUANTITY_SCENE, "数量滑杆右端")
    end_x = float(anchor.get("x") or 0.0) + float(anchor.get("w") or 0.0) + 40.0
    end_y = float(anchor.get("y") or 0.0) + float(anchor.get("h") or 0.0) / 2
    context.drag_frame_point(
        USE_QUANTITY_SCENE, start_x, start_y, end_x, end_y, duration_ms=800
    )


def adjust_use_quantity(context: Any, target: int):
    """整叠开箱同样精确核验最大数量，不假定弹窗初始值或滑块参考位置。

    与部分开箱共用数量控件；到达已核实库存上限才允许点击使用。
    """
    return (yield from adjust_bounded_use_quantity(context, target, maximum=target))


def storage_bag_quantity_button_budget(maximum: int, track_pixels: float) -> int:
    """给一像素数量跨度留出精调预算，避免大库存的中间目标不可达。

    2026-10-05 实机42.9万库存、目标1000，最后落在1077；30次按钮
    不足以修正77残差。阈值m仍为30，粗调还须证明残差小于实测像素
    颗粒度才可交给按钮；预算不授权从任意大误差逐个点击。
    """
    if type(maximum) is not int or maximum <= 0 or not math.isfinite(track_pixels) or track_pixels <= 0:
        raise ValueError("数量预算需要正整数库存和有效滑轨宽度")
    return max(30, math.ceil((maximum - 1) / track_pixels) + 30)


def adjust_bounded_use_quantity(context: Any, target: int, *, maximum: int):
    """按显式数量开箱：库存约束、真实数量闭环，未精确核验绝不使用。

    弹窗可能保留上一次数量，不假定默认1；共用整数控件的批量加减与
    稳定读数。误差超过30先滑动；按钮预算按一像素数量跨度配置，只有
    已进入实测拖拽颗粒度且预算足够的残差才微调，不把库存当点击预算。
    """
    from backend.core.fanxiu.runtime_gui.integer_count_control import (
        IntegerSliderAssets, set_verified_integer_slider_count,
    )
    if isinstance(target, bool) or not isinstance(target, int) or not 0 < target <= maximum:
        raise StorageBagRandomBoxBlocked("开箱数量必须在已核实库存以内")
    def read_count():
        value = read_confirmed_use_quantity(context, context.cur_frame(update=True))
        if value > maximum:
            raise StorageBagRandomBoxBlocked("使用数量超过已核实库存")
        return value
    assets = IntegerSliderAssets(
        settings_scene_id=USE_QUANTITY_SCENE, count_region="当前数量",
        count_decrease="减少", count_increase="增加", count_slider_thumb="数量滑杆拖柄",
        count_slider_left_anchor="数量滑杆左端", count_slider_right_anchor="数量滑杆右端",
        count_slider_track="数量滑杆拖条",
    )
    track_width = float(context.shape_box(USE_QUANTITY_SCENE, "数量滑杆拖条")["w"])
    button_budget = storage_bag_quantity_button_budget(maximum, track_width)
    result = yield from set_verified_integer_slider_count(
        context, assets, target, maximum=maximum, max_adjustments=30,
        max_button_actions=button_budget, runtime_count_reader=read_count,
        count_label="储物袋使用数量",
    )
    if result["after"] != target:
        raise StorageBagRandomBoxBlocked("开箱数量未精确核验，尚未使用")
    return target


def _ordered_texts(tokens: list[Mapping[str, Any]]) -> tuple[str, ...]:
    def key(token: Mapping[str, Any]) -> tuple[float, float]:
        return (float(token.get("y") or 0), float(token.get("x") or 0))

    return tuple(
        str(token.get("text") or "").strip()
        for token in sorted(tokens, key=key)
        if str(token.get("text") or "").strip()
    )


def parse_confirmed_use_quantity(tokens: list[Mapping[str, Any]]) -> int:
    """Parse exactly one positive integer from the #584 current-value ROI."""

    texts = _ordered_texts(tokens)
    values = parse_ocr_values("".join(texts)) if texts else None
    if values is None or len(values) != 1 or values[0] <= 0:
        raise StorageBagRandomBoxBlocked("#584 当前数量 OCR 不是唯一正整数")
    return int(values[0])


def read_confirmed_use_quantity(context: Any, frame_data_url: str) -> int:
    """Read #584's small orange integer, with a bounded enlarged-ROI fallback."""

    tokens = context.ocr_tokens_in_shapes(
        USE_QUANTITY_SCENE,
        ("当前数量",),
        padding=4,
        frame_data_url=frame_data_url,
        crop=True,
    )
    try:
        return parse_confirmed_use_quantity(tokens)
    except StorageBagRandomBoxBlocked:
        pass

    import cv2

    frame = _decode_frame(frame_data_url)
    shape = context.shape(USE_QUANTITY_SCENE, "当前数量").raw
    height, width = frame.shape[:2]
    x1 = max(0, round(width * float(shape.get("x") or 0.0)))
    y1 = max(0, round(height * float(shape.get("y") or 0.0)))
    x2 = min(width, round(width * (float(shape.get("x") or 0.0) + float(shape.get("w") or 0.0))))
    y2 = min(height, round(height * (float(shape.get("y") or 0.0) + float(shape.get("h") or 0.0))))
    if x2 <= x1 or y2 <= y1:
        raise StorageBagRandomBoxBlocked("#584 当前数量正式 ROI 几何无效")
    enlarged = cv2.resize(
        frame[y1:y2, x1:x2],
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    from pyxllib.ai.ocr import ocr_text

    result = ocr_text(enlarged, model="basic")
    texts = list(result.get("rec_texts") or [])
    scores = list(result.get("rec_scores") or [])
    candidates = [
        int(str(text).strip())
        for text, score in zip(texts, scores)
        if str(text).strip().isdigit() and float(score) >= 0.95
    ]
    if len(candidates) != 1 or candidates[0] <= 0:
        raise StorageBagRandomBoxBlocked(
            f"#584 放大 ROI OCR 仍不是唯一高置信正整数：{texts!r}"
        )
    return candidates[0]


def _action_key(
    request: StorageBagRandomBoxRequest,
    delta: StorageBagVerifiedOpenDelta,
    operation_template: str,
) -> str:
    raw = "|".join(
        (
            f"storage-bag-{operation_template}-v1",
            str(request.base_id),
            request.instance_id,
            delta.before_fingerprint,
            delta.after_fingerprint,
        )
    )
    return f"storage-bag-{operation_template}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def record_box_execution(
    session: Session, execution: StorageBagRandomBoxExecution
) -> Any:
    """Append the verified event and update its aggregate in one DB transaction."""

    with session.begin_nested():
        return record_storage_bag_open_event(
            session,
            action_key=execution.action_key,
            base_id=execution.request.base_id,
            operation_template=execution.operation_template,
            opened_count=execution.delta.opened_count,
            rewards=list(execution.delta.rewards),
            runtime_before_fingerprint=execution.delta.before_fingerprint,
            runtime_after_fingerprint=execution.delta.after_fingerprint,
            evidence=execution.evidence(),
        )


class StorageBagRandomBoxGuiAdapter:
    """Reusable #525/detail/#584 box adapter with injectable Runtime readers.

    ``detail_only`` shares the verified bag alignment/title contract with
    special-use #610/#934 details. It grants navigation only; execute is rejected
    before any side effect. Special-use consumption needs its own adapter.
    """

    def __init__(
        self,
        *,
        context: Any,
        snapshot_reader: SnapshotReader,
        catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
        recorder: ExecutionRecorder,
        wallet_snapshot_reader: WalletSnapshotReader | None = None,
        ring_inventory_reader: SnapshotReader | None = None,
        click_planner: ClickPlanner = plan_current_random_box_click,
        detail_scene_id: int = RANDOM_BOX_DETAIL_SCENE,
        operation_label: str = "随机箱",
        operation_template: str = "random_box",
        detail_only: bool = False,
        coarse_scrolls: bool = True,
        alignment_retries: int = 2,
        max_scrolls: int = 120,
        after_snapshot_retries: int = 4,
    ) -> None:
        self.context = context
        self.snapshot_reader = snapshot_reader
        # 上一件开箱后的 Runtime 快照：它同时是下一件的“动作前快照”。整单一件件
        # 开箱时快照读取本身要十几秒（548 件解码），复用它可以省掉一半读取；复用
        # 前仍按实例身份与数量校验，不成立就退回重新读取。
        self._reusable_after_snapshot: dict[str, Any] | None = None
        self.catalog_cards_by_id = catalog_cards_by_id
        self.click_planner = click_planner
        self.recorder = recorder
        self.wallet_snapshot_reader = wallet_snapshot_reader
        self.ring_inventory_reader = ring_inventory_reader
        self.detail_scene_id = int(detail_scene_id)
        self.operation_label = str(operation_label or "开箱")
        self.operation_template = str(operation_template or "")
        self.detail_only = bool(detail_only)
        self.coarse_scrolls = bool(coarse_scrolls)
        if self.operation_template not in {"random_box", "fixed_box"} and not (
            self.detail_only and self.operation_template == "special_use"
        ):
            raise ValueError("储物袋开箱 operation_template 必须是 random_box/fixed_box")
        if self.detail_scene_id not in {
            RANDOM_BOX_DETAIL_SCENE,
            FIXED_BOX_DETAIL_SCENE,
        } and not (self.detail_only and self.detail_scene_id in {610, 934}):
            raise ValueError("储物袋开箱详情场景必须是正式 #583/#585")
        self.alignment_retries = max(0, min(4, int(alignment_retries)))
        # 绝对上限只兜住失控情况；真实允许次数由 bounded_scroll_allowance
        # 按剩余距离推导，避免固定次数在物品列表变长后静默失效。
        self.max_scrolls = max(0, min(240, int(max_scrolls)))
        self.after_snapshot_retries = max(1, min(10, int(after_snapshot_retries)))

    def invalidate_reusable_snapshot(self) -> None:
        """Discard a post-open snapshot when another adapter has changed the bag."""

        self._reusable_after_snapshot = None

    def _take_reusable_snapshot(
        self, request: StorageBagRandomBoxRequest
    ) -> dict[str, Any] | None:
        """Reuse the previous open's post-action snapshot as this action's pre-state.

        Reading the 548-item Runtime list costs about 11s (2026-09-22 measured), and
        one open only changes the target stack plus its rewards, so the previous
        post-action snapshot is the exact current state of the next item.  It is
        reused only after the same instance/quantity validation a fresh read would
        get; otherwise the caller falls back to a new read.
        """

        candidate = self._reusable_after_snapshot
        self._reusable_after_snapshot = None
        if candidate is None:
            return None
        snapshot = dict(candidate)
        try:
            _target_runtime_item(snapshot, request)
        except StorageBagRandomBoxBlocked:
            return None
        return snapshot

    def open_detail(self, request: StorageBagRandomBoxRequest, *, snapshot: Mapping[str, Any] | None = None):
        """定位并核验箱子详情后停止，不点击打开/使用，不消耗道具。

        与 execute 共用同一 Runtime 身份、视窗配准和详情标题校验。
        snapshot 仅用于同一次调用链复用刚读取的库存；独立调用省略它。
        成功返回配准、标题核验和滚动统计，GUI 留在详情页供研发观察。
        """
        if request.base_id <= 0 or not request.instance_id or not request.name.strip():
            raise StorageBagRandomBoxBlocked("箱子详情请求缺少稳定身份")
        before = dict(snapshot) if snapshot is not None else dict(self.snapshot_reader())
        _snapshot_process_identity(before)
        _target_runtime_item(before, request)
        retry_count = 0
        scroll_count = 0
        # 滚动预算只对“当前锚点”生效：视窗每往前推进一次就重新锚定，预算按更近的
        # 剩余距离重算；于是正常推进不会被累计次数误判，只有同一锚点原地打转才耗尽。
        anchor_remaining: int | None = None
        anchor_scroll_count = 0
        known_top = False
        stagnant_up_batches = 0
        while True:
            planned = (
                self.click_planner(
                    self.context, before, request, known_viewport_start=0,
                )
                if known_top else self.click_planner(self.context, before, request)
            )
            plan = (yield from planned) if isinstance(planned, GeneratorType) else planned
            if plan.ready:
                break
            if plan.status in {"insufficient_observations", "ambiguous_offset"}:
                if retry_count >= self.alignment_retries:
                    raise StorageBagRandomBoxBlocked(
                        f"#525 对齐有限重试后仍不唯一：{plan.status}；{plan.reason}"
                    )
                retry_count += 1
                yield from self.context.wait_action_settle(0.2)
                continue
            if plan.status == "target_not_visible":
                if plan.viewport_runtime_start is None or not plan.observations:
                    raise StorageBagRandomBoxBlocked("#525 目标不可见但缺少唯一视窗起点")
                directive = plan_storage_bag_scroll(
                    target_runtime_index=int(plan.runtime_index),
                    viewport_runtime_start=int(plan.viewport_runtime_start),
                    visible_cell_count=max(obs.visible_index for obs in plan.observations) + 1,
                )
                if directive.direction == "none":
                    raise StorageBagRandomBoxBlocked("#525 滚动规划与不可见判定矛盾")
                if anchor_remaining is None or directive.remaining_items < anchor_remaining:
                    anchor_remaining = directive.remaining_items
                    anchor_scroll_count = scroll_count
                allowance = min(
                    self.max_scrolls,
                    bounded_scroll_allowance(anchor_remaining),
                )
                anchor_used = scroll_count - anchor_scroll_count
                if anchor_used >= allowance:
                    raise StorageBagRandomBoxBlocked(
                        "#525 有界滚动后目标仍不可见"
                        f"（同一锚点已滚动 {anchor_used} 次 / 允许 {allowance} 次，"
                        f"剩余 {directive.remaining_items} 格，累计 {scroll_count} 次，"
                        f"目标 Runtime 索引 {plan.runtime_index}，"
                        f"视窗起点 {plan.viewport_runtime_start}）"
                    )
                # 统一使用框架默认滚动手势（ratio 0.5、duration 1.5s）。2026-09-22 实测
                # 任务原先自定的 0.45s 快速手势在 #525 会被游戏忽略，整格不动。
                # 粗推进：同一份配准承担多次手势，下一次循环才重新识别配准。每把手势后
                # 都留足惯性停稳时间，否则窗口还在滑行时发出的下一把位移无法估计。
                batch = min(coarse_drag_batch(directive.remaining_items) if self.coarse_scrolls else 1,
                            allowance - anchor_used)
                before_frame = self.context.cur_frame(update=True)
                before_signature = self.context.image_signature_bytes_in_shape(
                    STORAGE_BAG_SCENE, "窗口", frame_data_url=before_frame,
                )
                for _ in range(max(1, batch)):
                    self.context.drag_shape_content(
                        STORAGE_BAG_SCENE,
                        "窗口",
                        direction=directive.direction,
                    )
                    scroll_count += 1
                    retry_count = 0
                    yield from self.context.wait_action_settle(STORAGE_BAG_DRAG_SETTLE_SECONDS)
                after_frame = self.context.cur_frame(update=True)
                after_signature = self.context.image_signature_bytes_in_shape(
                    STORAGE_BAG_SCENE, "窗口", frame_data_url=after_frame,
                )
                unchanged = self.context.image_signature_similarity(
                    before_signature, after_signature,
                ) >= 95.0
                # A visually unchanged upward batch at the top boundary can
                # expose a false quantity-only offset. The next registration
                # may use Runtime index zero only if every fresh OCR quantity
                # agrees with that prefix; otherwise it fails closed.
                stagnant_up_batches = (
                    stagnant_up_batches + 1
                    if directive.direction == "up" and unchanged else 0
                )
                known_top = stagnant_up_batches >= 2
                continue
            raise StorageBagRandomBoxBlocked(
                f"#525 目标定位失败：{plan.status}；{plan.reason}"
            )

        self.context.click_frame_point(STORAGE_BAG_SCENE, *plan.point)
        yield from self.context.wait_scene(
            [self.detail_scene_id],
            wait=8.0,
            label=f"储物袋{self.operation_label}：等待 #{self.detail_scene_id} 详情",
        )
        detail_frame = self.context.cur_frame(update=True)
        title_tokens = self.context.ocr_tokens_in_shapes(
            self.detail_scene_id,
            ("详情标题",),
            padding=6,
            frame_data_url=detail_frame,
            crop=True,
        )
        detail = verify_storage_bag_item_detail(
            plan,
            expected_name=request.name,
            detail_title_texts=_ordered_texts(title_tokens),
        )
        if not detail.confirmed:
            cleanup_error = ""
            try:
                yield from self.context.wait_click_then_scene(
                    self.detail_scene_id,
                    "外侧空白" if self.detail_scene_id == FIXED_BOX_DETAIL_SCENE else "右侧暗幕返回",
                    STORAGE_BAG_SCENE,
                    timeout=8.0,
                    label=f"储物袋{self.operation_label}：错误详情安全返回 #525",
                )
            except Exception as exc:  # pragma: no cover - real Runtime error detail
                cleanup_error = f"；返回 #525 失败：{exc}"
            raise StorageBagRandomBoxBlocked(
                f"#{self.detail_scene_id} 详情标题二次核验失败：{detail.reason}{cleanup_error}"
            )

        return {"plan": plan, "detail": detail, "scroll_count": scroll_count}

    def execute(
        self, request: StorageBagRandomBoxRequest
    ) -> Generator[Any, Any, StorageBagRandomBoxExecution]:
        if self.detail_only:
            raise StorageBagRandomBoxBlocked("仅详情适配器不能执行开箱或使用道具")
        open_count = request.requested_open_count
        if request.base_id <= 0 or not request.instance_id or not request.name.strip():
            raise StorageBagRandomBoxBlocked("随机箱请求缺少 base_id/instance_id/稳定名称")
        before = self._take_reusable_snapshot(request)
        if before is None:
            before = dict(self.snapshot_reader())
        process_identity = _snapshot_process_identity(before)
        _target_runtime_item(before, request)
        box_card = self.catalog_cards_by_id.get(str(request.base_id)) or {}
        from .storage_bag_ring import ring_box_reward_targets, verify_ring_reward_delta
        ring_targets = ring_box_reward_targets(box_card, self.catalog_cards_by_id)
        if ring_targets and self.ring_inventory_reader is None:
            raise StorageBagRandomBoxBlocked("灵环箱缺少独立库存验账读者，禁止开箱")
        wallet_targets = wallet_reward_targets(box_card, self.catalog_cards_by_id)
        if wallet_targets and self.wallet_snapshot_reader is None:
            raise StorageBagRandomBoxBlocked(
                "随机箱候选奖励包含钱包资源，但执行器没有钱包 Runtime 读取器"
            )
        wallet_before = (
            _read_wallet_amounts(
                self.wallet_snapshot_reader,
                wallet_targets,
                process_identity=process_identity,
            )
            if wallet_targets and self.wallet_snapshot_reader is not None
            else {}
        )

        opened_detail = yield from self.open_detail(request, snapshot=before)
        plan = opened_detail["plan"]
        detail = opened_detail["detail"]
        scroll_count = opened_detail["scroll_count"]

        yield from self.context.wait_click(
            self.detail_scene_id, "打开", timeout=8.0
        )
        yield from self.context.wait_scene(
            [USE_QUANTITY_SCENE],
            wait=8.0,
            label="储物袋随机箱：等待 #584 数量确认",
        )
        # 弹窗默认停在 1，而业务语义是“一叠开完”：把控件拉满即可，不需要读那个
        # 美术字数字（真机单位数根本检不出）。是否真的整叠开完由下面开箱后的
        # Runtime 差值证明，这里不做无法证明的读数断言。
        if request.open_quantity is None:
            yield from adjust_use_quantity(self.context, request.quantity)
        else:
            yield from adjust_bounded_use_quantity(self.context, open_count, maximum=request.quantity)

        ring_before = dict(self.ring_inventory_reader()) if ring_targets else None
        if ring_before is not None:
            # Identity/readiness is checked before the irreversible use; no
            # post-action failure may excuse an unobserved baseline.
            verify_ring_reward_delta(ring_before, ring_before, targets=ring_targets,
                                     opened_count=0, process_identity=process_identity)
        yield from self.context.wait_click(USE_QUANTITY_SCENE, "使用", timeout=8.0)
        landed = yield from self.context.wait_scene(
            [STORAGE_BAG_SCENE,
            TRANSIENT_REWARD_SCENE],
            wait=8.0,
            label="储物袋随机箱：等待结果或回到 #525",
        )
        if _view_id(landed) == TRANSIENT_REWARD_SCENE:
            yield from self.context.wait_scene(
                [STORAGE_BAG_SCENE],
                wait=8.0,
                label="储物袋随机箱：等待短暂结果层自动回到 #525",
            )
        elif _view_id(landed) != STORAGE_BAG_SCENE:
            raise StorageBagRandomBoxBlocked("随机箱使用后落点不是 #578/#525")

        after: dict[str, Any] | None = None
        observations: list[dict[str, Any]] = []
        for attempt in range(self.after_snapshot_retries):
            candidate = dict(self.snapshot_reader())
            identity_error = ""
            try:
                candidate_identity = _snapshot_process_identity(candidate)
            except StorageBagRandomBoxBlocked as exc:
                candidate_identity = (-1, -1)
                identity_error = str(exc)
            observations.append({
                "complete": candidate.get("complete"),
                "identity": candidate_identity,
                "identity_error": identity_error,
                "fingerprint": candidate.get("fingerprint"),
                "target_quantity": [row.get("num") for row in candidate.get("items") or []
                                    if str(row.get("instance_id")) == request.instance_id],
                "reason": candidate.get("reason") or candidate.get("error"),
            })
            if (
                candidate_identity == process_identity
                and candidate.get("fingerprint") != before.get("fingerprint")
            ):
                after = candidate
                break
            if attempt + 1 < self.after_snapshot_retries:
                yield from self.context.wait_action_settle(0.2)
        if after is None:
            raise StorageBagRandomBoxBlocked(
                "使用后未取得同进程、完整且已变化的 Runtime 快照；"
                f"target={request!r} before={before.get('fingerprint')} "
                f"process={process_identity} observations={observations!r}"
            )

        wallet_after = (
            _read_wallet_amounts(
                self.wallet_snapshot_reader,
                wallet_targets,
                process_identity=process_identity,
            )
            if wallet_targets and self.wallet_snapshot_reader is not None
            else {}
        )
        wallet_rewards = _wallet_reward_deltas(
            wallet_before, wallet_after, wallet_targets
        )
        ring_after = dict(self.ring_inventory_reader()) if ring_targets else None
        ring_rewards = []
        if ring_after is not None:
            remaining = sum(row.get("num", 0) for row in after["items"]
                            if row.get("instance_id") == request.instance_id and not row.get("is_padding"))
            actual_opened = request.quantity - remaining
            if not 0 < actual_opened <= open_count:
                raise StorageBagRandomBoxBlocked("灵环箱实际消费量不符合本次请求")
            ring_rewards = verify_ring_reward_delta(ring_before, ring_after, targets=ring_targets,
                opened_count=actual_opened, process_identity=process_identity)

        delta = derive_storage_bag_open_delta(
            before,
            after,
            target_base_id=request.base_id,
            target_instance_id=request.instance_id,
            catalog_cards_by_id=self.catalog_cards_by_id,
            additional_rewards=[*wallet_rewards, *ring_rewards],
        )
        if ring_targets and (any(reward.get("item_id") not in ring_targets for reward in delta.rewards)
                             or sum(reward["quantity"] for reward in delta.rewards) != delta.opened_count):
            raise StorageBagRandomBoxBlocked("灵环箱收益发生重复归因或候选之外变化")
        if delta.opened_count <= 0:
            raise StorageBagRandomBoxBlocked("Runtime 没有记录到任何目标实例消耗")
        if delta.opened_count > open_count:
            raise StorageBagRandomBoxBlocked(
                f"Runtime 实际开启数量 {delta.opened_count} 超过计划 {open_count}"
            )
        # 游戏对单次“使用”数量有自己的上限：2026-09-22 实测万兽鼎馈赠 x66 单次只放行
        # 43 个，而同一批的 2052/1032 大叠都能一次开完。少开不属于失败关闭条件——
        # 按实际消耗记账，剩余数量由下一次整单重入按当前 Runtime 事实继续消费。
        # 这次开箱后的快照就是下一件的动作前事实，留给下一次 execute 复用。
        self._reusable_after_snapshot = dict(after)
        # 指纹仅用于幂等，不能解释异常增量。凭证保留本次箱子与实际到账
        # 资源的前后库存及采样时间，便于核查额外到账或跨次旧数据；钱包另记。
        evidence_ids = {request.base_id} | {int(r["item_id"]) for r in delta.rewards}
        def inventory_evidence(snapshot):
            totals = {base: 0 for base in evidence_ids}
            for row in snapshot["items"]:
                if not row.get("is_padding") and row.get("base_id") in totals:
                    totals[row["base_id"]] += row["num"]
            return tuple(sorted(totals.items()))
        execution = StorageBagRandomBoxExecution(
            request=request,
            delta=delta,
            action_key=_action_key(request, delta, self.operation_template),
            operation_template=self.operation_template,
            detail_observed_name=detail.observed_name,
            detail_similarity=detail.similarity,
            scroll_count=scroll_count,
            wallet_before=tuple(sorted(wallet_before.items())),
            wallet_after=tuple(sorted(wallet_after.items())),
            inventory_before=inventory_evidence(before),
            inventory_after=inventory_evidence(after),
            observed_at_before=before.get("observed_at"),
            observed_at_after=after.get("observed_at"),
            process_identity=process_identity,
            ring_inventory_before=ring_before,
            ring_inventory_after=ring_after,
        )
        self.recorder(execution)
        return execution


class StorageBagFixedBoxGuiAdapter(StorageBagRandomBoxGuiAdapter):
    """The same box interaction family, bound to the fixed-reward detail identity."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            **kwargs,
            detail_scene_id=FIXED_BOX_DETAIL_SCENE,
            operation_label="固定箱",
            operation_template="fixed_box",
        )


__all__ = [
    "FIXED_BOX_DETAIL_SCENE",
    "RANDOM_BOX_DETAIL_SCENE",
    "STORAGE_BAG_SCENE",
    "TRANSIENT_REWARD_SCENE",
    "USE_QUANTITY_SCENE",
    "StorageBagRandomBoxBlocked",
    "StorageBagRandomBoxExecution",
    "StorageBagFixedBoxGuiAdapter",
    "StorageBagRandomBoxGuiAdapter",
    "StorageBagRandomBoxRequest",
    "parse_confirmed_use_quantity",
    "require_stable_storage_bag_plan_frame",
    "read_confirmed_use_quantity",
    "read_live_use_quantity",
    "drag_use_quantity_slider_to_end",
    "adjust_use_quantity",
    "wallet_reward_targets",
    "plan_current_random_box_click",
    "record_box_execution",
]
