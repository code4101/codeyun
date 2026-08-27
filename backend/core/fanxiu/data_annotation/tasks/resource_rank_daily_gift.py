from __future__ import annotations

"""Daily zero-cost gift claim for the shared resource-ranking page family."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE
from backend.core.fanxiu.activity.daily_activity_sync import (
    load_worldline_activity_schedule_snapshot,
)
from backend.core.fanxiu.activity.runtime_schedule import (
    get_cached_fanxiu_activity_runtime_schedule,
)
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    CALENDAR_SHAPE,
    HEADER_SHAPE,
    SCHEDULE_SCENE_ID,
    resolve_schedule_runtime_activity_targets,
    runtime_activity_entities_for_date,
)
from backend.core.fanxiu.instrumentation.activity_gift import (
    read_activity_gift_runtime_snapshot,
)


RESOURCE_RANK_DAILY_GIFT_TASK_TYPE = "resource_rank_daily_free_gift"
RESOURCE_RANK_DAILY_GIFT_TASK_ID = "resource-rank-daily-free-gift"
RESOURCE_RANK_DAILY_GIFT_LABEL = "资源榜_每日免费礼包"
RESOURCE_RANK_DAILY_GIFT_TRIGGER = (5, 10)


@dataclass(frozen=True)
class ResourceRankGiftAdapter:
    key: str
    label: str
    schedule_pattern: str
    activity_ids: tuple[int, ...]
    page_scene_ids: tuple[int, ...]
    intro_scene_id: int | None = None


@dataclass(frozen=True)
class ResourceRankGiftListAction:
    kind: str
    text: str
    x: float
    y: float


# Each resource-rank family enters the shared #605 gift page only after its own
# real scene and negative-sample proof.  The zero-cost policy and ChargeMgr
# idempotency reader stay shared across adapters.
RESOURCE_RANK_GIFT_ADAPTERS = (
    ResourceRankGiftAdapter(
        key="lingzhuang-huadao",
        label="灵装化道",
        schedule_pattern=r"灵装化道",
        activity_ids=(
            1044501,
            1044301,
            2044301,
            4044301,
            1044311,
            8044301,
            16044301,
            32044301,
        ),
        page_scene_ids=(676,),
        intro_scene_id=675,
    ),
    ResourceRankGiftAdapter(
        key="dandao-wending",
        label="丹道问鼎",
        schedule_pattern=r"丹道问鼎",
        activity_ids=(1043111, 4043101),
        page_scene_ids=(597, 598, 599),
        intro_scene_id=596,
    ),
)


def next_resource_rank_daily_gift_time(
    now: datetime | None = None,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    current = now or datetime.now(ZoneInfo(timezone_name))
    zone = ZoneInfo(timezone_name)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    else:
        current = current.astimezone(zone)
    candidate = current.replace(
        hour=RESOURCE_RANK_DAILY_GIFT_TRIGGER[0],
        minute=RESOURCE_RANK_DAILY_GIFT_TRIGGER[1],
        second=0,
        microsecond=0,
    )
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def active_resource_rank_gift_adapters(
    snapshot: Mapping[str, Any],
    *,
    now: datetime,
    adapters: Iterable[ResourceRankGiftAdapter] = RESOURCE_RANK_GIFT_ADAPTERS,
) -> list[tuple[ResourceRankGiftAdapter, int]]:
    """Resolve only currently open, identity-complete resource-rank instances."""

    zone = now.tzinfo
    current = now if zone is not None else now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    by_activity = {
        int(activity_id): adapter
        for adapter in adapters
        for activity_id in adapter.activity_ids
    }
    selected: list[tuple[ResourceRankGiftAdapter, int]] = []
    seen: set[tuple[str, int]] = set()
    for raw in snapshot.get("occurrences") or []:
        if not isinstance(raw, Mapping) or not raw.get("identity_complete"):
            continue
        activity_id = int(raw.get("activity_id") or 0)
        adapter = by_activity.get(activity_id)
        if adapter is None:
            continue
        start = _parse_time(raw.get("start_at"))
        end = _parse_time(raw.get("end_at"))
        if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
            continue
        if not start <= current.astimezone(start.tzinfo) <= end:
            continue
        key = (adapter.key, activity_id)
        if key not in seen:
            seen.add(key)
            selected.append((adapter, activity_id))
    return selected


def project_resource_rank_gift_list_actions(
    lines: Iterable[Mapping[str, Any]],
    *,
    frame_width: float = 900.0,
) -> tuple[ResourceRankGiftListAction, ...]:
    """Project only the fixed right-hand action column of visible gift rows.

    Free rows form a prefix.  The first numeric spiritual-stone price is the
    hard stop boundary; reward quantities live to its left and are ignored.
    """

    minimum_x = float(frame_width) * 0.62
    actions: list[ResourceRankGiftListAction] = []
    for raw in lines:
        text = re.sub(r"\s+", "", str(raw.get("text") or ""))
        x = float(raw.get("x") or 0.0)
        y = float(raw.get("y") or 0.0)
        width = float(raw.get("w") or 0.0)
        height = float(raw.get("h") or 0.0)
        if x < minimum_x:
            continue
        if text == "免费":
            kind = "free"
        elif re.fullmatch(r"\d+", text):
            kind = "spirit_stone"
        else:
            continue
        actions.append(
            ResourceRankGiftListAction(
                kind=kind,
                text=text,
                x=x + width / 2.0,
                y=y + height / 2.0,
            )
        )
    return tuple(sorted(actions, key=lambda item: (item.y, item.x)))


def validate_one_free_gift_increment(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> int:
    """Return the one zero-cost gift whose Runtime purchase count advanced."""

    before_rows = {
        int(row["id"]): dict(row)
        for row in before.get("items") or []
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    after_rows = {
        int(row["id"]): dict(row)
        for row in after.get("items") or []
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    if before_rows.keys() != after_rows.keys():
        raise RuntimeError("资源榜礼包 Runtime 配置集在点击前后发生变化")
    changed: list[int] = []
    for gift_id, old in before_rows.items():
        delta = int(after_rows[gift_id].get("purchased_times") or 0) - int(
            old.get("purchased_times") or 0
        )
        if delta == 0:
            continue
        if delta != 1 or not bool(old.get("is_free")):
            raise RuntimeError(
                f"资源榜礼包 {gift_id} 出现非免费或非单次购买增量：{delta}"
            )
        changed.append(gift_id)
    if len(changed) != 1:
        raise RuntimeError(
            f"资源榜免费礼包点击后购买次数增量数为 {len(changed)}，拒绝判成功"
        )
    return changed[0]


def _claimable_free_gift_ids(snapshot: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(
        int(row.get("id") or 0)
        for row in snapshot.get("items") or []
        if isinstance(row, Mapping)
        and bool(row.get("claimable"))
        and int(row.get("id") or 0) > 0
    )


def _resource_rank_schedule_entry_date(
    entities: Iterable[Any],
    *,
    activity_id: int,
    now: datetime,
):
    """Return the calendar column that owns the active resource-rank card.

    The schedule renders a multi-day resource-rank occurrence on its formal
    start date.  It does not repeat the card in every open day's column; the
    gameplay-ranking card occupying today's column is an independent line.
    """

    matched_payloads: list[Mapping[str, Any]] = []
    for entity in entities:
        payload = (
            entity.get("payload")
            if isinstance(entity, Mapping)
            else getattr(entity, "payload", None)
        )
        if not isinstance(payload, Mapping):
            continue
        if int(payload.get("activityId") or 0) == int(activity_id):
            matched_payloads.append(payload)
    if len(matched_payloads) != 1:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：活动 {activity_id} 的 Runtime 日程实体数为 "
            f"{len(matched_payloads)}"
        )
    start_millis = int(matched_payloads[0].get("startTime") or 0)
    if start_millis <= 0:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：活动 {activity_id} 缺少 startTime"
        )
    zone = now.tzinfo or ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.fromtimestamp(start_millis / 1000.0, tz=zone).date()


def _resolve_resource_rank_schedule_target(
    entities: Iterable[Any],
    *,
    activity_id: int,
    now: datetime,
    header_lines: Iterable[Mapping[str, Any]],
    calendar_lines: Iterable[Mapping[str, Any]],
):
    """Align one exact occurrence to a calendar row and start-date column.

    Multi-day calendar bars can span several date columns, while their OCR
    title is rendered near the middle of the bar.  The title's X coordinate is
    therefore not evidence that the occurrence belongs to that date.  Runtime
    identity/qualifier selects the row; the occurrence start date independently
    selects the click column.
    """

    exact_entities = tuple(
        entity
        for entity in entities
        if int(
            (
                entity.get("payload")
                if isinstance(entity, Mapping)
                else getattr(entity, "payload", {})
            ).get("activityId")
            or 0
        )
        == int(activity_id)
    )
    entry_date = _resource_rank_schedule_entry_date(
        exact_entities,
        activity_id=activity_id,
        now=now,
    )
    day_offset = (entry_date - now.date()).days
    targets = resolve_schedule_runtime_activity_targets(
        header_lines=header_lines,
        calendar_lines=calendar_lines,
        runtime_entities=exact_entities,
        day_offset=day_offset,
        anchor_date=now.date(),
    )
    if len(targets) != 1:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：活动 {activity_id} 在 #66 "
            f"对齐到 {len(targets)} 个日历任务行，拒绝猜测"
        )
    return targets[0], entry_date


def _enter_adapter_from_schedule(
    runtime: Any,
    adapter: ResourceRankGiftAdapter,
    *,
    activity_id: int,
    now: datetime,
):
    """Enter the active occurrence's exact #66 start-date calendar cell."""

    entities = runtime_activity_entities_for_date(
        get_cached_fanxiu_activity_runtime_schedule(),
        adapter.schedule_pattern,
        target_date=now.date(),
    )
    if not entities:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：缺少当天 {adapter.label} Runtime 日程实体"
        )
    target = None
    entry_date = None
    last_alignment_error: Exception | None = None
    # #66 first becomes recognizable before its calendar cards necessarily
    # finish rendering.  Refresh a bounded number of frames so that this
    # transient state cannot turn an open activity into a false negative.
    for attempt in range(3):
        runtime.runner._raise_if_stopped(runtime.stop_event)
        frame = runtime.cur_frame(update=True)
        scene, score, _ = runtime.current_scene([SCHEDULE_SCENE_ID], update=False)
        if scene != SCHEDULE_SCENE_ID or score < 90:
            raise RuntimeError(
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：#66 身份无效 {scene}/{score:.0f}"
            )
        header_lines = runtime.ocr_fragments_in_shapes(
            SCHEDULE_SCENE_ID, [HEADER_SHAPE], frame_data_url=frame
        )
        calendar_lines = runtime.ocr_fragments_in_shapes(
            SCHEDULE_SCENE_ID, [CALENDAR_SHAPE], frame_data_url=frame
        )
        try:
            target, entry_date = _resolve_resource_rank_schedule_target(
                entities,
                activity_id=activity_id,
                now=now,
                header_lines=header_lines,
                calendar_lines=calendar_lines,
            )
            break
        except RuntimeError as exc:
            last_alignment_error = exc
        if attempt == 2:
            break
        yield from runtime.wait_action_settle(1.0)
    if target is None or entry_date is None:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：Runtime 确认活动 {activity_id} 正在开放，"
            f"但 #66 连续三帧未把{adapter.label}对齐到唯一任务行；"
            f"{last_alignment_error or '无可用 OCR 候选'}"
        )
    runtime.runner._raise_if_stopped(runtime.stop_event)
    runtime.runner._click_frame_point(
        runtime.ctx,
        runtime.view(SCHEDULE_SCENE_ID).raw,
        target.x,
        target.y,
    )
    runtime.clear_frame()
    targets = [*adapter.page_scene_ids]
    if adapter.intro_scene_id is not None:
        targets.insert(0, adapter.intro_scene_id)
    return (yield from runtime.wait_scene(
        *targets,
        timeout=30.0,
        label=f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：等待{adapter.label}页面",
    ))


def open_resource_rank_activity_page(
    runtime: Any,
    adapter: ResourceRankGiftAdapter,
    *,
    activity_id: int,
    now: datetime,
):
    """Open one active resource-ranking occurrence and return its main scene."""

    scene, _score, _frame = runtime.current_scene(
        [34, 66, *(adapter.page_scene_ids), *(
            (adapter.intro_scene_id,) if adapter.intro_scene_id is not None else ()
        )],
        update=True,
    )
    if scene not in adapter.page_scene_ids:
        if scene != 66:
            result = runtime.go_scene(66)
            if hasattr(result, "send"):
                yield from result
        waited = yield from _enter_adapter_from_schedule(
            runtime,
            adapter,
            activity_id=activity_id,
            now=now,
        )
        scene = int(getattr(waited, "id", waited))
    if adapter.intro_scene_id is not None and scene == adapter.intro_scene_id:
        runtime.click_shape_center(adapter.intro_scene_id, "查看详情")
        waited = yield from runtime.wait_scene(
            *adapter.page_scene_ids,
            timeout=20.0,
            label=f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：等待{adapter.label}榜单",
        )
        scene = int(getattr(waited, "id", waited))
    if scene not in adapter.page_scene_ids:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：{adapter.label}页面身份无效：{scene}"
        )
    return int(scene)


def _open_adapter_gift_page(
    runtime: Any,
    adapter: ResourceRankGiftAdapter,
    *,
    activity_id: int,
    now: datetime,
):
    scene, _score, _frame = runtime.current_scene([605], update=True)
    if scene == 605:
        return
    scene = yield from open_resource_rank_activity_page(
        runtime,
        adapter,
        activity_id=activity_id,
        now=now,
    )
    runtime.click_shape_center(int(scene), "礼包")
    # #605 is the first real shared ActivityRankGiftView asset.  Additional
    # activities reuse this scene only after their own positive/negative replay.
    yield from runtime.wait_view(
        605,
        timeout=20.0,
        label=f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：等待{adapter.label}礼包页",
    )
    return True


def run_resource_rank_daily_gift_flow(
    runtime: Any,
    *,
    now: datetime | None = None,
    manage_schedule: bool = False,
    expected_activity_type: str | None = None,
    expected_activity_id: int | None = None,
) -> dict[str, Any]:
    """Claim every currently claimable zero-cost gift with Runtime readback."""

    current = now or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    snapshot = load_worldline_activity_schedule_snapshot()
    active = active_resource_rank_gift_adapters(snapshot, now=current)
    occurrence_scoped = (
        expected_activity_type is not None or expected_activity_id is not None
    )
    if occurrence_scoped:
        active = [
            (adapter, activity_id)
            for adapter, activity_id in active
            if (
                expected_activity_type is None
                or adapter.key == expected_activity_type
            )
            and (
                expected_activity_id is None
                or int(activity_id) == int(expected_activity_id)
            )
        ]
        if len(active) != 1:
            raise RuntimeError(
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：父 checkpoint 要求精确 occurrence "
                f"type={expected_activity_type!r}, id={expected_activity_id!r}，"
                f"实际匹配 {len(active)} 个，拒绝假完成"
            )
    next_time = next_resource_rank_daily_gift_time(current).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    if not active:
        if manage_schedule:
            runtime.set_next_time(next_time)
        return {
            "result": "success",
            "current_scene": 34,
            "claimed_count": 0,
            "message": (
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：当前没有已验收且正在开放的资源榜；"
                f"下次 {next_time}"
            ),
        }
    if len(active) != 1:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：同时开放 {len(active)} 个已验收资源榜，"
            "尚未证明串行页面归属，拒绝点击"
        )
    adapter, activity_id = active[0]
    runtime_snapshot = read_activity_gift_runtime_snapshot([activity_id])
    if not runtime_snapshot.get("ok") or not runtime_snapshot.get("complete"):
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：礼包 Runtime 不完整："
            f"{runtime_snapshot.get('reason') or 'unknown'}"
        )
    if bool(runtime_snapshot.get("active_filter_applied")) and not _claimable_free_gift_ids(runtime_snapshot):
        # ChargeMgr is readable before entering the activity page.  Let this
        # authoritative idempotency fact short-circuit all navigation and
        # visual interaction; the calendar entry can already be absent after
        # the activity was completed earlier today.
        if manage_schedule:
            runtime.set_next_time(next_time)
        return {
            "result": "success",
            "current_scene": None,
            "claimed_count": 0,
            "claimed_ids": [],
            "boundary": "runtime_all_free_claimed",
            "message": (
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：Runtime 确认免费礼包已全部领取；"
                f"下次 {next_time}"
            ),
        }

    yield from _open_adapter_gift_page(
        runtime,
        adapter,
        activity_id=activity_id,
        now=current,
    )

    claimed_count = 0
    claimed_ids: list[int] = []
    scroll_steps = 0
    boundary = ""
    while claimed_count < 20:
        remaining_free_ids = _claimable_free_gift_ids(runtime_snapshot)
        if not remaining_free_ids:
            # The read-only ChargeMgr counters are the authoritative
            # idempotency fact.  A visually stale "免费" row must never be
            # clicked again after every free configuration is exhausted.
            boundary = "runtime_all_free_claimed"
            break
        frame = runtime.cur_frame(update=True)
        lines = runtime.ocr_fragments_in_shapes(
            605, ["礼包列表窗口"], frame_data_url=frame
        )
        actions = project_resource_rank_gift_list_actions(lines)
        if actions:
            first = actions[0]
            if first.kind == "spirit_stone":
                boundary = f"spirit_stone:{first.text}"
                break
            before = runtime_snapshot
            runtime.runner._raise_if_stopped(runtime.stop_event)
            runtime.runner._click_frame_point(
                runtime.ctx,
                runtime.view(605).raw,
                first.x,
                first.y,
            )
            runtime.clear_frame()
            yield from runtime.wait_action_settle(1.0)
            landed = yield from runtime.wait_scene(
                605,
                578,
                timeout=10.0,
                label=f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：等待领取反馈",
            )
            if int(getattr(landed, "id", landed)) == 578:
                yield from runtime.wait_scene(
                    605,
                    timeout=12.0,
                    label=f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：等待奖励提示消失",
                )
            after = read_activity_gift_runtime_snapshot([activity_id])
            if not after.get("ok") or not after.get("complete"):
                raise RuntimeError(
                    f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：领取后 Runtime 不完整："
                    f"{after.get('reason') or 'unknown'}"
                )
            claimed_ids.append(validate_one_free_gift_increment(before, after))
            claimed_count += 1
            runtime_snapshot = after
            continue
        if any("适度娱乐" in str(line.get("text") or "") for line in lines):
            boundary = "list_footer"
            break
        changed = yield from runtime.scroll_shape_content(
            605,
            "礼包列表窗口",
            direction="down",
        )
        scroll_steps += 1
        if not changed:
            raise RuntimeError(
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：未找到灵石边界且列表无法继续滚动"
            )
        if scroll_steps >= 20:
            raise RuntimeError(
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：20 次滚动内未找到免费前缀终点"
            )
    if not boundary:
        raise RuntimeError(
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：免费领取达到安全上限但未见付费边界"
        )

    if manage_schedule:
        runtime.set_next_time(next_time)
    try:
        result = runtime.go_scene(34)
        if hasattr(result, "send"):
            yield from result
    except (InterruptedError, GeneratorExit):
        raise
    except Exception as exc:
        # The irreversible claim has already been read back and next_time was
        # persisted.  A departure failure must not cause a duplicate purchase.
        return {
            "result": "success",
            "current_scene": 605,
            "claimed_count": claimed_count,
            "claimed_ids": claimed_ids,
            "boundary": boundary,
            "message": (
                f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：{adapter.label}免费礼包已幂等收敛；"
                f"离场告警 {type(exc).__name__}: {exc}；下次 {next_time}"
            ),
        }
    return {
        "result": "success",
        "current_scene": 34,
        "claimed_count": claimed_count,
        "claimed_ids": claimed_ids,
        "boundary": boundary,
        "message": (
            f"{RESOURCE_RANK_DAILY_GIFT_LABEL}：{adapter.label}"
            f"本次领取 {claimed_count} 项，Runtime 已确认无可领免费礼包；"
            f"下次 {next_time}"
        ),
    }


class ResourceRankDailyGiftTaskMixin:
    def _execute_resource_rank_daily_free_gift_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type=RESOURCE_RANK_DAILY_GIFT_TASK_TYPE,
            label=RESOURCE_RANK_DAILY_GIFT_LABEL,
            flow=run_resource_rank_daily_gift_flow,
        )


__all__ = [
    "RESOURCE_RANK_DAILY_GIFT_LABEL",
    "RESOURCE_RANK_DAILY_GIFT_TASK_ID",
    "RESOURCE_RANK_DAILY_GIFT_TASK_TYPE",
    "RESOURCE_RANK_GIFT_ADAPTERS",
    "ResourceRankDailyGiftTaskMixin",
    "ResourceRankGiftAdapter",
    "active_resource_rank_gift_adapters",
    "next_resource_rank_daily_gift_time",
    "open_resource_rank_activity_page",
    "run_resource_rank_daily_gift_flow",
]
