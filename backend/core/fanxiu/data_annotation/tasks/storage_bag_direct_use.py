from __future__ import annotations

"""Fail-closed transaction adapter for direct-use spirit stones.

This module deliberately remains outside the production auto-claim registry
until one real Runtime run has verified the #610 -> #584 action chain.  The
game Runtime owns item identity and quantities; OCR is used only to bind that
already-authorized instance to the two visible confirmation pages.
"""

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    STORAGE_BAG_SCENE,
    TRANSIENT_REWARD_SCENE,
    USE_QUANTITY_SCENE,
    StorageBagRandomBoxRequest,
    plan_current_random_box_click,
    read_confirmed_use_quantity,
)
from backend.core.fanxiu.runtime_gui import (
    StorageBagItemClickPlan,
    plan_storage_bag_scroll,
    verify_storage_bag_item_detail,
)


DIRECT_ITEM_DETAIL_SCENE = 610
SPIRIT_STONE_BASE_ID = 1001
SPIRIT_STONE_NAME = "灵石"
SPIRIT_STONE_WALLET_TYPE = 1


class StorageBagDirectUseBlocked(RuntimeError):
    """The adapter could not prove an authorized direct-use transaction."""


@dataclass(frozen=True)
class StorageBagDirectUseRequest:
    base_id: int
    instance_id: str
    name: str
    quantity: int


@dataclass(frozen=True)
class StorageBagDirectUseExecution:
    request: StorageBagDirectUseRequest
    before_fingerprint: str
    after_fingerprint: str
    wallet_before: int
    wallet_after: int
    detail_observed_name: str
    quantity_observed_name: str
    scroll_count: int

    @property
    def consumed_quantity(self) -> int:
        return self.request.quantity

    @property
    def wallet_delta(self) -> int:
        return self.wallet_after - self.wallet_before


SnapshotReader = Callable[[], Mapping[str, Any]]
WalletSnapshotReader = Callable[[int], Mapping[str, Any]]
ClickPlanner = Callable[
    [Any, Mapping[str, Any], StorageBagDirectUseRequest], StorageBagItemClickPlan
]
Clock = Callable[[], datetime]


def _view_id(value: Any) -> int | None:
    raw = getattr(value, "id", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _ordered_texts(tokens: list[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(token.get("text") or "").strip()
        for token in sorted(
            tokens,
            key=lambda token: (
                float(token.get("y") or 0),
                float(token.get("x") or 0),
            ),
        )
        if str(token.get("text") or "").strip()
    )


def parse_exact_positive_quantity(
    tokens: list[Mapping[str, Any]],
    *,
    label: str = "",
) -> int:
    """Parse one positive integer, optionally following a fixed UI label."""

    compact = "".join(_ordered_texts(tokens)).replace(" ", "")
    if label:
        marker = label.replace(" ", "")
        index = compact.find(marker)
        if index < 0:
            raise StorageBagDirectUseBlocked(f"数量 OCR 缺少标签 {label}")
        compact = compact[index + len(marker) :]
    values = parse_ocr_values(compact) if compact else None
    if values is None or len(values) != 1 or values[0] <= 0:
        raise StorageBagDirectUseBlocked("数量 OCR 不是唯一正整数")
    return int(values[0])


def _snapshot_identity(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    try:
        identity = (
            int(evidence.get("pid") or 0),
            int(evidence.get("process_start_ticks") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise StorageBagDirectUseBlocked("储物袋 Runtime 进程身份无效") from exc
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
        or not str(snapshot.get("fingerprint") or "")
        or min(identity) <= 0
    ):
        raise StorageBagDirectUseBlocked(
            "储物袋 Runtime 不完整、来源错误或缺少进程/指纹证据"
        )
    return identity


def _instance_map(snapshot: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for raw in snapshot.get("items") or []:
        if not isinstance(raw, Mapping) or raw.get("is_padding"):
            continue
        instance_id = str(raw.get("instance_id") or "").strip()
        try:
            base_id = int(raw.get("base_id") or 0)
            quantity = int(raw.get("num") or 0)
        except (TypeError, ValueError) as exc:
            raise StorageBagDirectUseBlocked("储物袋 Runtime 物品字段无效") from exc
        if (
            not instance_id
            or base_id <= 0
            or quantity < 0
            or instance_id in result
        ):
            raise StorageBagDirectUseBlocked("储物袋 Runtime 实例身份无效或重复")
        result[instance_id] = (base_id, quantity)
    return result


def _require_target(
    snapshot: Mapping[str, Any], request: StorageBagDirectUseRequest
) -> None:
    target = _instance_map(snapshot).get(request.instance_id)
    if target != (request.base_id, request.quantity) or request.quantity <= 0:
        raise StorageBagDirectUseBlocked(
            "动作前 Runtime 没有唯一且数量一致的目标实例"
        )


def _parse_capture_time(value: Any) -> datetime:
    try:
        captured_at = datetime.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise StorageBagDirectUseBlocked("钱包 Runtime 缺少有效 captured_at") from exc
    if captured_at.tzinfo is None:
        raise StorageBagDirectUseBlocked("钱包 Runtime captured_at 缺少时区")
    return captured_at


def _wallet_amount(
    snapshot: Mapping[str, Any],
    *,
    process_identity: tuple[int, int],
) -> tuple[int, datetime]:
    evidence = snapshot.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    try:
        identity = (
            int(evidence.get("pid") or 0),
            int(evidence.get("process_start_ticks") or 0),
        )
        currency_type = int(snapshot.get("currency_type") or 0)
        amount = int(snapshot.get("exchange_currency"))
    except (TypeError, ValueError) as exc:
        raise StorageBagDirectUseBlocked("钱包 Runtime 数值或进程身份无效") from exc
    if (
        snapshot.get("source") != "runtime_memory"
        or currency_type != SPIRIT_STONE_WALLET_TYPE
        or identity != process_identity
        or amount < 0
    ):
        raise StorageBagDirectUseBlocked(
            "Wallet type=1 快照来源、币种、进程身份或数量无效"
        )
    return amount, _parse_capture_time(snapshot.get("captured_at"))


def _require_fresh_wallet_capture(
    captured_at: datetime,
    *,
    now: datetime,
    max_age_seconds: float,
) -> None:
    if now.tzinfo is None:
        raise StorageBagDirectUseBlocked("事务时钟缺少时区")
    age = (now - captured_at).total_seconds()
    # ISO snapshots are serialized to whole seconds, so tolerate a small
    # forward skew while still rejecting a persisted/replayed old fact.
    if age < -2.0 or age > max_age_seconds:
        raise StorageBagDirectUseBlocked(
            f"钱包 Runtime 快照不新鲜（age={age:.1f}s）"
        )


def verify_spirit_stone_direct_use_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    request: StorageBagDirectUseRequest,
    wallet_before: Mapping[str, Any],
    wallet_after: Mapping[str, Any],
) -> StorageBagDirectUseExecution:
    """Prove exact ``bag -N`` and ``Wallet(type=1) +N`` in one process."""

    if (
        request.base_id != SPIRIT_STONE_BASE_ID
        or request.name.strip() != SPIRIT_STONE_NAME
        or not request.instance_id
        or request.quantity <= 0
    ):
        raise StorageBagDirectUseBlocked("事务适配器只授权 base1001 灵石正数量")
    process_identity = _snapshot_identity(before)
    if _snapshot_identity(after) != process_identity:
        raise StorageBagDirectUseBlocked("动作前后储物袋 Runtime 不是同一游戏进程")
    _require_target(before, request)
    if before.get("fingerprint") == after.get("fingerprint"):
        raise StorageBagDirectUseBlocked("动作前后储物袋 Runtime 指纹没有变化")

    before_items = _instance_map(before)
    after_items = _instance_map(after)
    target_after = after_items.get(request.instance_id)
    if target_after is not None and target_after[0] != request.base_id:
        raise StorageBagDirectUseBlocked("动作后目标 instance_id 被不同 base_id 复用")
    remaining = target_after[1] if target_after is not None else 0
    if request.quantity - remaining != request.quantity:
        raise StorageBagDirectUseBlocked("灵石背包数量未精确减少请求数量")

    before_other = dict(before_items)
    after_other = dict(after_items)
    before_other.pop(request.instance_id)
    after_other.pop(request.instance_id, None)
    if before_other != after_other:
        raise StorageBagDirectUseBlocked("直接使用灵石时其它背包实例发生变化")

    before_amount, before_at = _wallet_amount(
        wallet_before, process_identity=process_identity
    )
    after_amount, after_at = _wallet_amount(
        wallet_after, process_identity=process_identity
    )
    if after_at < before_at:
        raise StorageBagDirectUseBlocked("动作后钱包 Runtime 快照时间发生倒退")
    if after_amount - before_amount != request.quantity:
        raise StorageBagDirectUseBlocked("Wallet type=1 未精确增加请求数量")

    return StorageBagDirectUseExecution(
        request=request,
        before_fingerprint=str(before["fingerprint"]),
        after_fingerprint=str(after["fingerprint"]),
        wallet_before=before_amount,
        wallet_after=after_amount,
        detail_observed_name="",
        quantity_observed_name="",
        scroll_count=0,
    )


def _default_click_planner(
    runtime: Any,
    snapshot: Mapping[str, Any],
    request: StorageBagDirectUseRequest,
) -> StorageBagItemClickPlan:
    return plan_current_random_box_click(
        runtime,
        snapshot,
        StorageBagRandomBoxRequest(
            request.base_id,
            request.instance_id,
            request.name,
            request.quantity,
        ),
    )


class StorageBagSpiritStoneGuiAdapter:
    """Isolated #525 -> #610 -> #584 direct-use transaction."""

    def __init__(
        self,
        *,
        runtime: Any,
        snapshot_reader: SnapshotReader,
        wallet_snapshot_reader: WalletSnapshotReader,
        click_planner: ClickPlanner = _default_click_planner,
        clock: Clock = lambda: datetime.now().astimezone(),
        max_wallet_age_seconds: float = 10.0,
        alignment_retries: int = 2,
        max_scrolls: int = 12,
        after_snapshot_retries: int = 5,
    ) -> None:
        self.runtime = runtime
        self.snapshot_reader = snapshot_reader
        self.wallet_snapshot_reader = wallet_snapshot_reader
        self.click_planner = click_planner
        self.clock = clock
        self.max_wallet_age_seconds = max(2.0, float(max_wallet_age_seconds))
        self.alignment_retries = max(0, min(4, int(alignment_retries)))
        self.max_scrolls = max(0, min(30, int(max_scrolls)))
        self.after_snapshot_retries = max(
            1, min(10, int(after_snapshot_retries))
        )

    def _ocr(self, scene: int, shape: str, frame: str) -> list[Mapping[str, Any]]:
        return self.runtime.ocr_tokens_in_shapes(
            scene,
            (shape,),
            padding=6,
            frame_data_url=frame,
            crop=True,
        )

    def execute(
        self, request: StorageBagDirectUseRequest
    ) -> Generator[Any, Any, StorageBagDirectUseExecution]:
        if (
            request.base_id != SPIRIT_STONE_BASE_ID
            or request.name.strip() != SPIRIT_STONE_NAME
            or not request.instance_id
            or request.quantity <= 0
        ):
            raise StorageBagDirectUseBlocked(
                "灵石事务请求必须是 base1001、稳定 instance_id 与正数量"
            )

        before = dict(self.snapshot_reader())
        process_identity = _snapshot_identity(before)
        _require_target(before, request)

        retry_count = 0
        scroll_count = 0
        while True:
            plan = self.click_planner(self.runtime, before, request)
            if plan.ready:
                break
            if plan.status in {"insufficient_observations", "ambiguous_offset"}:
                if retry_count >= self.alignment_retries:
                    raise StorageBagDirectUseBlocked(
                        f"#525 对齐有限重试后仍不唯一：{plan.status}；{plan.reason}"
                    )
                retry_count += 1
                yield from self.runtime.wait_action_settle(0.2)
                continue
            if plan.status == "target_not_visible":
                if scroll_count >= self.max_scrolls:
                    raise StorageBagDirectUseBlocked("#525 有界滚动后灵石仍不可见")
                if plan.viewport_runtime_start is None or not plan.observations:
                    raise StorageBagDirectUseBlocked(
                        "#525 灵石不可见但缺少唯一视窗起点"
                    )
                directive = plan_storage_bag_scroll(
                    target_runtime_index=int(plan.runtime_index),
                    viewport_runtime_start=plan.viewport_runtime_start,
                    visible_cell_count=max(
                        observation.visible_index for observation in plan.observations
                    )
                    + 1,
                )
                if directive.direction == "none":
                    raise StorageBagDirectUseBlocked("#525 滚动规划与不可见判定矛盾")
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
            raise StorageBagDirectUseBlocked(
                f"#525 灵石定位失败：{plan.status}；{plan.reason}"
            )

        planned_item = plan.runtime_item or {}
        try:
            planned_identity = (
                str(planned_item.get("instance_id") or ""),
                int(planned_item.get("base_id") or 0),
                int(planned_item.get("num") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise StorageBagDirectUseBlocked(
                "#525 点击计划的 Runtime 实例字段无效"
            ) from exc
        if planned_identity != (
            request.instance_id,
            request.base_id,
            request.quantity,
        ) or plan.point is None:
            raise StorageBagDirectUseBlocked("#525 点击计划没有绑定请求的精确 Runtime 实例")

        # Alignment/OCR can take seconds.  Re-read the active panel immediately
        # before the first click so a stale plan cannot authorize a changed
        # inventory, then acquire the wallet baseline in the same narrow window.
        pre_click = dict(self.snapshot_reader())
        if (
            _snapshot_identity(pre_click) != process_identity
            or pre_click.get("fingerprint") != before.get("fingerprint")
        ):
            raise StorageBagDirectUseBlocked("#525 点击前储物袋 Runtime 已变化，旧计划失效")
        _require_target(pre_click, request)
        before = pre_click
        wallet_before = dict(
            self.wallet_snapshot_reader(SPIRIT_STONE_WALLET_TYPE)
        )
        _before_amount, before_wallet_at = _wallet_amount(
            wallet_before, process_identity=process_identity
        )
        _require_fresh_wallet_capture(
            before_wallet_at,
            now=self.clock(),
            max_age_seconds=self.max_wallet_age_seconds,
        )

        self.runtime.click_frame_point(STORAGE_BAG_SCENE, *plan.point)
        yield from self.runtime.wait_view(
            DIRECT_ITEM_DETAIL_SCENE,
            timeout=8.0,
            label="储物袋灵石：等待 #610 普通物品详情",
        )
        detail_frame = self.runtime.cur_frame(update=True)
        detail = verify_storage_bag_item_detail(
            plan,
            expected_name=request.name,
            detail_title_texts=_ordered_texts(
                self._ocr(DIRECT_ITEM_DETAIL_SCENE, "物品标题", detail_frame)
            ),
        )
        detail_held = parse_exact_positive_quantity(
            self._ocr(DIRECT_ITEM_DETAIL_SCENE, "持有数量", detail_frame)
        )
        if not detail.confirmed or detail_held != request.quantity:
            raise StorageBagDirectUseBlocked(
                "#610 物品标题或持有数量与 Runtime 灵石实例不一致"
            )

        yield from self.runtime.wait_click(
            DIRECT_ITEM_DETAIL_SCENE,
            "使用（高风险）",
            timeout=8.0,
        )
        yield from self.runtime.wait_view(
            USE_QUANTITY_SCENE,
            timeout=8.0,
            label="储物袋灵石：等待 #584 数量确认",
        )
        quantity_frame = self.runtime.cur_frame(update=True)
        quantity_detail = verify_storage_bag_item_detail(
            plan,
            expected_name=request.name,
            detail_title_texts=_ordered_texts(
                self._ocr(USE_QUANTITY_SCENE, "物品标题", quantity_frame)
            ),
        )
        quantity_held = parse_exact_positive_quantity(
            self._ocr(USE_QUANTITY_SCENE, "持有数量", quantity_frame),
            label="持有数量",
        )
        current_quantity = read_confirmed_use_quantity(
            self.runtime, quantity_frame
        )
        if (
            not quantity_detail.confirmed
            or quantity_held != request.quantity
            or current_quantity != request.quantity
        ):
            raise StorageBagDirectUseBlocked(
                "#584 物品标题、持有数量或当前数量与 Runtime 灵石实例不一致"
            )

        yield from self.runtime.wait_click(
            USE_QUANTITY_SCENE, "使用", timeout=8.0
        )
        landed = yield from self.runtime.wait_view(
            STORAGE_BAG_SCENE,
            TRANSIENT_REWARD_SCENE,
            timeout=8.0,
            label="储物袋灵石：等待结果或回到 #525",
        )
        if _view_id(landed) == TRANSIENT_REWARD_SCENE:
            yield from self.runtime.wait_view(
                STORAGE_BAG_SCENE,
                timeout=8.0,
                label="储物袋灵石：等待短暂结果层返回 #525",
            )
        elif _view_id(landed) != STORAGE_BAG_SCENE:
            raise StorageBagDirectUseBlocked("灵石使用后落点不是 #578/#525")

        last_error: Exception | None = None
        for attempt in range(self.after_snapshot_retries):
            after = dict(self.snapshot_reader())
            wallet_after = dict(
                self.wallet_snapshot_reader(SPIRIT_STONE_WALLET_TYPE)
            )
            try:
                _after_amount, after_wallet_at = _wallet_amount(
                    wallet_after, process_identity=process_identity
                )
                _require_fresh_wallet_capture(
                    after_wallet_at,
                    now=self.clock(),
                    max_age_seconds=self.max_wallet_age_seconds,
                )
                result = verify_spirit_stone_direct_use_delta(
                    before,
                    after,
                    request=request,
                    wallet_before=wallet_before,
                    wallet_after=wallet_after,
                )
            except StorageBagDirectUseBlocked as exc:
                last_error = exc
                if attempt + 1 < self.after_snapshot_retries:
                    yield from self.runtime.wait_action_settle(0.2)
                    continue
                break
            return StorageBagDirectUseExecution(
                request=request,
                before_fingerprint=result.before_fingerprint,
                after_fingerprint=result.after_fingerprint,
                wallet_before=result.wallet_before,
                wallet_after=result.wallet_after,
                detail_observed_name=detail.observed_name,
                quantity_observed_name=quantity_detail.observed_name,
                scroll_count=scroll_count,
            )
        raise StorageBagDirectUseBlocked(
            f"最终 Runtime 双差值有限重试仍未成立：{last_error}"
        )


__all__ = [
    "DIRECT_ITEM_DETAIL_SCENE",
    "SPIRIT_STONE_BASE_ID",
    "SPIRIT_STONE_NAME",
    "SPIRIT_STONE_WALLET_TYPE",
    "StorageBagDirectUseBlocked",
    "StorageBagDirectUseExecution",
    "StorageBagDirectUseRequest",
    "StorageBagSpiritStoneGuiAdapter",
    "parse_exact_positive_quantity",
    "verify_spirit_stone_direct_use_delta",
]
