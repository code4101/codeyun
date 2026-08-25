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
import re
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


class StorageBagRandomBoxBlocked(RuntimeError):
    """The adapter could not prove a unique, authorized next action."""


@dataclass(frozen=True)
class StorageBagRandomBoxRequest:
    base_id: int
    instance_id: str
    name: str
    quantity: int


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

    def evidence(self) -> dict[str, Any]:
        return {
            "adapter": f"storage_bag_{self.operation_template}_v1",
            "instance_id": self.request.instance_id,
            "expected_quantity": self.request.quantity,
            "detail_observed_name": self.detail_observed_name,
            "detail_similarity": self.detail_similarity,
            "scroll_count": self.scroll_count,
            "wallet_before": dict(self.wallet_before),
            "wallet_after": dict(self.wallet_after),
        }


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
    runtime: Any,
    snapshot: Mapping[str, Any],
    request: StorageBagRandomBoxRequest,
) -> StorageBagItemClickPlan:
    """Re-register one fresh #525 frame and resolve the exact instance."""

    view = runtime.view(STORAGE_BAG_SCENE)
    current_data_url = runtime.cur_frame(update=True)
    scene_id, _score, _frame = runtime.observe_scene(
        frame_data_url=current_data_url
    )
    if scene_id != STORAGE_BAG_SCENE:
        raise StorageBagRandomBoxBlocked("定位前全图模型未把当前帧唯一识别为 #525")
    reference_data_url = str(view.raw.get("dataUrl") or "")
    if not reference_data_url:
        # Formal trees normally store reference frames as sibling PNG files,
        # not multi-megabyte data URLs inside asset-tree.json.
        reference_data_url = runtime.runner._scene_frame_data_url_from_reference(
            runtime.ctx, view.raw
        )
    current = _decode_frame(current_data_url)
    reference = _decode_frame(reference_data_url)
    height, width = current.shape[:2]
    if reference.shape[:2] != current.shape[:2]:
        raise StorageBagRandomBoxBlocked("#525 参考帧与当前帧尺寸不一致")
    shape_names = ("窗口", "第1行第1个", "第1行第2个", "第2行第1个", "行间隙")
    shapes = {name: runtime.shape(STORAGE_BAG_SCENE, name).raw for name in shape_names}
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
    tokens = runtime.full_frame_ocr_tokens(frame_data_url=current_data_url)
    observations = quantity_observations_from_ocr(cells, tokens)
    plan = plan_storage_bag_item_click(
        snapshot,
        target_base_id=request.base_id,
        target_instance_id=request.instance_id,
        cells=cells,
        observations=observations,
    )
    # A quantity sequence proves the mapping only for ``current_data_url``.
    # Re-sample the annotated window after the expensive OCR pass.  If the
    # list coasted during OCR, the old point must never escape as ``ready``;
    # the adapter's existing bounded alignment retry will plan from the fresh
    # settled frame instead.
    if plan.viewport_runtime_start is None:
        return plan
    try:
        before_signature = runtime.image_signature_bytes_in_shape(
            STORAGE_BAG_SCENE,
            "窗口",
            frame_data_url=current_data_url,
        )
        verification_data_url = runtime.cur_frame(update=True)
        after_signature = runtime.image_signature_bytes_in_shape(
            STORAGE_BAG_SCENE,
            "窗口",
            frame_data_url=verification_data_url,
        )
        similarity = runtime.image_signature_similarity(
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


def read_confirmed_use_quantity(runtime: Any, frame_data_url: str) -> int:
    """Read #584's small orange integer, with a bounded enlarged-ROI fallback."""

    tokens = runtime.ocr_tokens_in_shapes(
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
    shape = runtime.shape(USE_QUANTITY_SCENE, "当前数量").raw
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
    """Reusable #525/detail/#584 box adapter with injectable Runtime readers."""

    def __init__(
        self,
        *,
        runtime: Any,
        snapshot_reader: SnapshotReader,
        catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
        recorder: ExecutionRecorder,
        wallet_snapshot_reader: WalletSnapshotReader | None = None,
        click_planner: ClickPlanner = plan_current_random_box_click,
        detail_scene_id: int = RANDOM_BOX_DETAIL_SCENE,
        operation_label: str = "随机箱",
        operation_template: str = "random_box",
        alignment_retries: int = 2,
        max_scrolls: int = 12,
        after_snapshot_retries: int = 4,
    ) -> None:
        self.runtime = runtime
        self.snapshot_reader = snapshot_reader
        self.catalog_cards_by_id = catalog_cards_by_id
        self.click_planner = click_planner
        self.recorder = recorder
        self.wallet_snapshot_reader = wallet_snapshot_reader
        self.detail_scene_id = int(detail_scene_id)
        self.operation_label = str(operation_label or "开箱")
        self.operation_template = str(operation_template or "")
        if self.operation_template not in {"random_box", "fixed_box"}:
            raise ValueError("储物袋开箱 operation_template 必须是 random_box/fixed_box")
        if self.detail_scene_id not in {
            RANDOM_BOX_DETAIL_SCENE,
            FIXED_BOX_DETAIL_SCENE,
        }:
            raise ValueError("储物袋开箱详情场景必须是正式 #583/#585")
        self.alignment_retries = max(0, min(4, int(alignment_retries)))
        self.max_scrolls = max(0, min(30, int(max_scrolls)))
        self.after_snapshot_retries = max(1, min(10, int(after_snapshot_retries)))

    def execute(
        self, request: StorageBagRandomBoxRequest
    ) -> Generator[Any, Any, StorageBagRandomBoxExecution]:
        if request.base_id <= 0 or not request.instance_id or not request.name.strip():
            raise StorageBagRandomBoxBlocked("随机箱请求缺少 base_id/instance_id/稳定名称")
        before = dict(self.snapshot_reader())
        process_identity = _snapshot_process_identity(before)
        _target_runtime_item(before, request)
        box_card = self.catalog_cards_by_id.get(str(request.base_id)) or {}
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

        retry_count = 0
        scroll_count = 0
        while True:
            plan = self.click_planner(self.runtime, before, request)
            if plan.ready:
                break
            if plan.status in {"insufficient_observations", "ambiguous_offset"}:
                if retry_count >= self.alignment_retries:
                    raise StorageBagRandomBoxBlocked(
                        f"#525 对齐有限重试后仍不唯一：{plan.status}；{plan.reason}"
                    )
                retry_count += 1
                yield from self.runtime.wait_action_settle(0.2)
                continue
            if plan.status == "target_not_visible":
                if scroll_count >= self.max_scrolls:
                    raise StorageBagRandomBoxBlocked("#525 有界滚动后目标仍不可见")
                if plan.viewport_runtime_start is None or not plan.observations:
                    raise StorageBagRandomBoxBlocked("#525 目标不可见但缺少唯一视窗起点")
                directive = plan_storage_bag_scroll(
                    target_runtime_index=int(plan.runtime_index),
                    viewport_runtime_start=plan.viewport_runtime_start,
                    visible_cell_count=max(obs.visible_index for obs in plan.observations) + 1,
                )
                if directive.direction == "none":
                    raise StorageBagRandomBoxBlocked("#525 滚动规划与不可见判定矛盾")
                self.runtime.drag_shape_content(
                    STORAGE_BAG_SCENE,
                    "窗口",
                    direction=directive.direction,
                    ratio=0.72 if directive.mode == "coarse" else 0.38,
                    duration=0.45,
                )
                scroll_count += 1
                retry_count = 0
                yield from self.runtime.wait_action_settle(0.25)
                continue
            raise StorageBagRandomBoxBlocked(
                f"#525 目标定位失败：{plan.status}；{plan.reason}"
            )

        self.runtime.click_frame_point(STORAGE_BAG_SCENE, *plan.point)
        yield from self.runtime.wait_view(
            self.detail_scene_id,
            timeout=8.0,
            label=f"储物袋{self.operation_label}：等待 #{self.detail_scene_id} 详情",
        )
        detail_frame = self.runtime.cur_frame(update=True)
        title_tokens = self.runtime.ocr_tokens_in_shapes(
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
                yield from self.runtime.wait_click_then_view(
                    self.detail_scene_id,
                    "右侧暗幕返回",
                    STORAGE_BAG_SCENE,
                    timeout=8.0,
                    label=f"储物袋{self.operation_label}：错误详情安全返回 #525",
                )
            except Exception as exc:  # pragma: no cover - real Runtime error detail
                cleanup_error = f"；返回 #525 失败：{exc}"
            raise StorageBagRandomBoxBlocked(
                f"#{self.detail_scene_id} 详情标题二次核验失败：{detail.reason}{cleanup_error}"
            )

        yield from self.runtime.wait_click(
            self.detail_scene_id, "打开", timeout=8.0
        )
        yield from self.runtime.wait_view(
            USE_QUANTITY_SCENE,
            timeout=8.0,
            label="储物袋随机箱：等待 #584 数量确认",
        )
        quantity_frame = self.runtime.cur_frame(update=True)
        confirmed_quantity = read_confirmed_use_quantity(self.runtime, quantity_frame)
        if confirmed_quantity != request.quantity:
            raise StorageBagRandomBoxBlocked(
                f"#584 当前数量 {confirmed_quantity} != 目标 Runtime 数量 {request.quantity}，拒绝使用"
            )

        yield from self.runtime.wait_click(USE_QUANTITY_SCENE, "使用", timeout=8.0)
        landed = yield from self.runtime.wait_view(
            STORAGE_BAG_SCENE,
            TRANSIENT_REWARD_SCENE,
            timeout=8.0,
            label="储物袋随机箱：等待结果或回到 #525",
        )
        if _view_id(landed) == TRANSIENT_REWARD_SCENE:
            yield from self.runtime.wait_view(
                STORAGE_BAG_SCENE,
                timeout=8.0,
                label="储物袋随机箱：等待短暂结果层自动回到 #525",
            )
        elif _view_id(landed) != STORAGE_BAG_SCENE:
            raise StorageBagRandomBoxBlocked("随机箱使用后落点不是 #578/#525")

        after: dict[str, Any] | None = None
        for attempt in range(self.after_snapshot_retries):
            candidate = dict(self.snapshot_reader())
            try:
                candidate_identity = _snapshot_process_identity(candidate)
            except StorageBagRandomBoxBlocked:
                candidate_identity = (-1, -1)
            if (
                candidate_identity == process_identity
                and candidate.get("fingerprint") != before.get("fingerprint")
            ):
                after = candidate
                break
            if attempt + 1 < self.after_snapshot_retries:
                yield from self.runtime.wait_action_settle(0.2)
        if after is None:
            raise StorageBagRandomBoxBlocked("使用后未取得同进程、完整且已变化的 Runtime 快照")

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

        delta = derive_storage_bag_open_delta(
            before,
            after,
            target_base_id=request.base_id,
            target_instance_id=request.instance_id,
            catalog_cards_by_id=self.catalog_cards_by_id,
            additional_rewards=wallet_rewards,
        )
        if delta.opened_count != request.quantity:
            raise StorageBagRandomBoxBlocked("Runtime 实际开启数量不等于 #584 已确认全量")
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
    "wallet_reward_targets",
    "plan_current_random_box_click",
    "record_box_execution",
]
