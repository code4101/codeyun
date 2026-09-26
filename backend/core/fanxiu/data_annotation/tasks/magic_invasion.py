from __future__ import annotations

"""Production Magic Invasion exploration shared by server and cross-server.

The workflow is intentionally occurrence-scoped.  A server-internal preview
and the following cross-server round reuse the same actions, but each exact
Runtime occurrence independently owes three confirmed 500-base-explore
batches, plus one compensation batch only when those three produce no
currency. Mail is outside the critical path: rank mail is handled by the
idempotent mail job and its absence never blocks exploration.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import re
import threading
import time
from typing import Any, Iterator, Mapping, Sequence

from backend.core.fanxiu.activity.magic_invasion_explore import (
    MAGIC_INVASION_EXPLORE_BATCH_SIZE,
    MAGIC_INVASION_MAX_BATCHES,
    MAGIC_INVASION_TARGET_BATCHES,
    TIANYAN_ITEM_ID,
    WHITE_DRAGON_EFFECT_ALIASES,
    actual_magic_invasion_topup,
    plan_magic_invasion_reward_batches,
)
from backend.core.fanxiu.activity.magic_invasion import (
    resolve_magic_invasion_shop_identity,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerSliderAssets,
    set_verified_integer_slider_count as _set_verified_slider_count,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.magic_invasion_task_rewards import (
    read_magic_invasion_task_reward_snapshot,
)
from backend.core.fanxiu.instrumentation.item_batch_use_dialog import (
    read_item_batch_use_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot


MAGIC_INVASION_PROGRESS_KEY = "magic_invasion_progress"
MAGIC_INVASION_ACTIVITY_TYPE_ID = 7
MAGIC_INVASION_MAIN_SCENE_ID = 509
MAGIC_INVASION_TASK_DEMON_SCENE_ID = 510
MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID = 511
MAGIC_INVASION_MAP_SCENE_ID = 512
MAGIC_INVASION_ITEM_SCENE_ID = 513
MAGIC_INVASION_USE_SCENE_ID = 514
MAGIC_INVASION_RESULT_SCENE_ID = 515
MAGIC_INVASION_EVENT_SCENE_ID = 516
MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID = 517
MAGIC_INVASION_OVERFLOW_SCENE_ID = 518
MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID = 641
MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID = 697
MAGIC_INVASION_WORLD_MAP_SCENE_ID = 425
# 沙盘切换会让客户端回到角色入口页。它属于登录过渡，不是开放世界：必须
# 点「进入」回到游戏，不能当成 #34 去走 #425 的历练路线。
MAGIC_INVASION_ROLE_ENTRY_SCENE_ID = 661
MAGIC_INVASION_TIANNAN_COMPLETE_SCENE_TITLE = "魔道入侵·天南大陆完成"
MAGIC_INVASION_ENTRY_SETTLE_TIMEOUT_SECONDS = 90.0
MAGIC_INVASION_ENTRY_MAX_OPTIONAL_STEPS = 128


def wait_magic_invasion_cover_after_schedule_entry(
    context: Any,
    target_scene_ids: Sequence[int],
    *,
    settle_seconds: float = 3.0,
    wait_seconds: float = 30.0,
    label: str = "魔道入侵：等待活动页",
) -> Iterator[Any]:
    """Wait immediately after #66 enters Magic, consuming only cover-entry noise.

    This helper defines a deliberately narrow business Layer 0.  It must not be
    reused by exploration, rewards, shops, or any other already-entered Magic
    workflow.
    """

    targets = tuple(
        dict.fromkeys(
            int(scene_id)
            for scene_id in target_scene_ids
            if int(scene_id) != MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID
        )
    )
    if not targets:
        raise ValueError("魔道入侵入口缺少目标场景")

    yield from context.wait_action_settle(max(0.0, float(settle_seconds)))
    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            expected = "/".join(f"#{scene_id}" for scene_id in targets)
            raise TimeoutError(f"{label}：处理入口噪声后仍未到达 {expected}")
        match = yield from context.wait_scene(
            [*targets, MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID],
            wait=min(remaining, 30.0),
            label=label,
        )
        scene_id = int(getattr(match, "scene_id", match))
        if scene_id in targets:
            return match
        if scene_id == MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID:
            context.click_shape_center(MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID, "返回")
            yield from context.wait_action_settle(1.0)
        else:
            # wait_scene may report a recognized fallback outside its request.
            # In particular #66 still showing a preview is not an activity
            # landing and must never be passed to a bottom-tab action.
            yield from context.wait_action_settle(1.0)


@dataclass(frozen=True)
class MagicInvasionOccurrence:
    occurrence_id: str
    activity_id: int
    runtime_id: int
    start_time_ms: int
    end_time_ms: int
    server_count: int
    mode: str


def _ms(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def magic_invasion_occurrences(
    schedule: Mapping[str, Any],
) -> tuple[MagicInvasionOccurrence, ...]:
    rows: list[MagicInvasionOccurrence] = []
    for raw in schedule.get("items") or ():
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if int(item.get("activityType") or 0) != MAGIC_INVASION_ACTIVITY_TYPE_ID:
            continue
        activity_id = int(item.get("activityId") or 0)
        runtime_id = int(item.get("id") or 0)
        start_ms = _ms(item.get("startTime"))
        end_ms = _ms(item.get("endTime"))
        if activity_id <= 0 or runtime_id <= 0 or start_ms <= 0 or end_ms < start_ms:
            continue
        server_count = max(1, int(item.get("serverCount") or 1))
        rows.append(
            MagicInvasionOccurrence(
                occurrence_id=str(runtime_id),
                activity_id=activity_id,
                runtime_id=runtime_id,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                server_count=server_count,
                mode="server" if server_count <= 1 else "cross",
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.start_time_ms, row.runtime_id)))


def current_magic_invasion_occurrence(
    schedule: Mapping[str, Any],
    *,
    now: datetime,
) -> MagicInvasionOccurrence | None:
    now_ms = int(now.timestamp() * 1000)
    current = [
        item
        for item in magic_invasion_occurrences(schedule)
        if item.start_time_ms <= now_ms <= item.end_time_ms
    ]
    if len(current) > 1:
        raise RuntimeError("同时命中多个魔道入侵 Runtime 实例，拒绝猜测")
    return current[0] if current else None


def next_magic_invasion_probe_time(now: datetime) -> datetime:
    """Daily fail-safe probe; active-day Runtime identity gates every click."""

    candidate = now.replace(hour=10, minute=1, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _read_available_explore_count() -> int:
    """Read the manager count; the map item icon is not an OCR digit."""
    from backend.core.fanxiu.instrumentation.magic_invasion_auto_running import read_magic_invasion_counters

    return int(read_magic_invasion_counters()["explore_count"])


def parse_available_explore_count(text: str) -> int:
    """Parse the Tianyan-backed available-explore counter, never event progress."""

    value = str(text or "")
    if "挑战事件" in value:
        raise RuntimeError(f"魔道入侵误把挑战事件进度当成可用探查次数：{text!r}")
    match = re.search(r"(\d+)\s*/\s*120(?!\d)", value)
    if match is None:
        raise RuntimeError(f"魔道入侵可用探查次数不可读：{text!r}")
    return int(match.group(1))


def parse_owned_item_count(text: str) -> int:
    match = re.search(r"持有数量\s*[:：]?\s*(\d+)", str(text or ""))
    if match is None:
        raise RuntimeError(f"天眼符持有数量不可读：{text!r}")
    return int(match.group(1))


def parse_selected_item_count(text: str) -> int:
    values = [int(value) for value in re.findall(r"(?<!\d)(\d+)(?!\d)", str(text or ""))]
    if len(values) != 1:
        raise RuntimeError(f"天眼符使用数量不唯一：{text!r}")
    return values[0]


def parse_magic_invasion_result_explore_count(
    text: str,
    *,
    batch_index: int,
) -> int:
    """Read the bonus-inclusive result while validating one 500-base batch."""

    match = re.search(r"(?:快速)?探索\s*(\d+)\s*次", str(text or ""))
    if match is None:
        raise RuntimeError(f"魔道入侵结果没有探索次数证据：{text!r}")
    result_explore_count = int(match.group(1))
    if result_explore_count < MAGIC_INVASION_EXPLORE_BATCH_SIZE:
        raise RuntimeError(
            f"魔道入侵第 {batch_index} 批结果少于 500 次：{result_explore_count}"
        )
    return result_explore_count


def _wait_scene(
    context: Any,
    targets: tuple[int, ...],
    *,
    timeout_seconds: float = 20.0,
) -> Iterator[Any]:
    """Wait through the shared scene pipeline so popup interruptions stay active."""

    match = yield from context.wait_scene(
        list(targets),
        wait=max(1.0, float(timeout_seconds)),
        label="魔道入侵：等待场景",
    )
    scene_id = getattr(match, "scene_id", match)
    if scene_id is None:
        raise RuntimeError(f"魔道入侵没有识别到目标场景：targets={targets}")
    if int(scene_id) not in targets:
        raise RuntimeError(
            f"等待魔道入侵场景时落到意外场景：targets={targets}, scene={scene_id}"
        )
    return (
        int(scene_id),
        float(getattr(match, "score", 0.0) or 0.0),
        str(getattr(match, "frame_data_url", "") or ""),
    )


def _shape_text(context: Any, scene_id: int, shape_title: str) -> str:
    frame = context.cur_frame(update=True)
    fragments = context.ocr_fragments_in_shapes(
        scene_id,
        (shape_title,),
        frame_data_url=frame,
        padding=8,
        crop=True,
    )
    return " ".join(str(item.get("text") or "") for item in fragments).strip()


def read_magic_invasion_result_count(context: Any, *, batch_index: int) -> Iterator[Any]:
    """Wait for the result line to finish rendering without replaying exploration.

    The title can match before the count line is readable. Each retry observes
    a fresh frame in the existing result Shape; no click occurs until proven.
    """
    for attempt in range(5):
        text = _shape_text(context, MAGIC_INVASION_RESULT_SCENE_ID, "探索次数结果")
        try:
            return parse_magic_invasion_result_explore_count(text, batch_index=batch_index)
        except RuntimeError:
            if attempt == 4:
                raise
            yield from context.wait_action_settle(1.0)


def _optional_scene_id(context: Any, title: str) -> int | None:
    """Resolve an optional business Layer-0 asset without guessing its number."""

    resolver = getattr(context, "resolve_view_selector", None)
    if not callable(resolver):
        return None
    view = resolver(title)
    scene_id = getattr(view, "id", None)
    return int(scene_id) if scene_id is not None else None


def _magic_entry_scene_ids(context: Any) -> tuple[int, ...]:
    optional_transition = _optional_scene_id(
        context,
        MAGIC_INVASION_TIANNAN_COMPLETE_SCENE_TITLE,
    )
    return tuple(dict.fromkeys([
        MAGIC_INVASION_MAIN_SCENE_ID,
        MAGIC_INVASION_MAP_SCENE_ID,
        MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID,
        MAGIC_INVASION_TASK_DEMON_SCENE_ID,
        MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
        MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID,
        MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID,
        MAGIC_INVASION_WORLD_MAP_SCENE_ID,
        34, 661,
        *([optional_transition] if optional_transition is not None else []),
    ]))


def _wait_magic_entry_scene(context: Any, *, wait_seconds: float = 30.0) -> Iterator[Any]:
    match = yield from context.wait_scene(
        list(_magic_entry_scene_ids(context)),
        wait=float(wait_seconds),
        label="魔道入侵：等待入口落点",
    )
    scene_id = getattr(match, "scene_id", match)
    if scene_id is None:
        raise RuntimeError("魔道入侵入口没有识别到稳定场景")
    return int(scene_id)


def _enter_magic_invasion_map(context: Any) -> Iterator[Any]:
    """Move from the Magic cover #509 to challenge cover #512.

    Its dynamic Layer 0 exists only for this transition.  In particular, #697
    is local cover-entry noise and is never exposed to the rest of Magic.
    """

    yield from context.wait_action_settle(3.0)
    optional_transition = _optional_scene_id(
        context,
        MAGIC_INVASION_TIANNAN_COMPLETE_SCENE_TITLE,
    )
    started_at = time.monotonic()
    visited: list[int] = []
    clicked_scene: int | None = None
    for _step in range(MAGIC_INVASION_ENTRY_MAX_OPTIONAL_STEPS):
        elapsed = time.monotonic() - started_at
        remaining = MAGIC_INVASION_ENTRY_SETTLE_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            break
        scene = yield from _wait_magic_entry_scene(
            context,
            wait_seconds=min(30.0, remaining),
        )
        visited.append(scene)
        if scene == clicked_scene:
            # The source can remain visible while its click is in flight.
            # Reobserve; repeating the input can close the destination page.
            yield from context.wait_action_settle(1.0)
            continue
        clicked_scene = None
        if scene == MAGIC_INVASION_MAP_SCENE_ID:
            return
        if scene == MAGIC_INVASION_ROLE_ENTRY_SCENE_ID:
            context.click_shape_center(scene, "进入")
            clicked_scene = scene
            yield from context.wait_action_settle(2.0)
            continue
        if scene == 34:
            # “前往大地图” may first close the cover onto the real world.
            # The annotated map route owns the next click and its landing.
            yield from context.go_scene(MAGIC_INVASION_WORLD_MAP_SCENE_ID)
            continue
        if scene == MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID:
            context.click_shape_center(
                MAGIC_INVASION_COVER_ENTRY_NOISE_SCENE_ID,
                "返回",
            )
            yield from context.wait_action_settle(1.0)
            continue
        if scene == MAGIC_INVASION_MAIN_SCENE_ID:
            context.click_shape(MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图")
            clicked_scene = scene
            yield from context.wait_action_settle(0.5)
            continue
        if scene in {
            MAGIC_INVASION_TASK_DEMON_SCENE_ID,
            MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
        }:
            raise RuntimeError(f"魔道入侵入口误落任务页 #{scene}，拒绝继续进入大地图")
        if scene == MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID:
            context.click_shape(MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认")
            yield from context.wait_action_settle(0.5)
            continue
        if scene == MAGIC_INVASION_WORLD_MAP_SCENE_ID:
            try:
                context.click_shape_center(MAGIC_INVASION_WORLD_MAP_SCENE_ID, "天南大陆")
            except RuntimeError as exc:
                raise RuntimeError(
                    "魔道入侵入口命中 #425，但资产缺少「天南大陆」动作 Shape"
                ) from exc
            yield from context.wait_action_settle(0.5)
            continue
        if optional_transition is not None and scene == optional_transition:
            context.click_shape_center(optional_transition, "背景")
            yield from context.wait_action_settle(0.5)
            continue
        if scene == MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID:
            # 情报过渡页会自行消失；只等待新事实，不在其上点击或发送返回键。
            yield from context.wait_action_settle(0.75)
            continue
        raise RuntimeError(f"魔道入侵入口出现未处理场景 #{scene}")
    sequence = " -> ".join(f"#{scene_id}" for scene_id in visited)
    raise RuntimeError(
        "魔道入侵入口处理可选页面超时，仍未稳定落到 #512："
        f"{sequence or '无已识别场景'}"
    )


def enter_magic_invasion_challenge_page(context: Any) -> Iterator[Any]:
    """Enter the Magic challenge map (#512) from its stable cover (#509)."""

    yield from _enter_magic_invasion_map(context)
    return MAGIC_INVASION_MAP_SCENE_ID


def _leave_magic_invasion_map(context: Any) -> Iterator[Any]:
    """Leave the sandbox without confusing the map-entry confirmation for an exit."""

    context.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "地图返回")
    scene, _score, _frame = yield from _wait_scene(
        context,
        (34, MAGIC_INVASION_MAIN_SCENE_ID),
        timeout_seconds=15.0,
    )
    if scene == MAGIC_INVASION_MAIN_SCENE_ID:
        context.click_shape_center(MAGIC_INVASION_MAIN_SCENE_ID, "返回")
        yield from _wait_scene(context, (34,), timeout_seconds=15.0)


def _magic_occurrence_checkpoint(
    occurrence: MagicInvasionOccurrence,
) -> Any:
    from backend.core.fanxiu.activity.ranking_lifecycle import (
        MAGIC_ACTIVE_KIND,
        RankingCheckpoint,
    )

    start_at = datetime.fromtimestamp(occurrence.start_time_ms / 1000).astimezone()
    end_at = datetime.fromtimestamp(occurrence.end_time_ms / 1000).astimezone()
    business_date = datetime.now().astimezone(start_at.tzinfo).date()
    from backend.core.fanxiu.activity.magic_occurrence_identity import magic_occurrence_key
    return RankingCheckpoint(
        instance_key=magic_occurrence_key(occurrence.activity_id, occurrence.server_count,
                                          start_at.isoformat(), end_at.isoformat()),
        activity_type="magic-invasion",
        family="gameplay_rank",
        runtime_id=occurrence.occurrence_id,
        activity_id=occurrence.activity_id,
        checkpoint_kind=MAGIC_ACTIVE_KIND,
        business_date=business_date.isoformat(),
        due_at=datetime.combine(
            business_date,
            datetime.strptime("19:00", "%H:%M").time(),
            tzinfo=start_at.tzinfo,
        ),
    )


def _set_progress(
    occurrence: MagicInvasionOccurrence,
    progress: Mapping[str, Any],
) -> None:
    """Persist irreversible evidence on the occurrence checkpoint, never a Job."""

    store_magic_invasion_occurrence_evidence(
        occurrence,
        {MAGIC_INVASION_PROGRESS_KEY: dict(progress)},
        message=f"魔道入侵不可逆对账证据：{progress.get('state') or 'unknown'}",
    )


def load_magic_invasion_occurrence_evidence(
    occurrence: MagicInvasionOccurrence,
) -> dict[str, Any]:
    """Read the complete durable evidence map for one exact occurrence."""

    from sqlmodel import Session

    from backend.core.fanxiu.activity.ranking_lifecycle_store import (
        ensure_ranking_lifecycle_checkpoint_table,
        ranking_checkpoint_evidence,
    )
    from backend.db import engine

    checkpoint = _magic_occurrence_checkpoint(occurrence)
    ensure_ranking_lifecycle_checkpoint_table(engine)
    with Session(engine) as session:
        return ranking_checkpoint_evidence(session, checkpoint)


def store_magic_invasion_occurrence_evidence(
    occurrence: MagicInvasionOccurrence,
    updates: Mapping[str, Any],
    *,
    message: str = "",
) -> dict[str, Any]:
    """Merge namespaced evidence without deleting sibling workflow state."""

    from sqlmodel import Session

    from backend.core.fanxiu.activity.ranking_lifecycle_store import (
        ensure_ranking_lifecycle_checkpoint_table,
        ranking_checkpoint_evidence,
        record_ranking_checkpoint_evidence,
    )
    from backend.db import engine

    if not isinstance(updates, Mapping) or not updates:
        raise ValueError("魔道入侵 occurrence evidence 更新为空")
    checkpoint = _magic_occurrence_checkpoint(occurrence)
    ensure_ranking_lifecycle_checkpoint_table(engine)
    with Session(engine) as session:
        evidence = ranking_checkpoint_evidence(session, checkpoint)
        evidence.update(dict(updates))
        row = record_ranking_checkpoint_evidence(
            session,
            checkpoint,
            evidence=evidence,
            message=message,
        )
        return dict(row.evidence or {})


def load_magic_invasion_occurrence_progress(
    occurrence: MagicInvasionOccurrence,
) -> dict[str, Any]:
    evidence = load_magic_invasion_occurrence_evidence(occurrence)
    existing = evidence.get(MAGIC_INVASION_PROGRESS_KEY)
    existing = dict(existing) if isinstance(existing, Mapping) else {}
    old_id = str(existing.get("occurrence_id") or "")
    old_state = str(existing.get("state") or "")
    if old_id and old_id != occurrence.occurrence_id and old_state not in {"", "complete"}:
        raise RuntimeError(
            "上一魔道入侵实例存在未闭合不可逆批次，拒绝用新实例覆盖防重复证据"
        )
    if old_id == occurrence.occurrence_id and old_state == "complete":
        return dict(existing)
    if old_id == occurrence.occurrence_id and old_state == "ready":
        # The currency baseline is saved before navigation. A navigation
        # failure at this boundary has not armed any consumptive action.
        if (existing.get("confirmed_batches") or existing.get("transaction_evidence")
                or int(existing.get("base_explore_count") or 0)
                or int(existing.get("batch_index") or 0)):
            raise RuntimeError("魔道 ready 状态混入批次执行证据，拒绝重跑")
        return dict(existing)
    resumable_states = {
        "confirmed",
        "use_armed",
        "topup_confirmed",
        "explore_armed",
        "result_observed",
    }
    if old_id == occurrence.occurrence_id and old_state in resumable_states:
        confirmed = list(existing.get("confirmed_batches") or [])
        expected_indexes = list(range(1, len(confirmed) + 1))
        actual_indexes = [int(item.get("batch_index") or 0) for item in confirmed]
        expected_base_count = len(confirmed) * MAGIC_INVASION_EXPLORE_BATCH_SIZE
        if (
            len(confirmed) > MAGIC_INVASION_MAX_BATCHES
            or (
                len(confirmed) == MAGIC_INVASION_MAX_BATCHES
                and old_state != "confirmed"
            )
            or actual_indexes != expected_indexes
            or int(existing.get("base_explore_count") or 0) != expected_base_count
            or any(
                int(item.get("base_explore_after") or 0)
                != index * MAGIC_INVASION_EXPLORE_BATCH_SIZE
                or int(
                    item.get("available_explore_count_after_result")
                    if item.get("available_explore_count_after_result") is not None
                    else -1
                )
                < 0
                for index, item in enumerate(confirmed, start=1)
            )
        ):
            raise RuntimeError("魔道入侵已确认批次证据不连续，拒绝恢复")
        if old_state == "confirmed":
            if not confirmed:
                raise RuntimeError("魔道入侵 confirmed 状态缺少已确认批次")
            return dict(existing)

        transaction_evidence = existing.get("transaction_evidence")
        expected_batch_index = len(confirmed) + 1
        if (
            not isinstance(transaction_evidence, Mapping)
            or int(existing.get("batch_index") or 0) != expected_batch_index
            or int(
                transaction_evidence.get("base_explore_before")
                if transaction_evidence.get("base_explore_before") is not None
                else -1
            )
            != expected_base_count
        ):
            raise RuntimeError("魔道入侵在途批次证据与已确认前缀不连续，拒绝恢复")
        return dict(existing)
    if old_id == occurrence.occurrence_id and old_state:
        raise RuntimeError(
            "魔道入侵上一 attempt 留有未闭合不可逆证据；禁止从 GUI 中间步骤恢复，需人工对账"
        )
    return {
        "occurrence_id": occurrence.occurrence_id,
        "activity_id": occurrence.activity_id,
        "mode": occurrence.mode,
        "server_count": occurrence.server_count,
        "state": "ready",
        "confirmed_batches": [],
        "base_explore_count": 0,
        "mail_policy": "optional_not_prerequisite",
    }


def _configure_use_quantity(context: Any, *, quantity: int) -> Iterator[Any]:
    """Load missing range facts once; adjust and verify through local OCR.

    The caller has established scene #514 and the Tianyan entry. Range/cap
    assets are not yet available, so one Runtime snapshot supplies these
    facts. Normal feedback never rereads the full dialog. On controller
    failure, a single read-only diagnostic preserves the original failure;
    it does not authorize retrying a drag or committing item use.
    Live OCR accuracy and latency still require acceptance on the real dialog.
    """
    runtime_before = read_item_batch_use_dialog_snapshot(
        expected_item_id=TIANYAN_ITEM_ID
    )
    owned = int(runtime_before["owned_count"])
    single_use_maximum = int(runtime_before["single_use_maximum"])
    slider_maximum = int(runtime_before["slider_maximum"])
    if quantity > owned:
        raise RuntimeError(f"天眼符不足：需要 {quantity}，持有 {owned}")
    if quantity > single_use_maximum:
        raise RuntimeError(
            f"天眼符单次使用上限不足：需要 {quantity}，上限 {single_use_maximum}"
        )
    assets = IntegerSliderAssets(
        settings_scene_id=MAGIC_INVASION_USE_SCENE_ID,
        count_region="使用数量",
        count_decrease="数量减",
        count_increase="数量加",
        count_slider_thumb="数量滑块游标",
        count_minimum_marker="使用数量为1",
        count_slider_left_anchor="数量滑轨左端",
        count_slider_right_anchor="数量滑轨右端",
    )
    try:
        calibration = yield from _set_verified_slider_count(
            context,
            assets,
            int(quantity),
            max_adjustments=10,
            maximum=slider_maximum,
            count_label="天眼符使用数量",
        )
    except RuntimeError as exc:
        try:
            diagnostic = read_item_batch_use_dialog_snapshot(
                expected_item_id=TIANYAN_ITEM_ID
            )
        except RuntimeError as diagnostic_error:
            exc.add_note(f"数量异常的 Runtime 诊断失败：{diagnostic_error}")
        else:
            exc.add_note(
                f"数量异常的 Runtime 诊断：target={quantity}, "
                f"current={diagnostic.get('current')}, "
                f"slider_maximum={diagnostic.get('slider_maximum')}"
            )
        raise
    calibration_evidence = dict(calibration or {})
    try:
        verified = int(calibration_evidence["after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("天眼符使用数量缺少稳定读回证据") from exc
    if verified != int(quantity):
        raise RuntimeError(f"天眼符使用数量验证失败：目标 {quantity}，实际 {verified}")
    return {
        "owned_count": owned,
        "single_use_maximum": single_use_maximum,
        "slider_maximum": slider_maximum,
        "selected_count": verified,
        "runtime_before": runtime_before,
        "count_source": "ocr",
        "slider_calibration": calibration_evidence,
    }


def _prepare_top_up_to_batch(context: Any, *, available_count: int) -> Iterator[Any]:
    """Prepare the Tianyan use dialog without committing the use action."""

    topup = MAGIC_INVASION_EXPLORE_BATCH_SIZE - int(available_count)
    if topup < 0:
        raise RuntimeError(f"魔道入侵可用探查次数越界：{available_count}>500")
    if topup == 0:
        return {"requested_topup": 0, "selected_count": 0}
    context.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "补充探查次数")
    yield from _wait_scene(context, (MAGIC_INVASION_ITEM_SCENE_ID,))
    context.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "天眼符条目")
    yield from _wait_scene(context, (MAGIC_INVASION_USE_SCENE_ID,))
    calibration = yield from _configure_use_quantity(context, quantity=topup)
    return {"requested_topup": topup, **dict(calibration or {})}


def _commit_prepared_top_up(context: Any) -> Iterator[Any]:
    """Commit an already prepared use dialog and return the authoritative map count."""

    context.click_shape_center(MAGIC_INVASION_USE_SCENE_ID, "使用")
    yield from _wait_scene(context, (MAGIC_INVASION_ITEM_SCENE_ID,))
    context.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "关闭道具列表")
    yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,))
    verified = _read_available_explore_count()
    if verified != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
        raise RuntimeError(f"魔道入侵补充后不是精确 500 次：{verified}")
    return verified


def ensure_magic_invasion_explore_batch_ready(context: Any) -> Iterator[Any]:
    """Idempotently fill the current challenge-page explore count to exactly 500."""

    scene, _score, _frame = yield from _wait_scene(
        context,
        (MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_ITEM_SCENE_ID),
    )
    if scene == MAGIC_INVASION_ITEM_SCENE_ID:
        context.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "关闭道具列表")
        yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,))

    available_before = _read_available_explore_count()
    prepared = yield from _prepare_top_up_to_batch(
        context,
        available_count=available_before,
    )
    requested_topup = int(prepared.get("requested_topup") or 0)
    available_after = (
        yield from _commit_prepared_top_up(context)
        if requested_topup
        else available_before
    )
    return {
        "available_before": available_before,
        "requested_topup": requested_topup,
        "available_after": available_after,
        **dict(prepared),
    }


def _read_tianyan_inventory() -> dict[str, Any]:
    counts, evidence = read_backpack_item_counts(
        (TIANYAN_ITEM_ID,),
        manager_key="magic-invasion-explore",
    )
    return {
        "count": int(counts.get(TIANYAN_ITEM_ID) or 0),
        "evidence": dict(evidence),
    }


def _compact_task_snapshot(activity_id: int) -> dict[str, Any]:
    snapshot = read_magic_invasion_task_reward_snapshot(int(activity_id))
    if not bool(snapshot.get("ok") and snapshot.get("available") and snapshot.get("complete")):
        raise RuntimeError(
            "魔道入侵任务权威快照不可用或不完整："
            f"{snapshot.get('reason') or snapshot.get('state') or 'unknown'}"
        )
    tasks = []
    for raw in snapshot.get("tasks") or ():
        if not isinstance(raw, Mapping):
            continue
        tasks.append(
            {
                "task_id": int(raw.get("task_id") or 0),
                "status": int(raw.get("status") or 0),
                "turn": int(raw.get("turn") or 0),
                "reward_time": int(raw.get("reward_time") or 0),
                "progress_complete": bool(raw.get("progress_complete")),
                "claimed": bool(raw.get("claimed")),
                "claimable": bool(raw.get("claimable")),
            }
        )
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "state": str(snapshot.get("state") or ""),
        "claimable_task_ids": [int(value) for value in snapshot.get("claimable_task_ids") or ()],
        "claimed_task_ids": [int(value) for value in snapshot.get("claimed_task_ids") or ()],
        "pending_task_ids": [int(value) for value in snapshot.get("pending_task_ids") or ()],
        "tasks": tasks,
        "source": str(snapshot.get("source") or ""),
        "protocol": str(snapshot.get("protocol") or ""),
    }


def _task_snapshot_did_not_regress(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> bool:
    """Conservatively reject a post-explore task snapshot that went backwards."""

    before_rows = {
        int(row.get("task_id") or 0): dict(row)
        for row in before.get("tasks") or ()
        if isinstance(row, Mapping) and int(row.get("task_id") or 0) > 0
    }
    after_rows = {
        int(row.get("task_id") or 0): dict(row)
        for row in after.get("tasks") or ()
        if isinstance(row, Mapping) and int(row.get("task_id") or 0) > 0
    }
    if set(before_rows) - set(after_rows):
        return False
    monotonic_fields = ("status", "turn", "reward_time")
    for task_id, old in before_rows.items():
        new = after_rows[task_id]
        if any(int(new.get(key) or 0) < int(old.get(key) or 0) for key in monotonic_fields):
            return False
        if bool(old.get("progress_complete")) and not bool(new.get("progress_complete")):
            return False
        if bool(old.get("claimed")) and not bool(new.get("claimed")):
            return False
    return True


def _legacy_phase(state: Any) -> str:
    phase = str(state or "ready")
    return "explore_armed" if phase == "armed" else phase


def _white_dragon_result(result_text: str) -> dict[str, Any]:
    matched = next(
        (alias for alias in WHITE_DRAGON_EFFECT_ALIASES if alias in str(result_text or "")),
        None,
    )
    return {
        "observed": matched is not None,
        "matched_alias": matched,
        "evidence_text": str(result_text or "")[:500],
    }


def _reward_currency_snapshot(occurrence: MagicInvasionOccurrence) -> dict[str, Any]:
    """Read the exact occurrence family's current Magic currency from Runtime."""

    _shop_id, currency_type, _cross_count = resolve_magic_invasion_shop_identity(
        cross_count=occurrence.server_count
    )
    snapshot = read_wallet_currency_snapshot(
        currency_type,
        # Supply/navigation can outlive the process-binding cache. This
        # explicit gameplay task must resolve its own read-only wallet root.
        allow_discovery=True,
        missing_as_zero=True,
    )
    if int(snapshot.get("currency_type") or 0) != int(currency_type):
        raise RuntimeError("魔道入侵兑币 Runtime 返回了错误币种")
    return {
        "currency_type": int(currency_type),
        "exchange_currency": int(snapshot.get("exchange_currency") or 0),
        "cumulative_currency": int(snapshot.get("cumulative_currency") or 0),
        "captured_at": str(snapshot.get("captured_at") or ""),
        "source": str(snapshot.get("source") or "runtime_memory"),
        "evidence": dict(snapshot.get("evidence") or {}),
    }


def execute_magic_invasion_explore_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    manage_schedule: bool = False,
    prepared_context: Any | None = None,
    prepared_schedule: Mapping[str, Any] | None = None,
    already_on_main_scene: bool = False,
    already_on_map_scene: bool = False,
) -> Iterator[Any]:
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    target_batches = int(payload.get("target_batches") or MAGIC_INVASION_TARGET_BATCHES)
    batch_size = int(payload.get("batch_size") or MAGIC_INVASION_EXPLORE_BATCH_SIZE)
    if target_batches != 3 or batch_size != 500:
        raise ValueError("魔道入侵生产策略固定为 3×500，未出兑币时至多补第 4 批")
    now = datetime.now().astimezone()
    schedule = dict(prepared_schedule) if prepared_schedule is not None else (
        read_fanxiu_activity_runtime_schedule(
            allow_discovery=True,
            force_refresh=True,
        )
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道入侵 Runtime 日程不可用，拒绝只按页面名称点击")
    occurrence = current_magic_invasion_occurrence(schedule, now=now)
    # Compatibility-only argument.  The activity executor never owns
    # Scheduler next_time or Scheduler payload, even when callers pass True.
    _ = manage_schedule
    expected_occurrence_id = str(payload.get("expected_occurrence_id") or "").strip()
    if expected_occurrence_id:
        if occurrence is None:
            raise RuntimeError(
                "统一榜单 checkpoint 指定的魔道 occurrence 当前已不在活动期，"
                "拒绝把零动作记为完成"
            )
        if occurrence.occurrence_id != expected_occurrence_id:
            raise RuntimeError(
                "魔道入侵当前 Runtime occurrence 与统一榜单 checkpoint 不一致"
            )
    if occurrence is None:
        return {
            "result": "success",
            "message": "魔道入侵：当前无活动实例",
            "performed_actions": False,
        }

    progress = load_magic_invasion_occurrence_progress(occurrence)
    progress["state"] = _legacy_phase(progress.get("state"))
    confirmed = list(progress.get("confirmed_batches") or [])
    if progress["state"] == "complete":
        completed_count = len(confirmed)
        return {
            "result": "success",
            "message": (
                f"魔道入侵 {occurrence.mode} 实例已幂等完成 "
                f"{completed_count}×500={completed_count * 500} 次"
            ),
            "performed_actions": False,
            "progress": progress,
        }

    baseline = progress.get("reward_currency_baseline")
    if not isinstance(baseline, Mapping):
        if confirmed:
            raise RuntimeError("魔道入侵已有探查批次但缺少开跑前兑币基线，拒绝猜测补第 4 批")
        baseline = _reward_currency_snapshot(occurrence)
        progress["reward_currency_baseline"] = baseline
        _set_progress(occurrence, progress)
    baseline_amount = int(baseline.get("exchange_currency") or 0)

    required_batches = MAGIC_INVASION_TARGET_BATCHES
    stored_reward_probe = progress.get("reward_currency_after_three")
    if len(confirmed) == MAGIC_INVASION_TARGET_BATCHES:
        if not isinstance(stored_reward_probe, Mapping):
            stored_reward_probe = _reward_currency_snapshot(occurrence)
            reward_plan = plan_magic_invasion_reward_batches(
                completed_batches=len(confirmed),
                currency_before=baseline_amount,
                currency_after=int(stored_reward_probe.get("exchange_currency") or 0),
            )
            progress.update({
                "reward_currency_after_three": stored_reward_probe,
                "reward_currency_delta_after_three": reward_plan.currency_delta,
                "reward_currency_observed_after_three": reward_plan.reward_observed,
                "fourth_batch_required": reward_plan.fourth_batch_required,
            })
            _set_progress(occurrence, progress)
        required_batches = (
            MAGIC_INVASION_MAX_BATCHES
            if bool(progress.get("fourth_batch_required"))
            else MAGIC_INVASION_TARGET_BATCHES
        )
    elif len(confirmed) >= MAGIC_INVASION_MAX_BATCHES:
        required_batches = MAGIC_INVASION_MAX_BATCHES

    if len(confirmed) >= required_batches:
        progress["state"] = "complete"
        progress["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _set_progress(occurrence, progress)
        completed_count = len(confirmed)
        return {
            "result": "success",
            "message": (
                f"魔道入侵 {occurrence.mode} 实例已完成 "
                f"{completed_count}×500={completed_count * 500} 次"
            ),
            "performed_actions": False,
            "progress": progress,
        }

    context = prepared_context or runner._behavior_tree_context(ctx, stop_event=stop_event)
    phase = str(progress["state"])
    if phase in {"ready", "confirmed"} or (already_on_main_scene and phase == "use_armed"):
        if already_on_map_scene:
            yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,), timeout_seconds=15.0)
        else:
            if not already_on_main_scene:
                yield from context.go_scene(66)
                yield from select_schedule_activity(
                    context,
                    r"魔道入侵",
                    enter=True,
                    runtime_schedule=schedule,
                    require_runtime_alignment=True,
                    now=now,
                )
            yield from enter_magic_invasion_challenge_page(context)

    def ensure_fast_explore_enabled() -> Iterator[Any]:
        # The result animation can briefly cover the checkmark after a batch.
        # Sample stable fresh frames before deciding that the switch is off.
        yield from context.wait_action_settle(1.0)
        for _attempt in range(4):
            if context.shape_matches(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开启态") is not None:
                return
            yield from context.wait_action_settle(0.5)

        context.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开关")
        for _attempt in range(6):
            yield from context.wait_action_settle(0.5)
            if context.shape_matches(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开启态") is not None:
                return
        raise RuntimeError("魔道入侵快速探索开关未进入开启态")

    while len(confirmed) < required_batches:
        if stop_event.is_set():
            raise InterruptedError()
        batch_index = len(confirmed) + 1
        base_explore_before = len(confirmed) * MAGIC_INVASION_EXPLORE_BATCH_SIZE
        phase = _legacy_phase(progress.get("state"))
        evidence = dict(progress.get("transaction_evidence") or progress.get("armed_evidence") or {})

        if phase in {"ready", "confirmed"}:
            yield from ensure_fast_explore_enabled()
            available_count = _read_available_explore_count()
            tianyan_before = _read_tianyan_inventory()
            task_before = _compact_task_snapshot(occurrence.activity_id)
            prepared = yield from _prepare_top_up_to_batch(
                context, available_count=available_count
            )
            evidence = {
                "base_explore_before": base_explore_before,
                "available_explore_count_before": available_count,
                **prepared,
                "tianyan_before": tianyan_before,
                "task_progress_before": task_before,
            }
            requested_topup = int(prepared["requested_topup"])
            if requested_topup:
                progress.update({
                    "state": "use_armed",
                    "batch_index": batch_index,
                    "transaction_evidence": evidence,
                })
                _set_progress(occurrence, progress)
                verified_topup = yield from _commit_prepared_top_up(context)
            else:
                verified_topup = available_count
        elif phase == "use_armed":
            requested_topup = int(evidence.get("requested_topup") or 0)
            if requested_topup <= 0:
                raise RuntimeError("魔道入侵 use_armed 缺少有效补充意图，拒绝恢复")
            tianyan_before = dict(evidence.get("tianyan_before") or {})
            tianyan_after = _read_tianyan_inventory()
            actual_topup = actual_magic_invasion_topup(
                inventory_before=int(tianyan_before.get("count") or 0),
                inventory_after=tianyan_after["count"],
            )
            if actual_topup != requested_topup:
                raise RuntimeError(
                    "魔道入侵 use_armed 后置事实不确定，禁止重放使用："
                    f"预期扣减 {requested_topup}，实际扣减 {actual_topup}"
                )
            scene, _score, _frame = yield from _wait_scene(
                context,
                (MAGIC_INVASION_ITEM_SCENE_ID, MAGIC_INVASION_MAP_SCENE_ID),
                timeout_seconds=15.0,
            )
            if scene == MAGIC_INVASION_ITEM_SCENE_ID:
                context.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "关闭道具列表")
                yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,))
            verified_topup = _read_available_explore_count()
        elif phase == "topup_confirmed":
            verified_topup = _read_available_explore_count()
        else:
            verified_topup = MAGIC_INVASION_EXPLORE_BATCH_SIZE

        if phase in {"ready", "confirmed", "use_armed"}:
            requested_topup = int(evidence.get("requested_topup") or 0)
            tianyan_before = dict(evidence.get("tianyan_before") or {})
            tianyan_after = _read_tianyan_inventory()
            actual_topup = actual_magic_invasion_topup(
                inventory_before=int(tianyan_before.get("count") or 0),
                inventory_after=tianyan_after["count"],
            )
            before_process = (
                dict(tianyan_before.get("evidence") or {}).get("pid"),
                dict(tianyan_before.get("evidence") or {}).get("process_start_ticks"),
            )
            after_process = (
                tianyan_after["evidence"].get("pid"),
                tianyan_after["evidence"].get("process_start_ticks"),
            )
            if before_process != after_process:
                raise RuntimeError("天眼符批次对账期间游戏进程代际变化，拒绝拼接快照")
            if actual_topup == requested_topup and 0 <= verified_topup < MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                # A legacy OCR baseline can underfill a batch. Close the proven
                # inventory debit before planning another use from current facts.
                settled = list(progress.get("settled_partial_topups") or [])
                settled.append({
                    **evidence,
                    "tianyan_after_topup": tianyan_after,
                    "tianyan_consumed": actual_topup,
                    "available_explore_count_after_topup": verified_topup,
                })
                progress.update({
                    "state": "confirmed" if confirmed else "ready",
                    "batch_index": len(confirmed),
                    "transaction_evidence": {},
                    "settled_partial_topups": settled,
                })
                _set_progress(occurrence, progress)
                continue
            if actual_topup != requested_topup or verified_topup != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                raise RuntimeError(
                    "天眼符权威库存精确扣减与地图 500 未成对成立，拒绝继续探查"
                )
            evidence.update({
                "available_explore_count_after_topup": verified_topup,
                "tianyan_after_topup": tianyan_after,
                "tianyan_consumed": actual_topup,
            })
            progress.update({
                "state": "topup_confirmed",
                "batch_index": batch_index,
                "transaction_evidence": evidence,
            })
            _set_progress(occurrence, progress)
            phase = "topup_confirmed"

        if phase == "topup_confirmed":
            if verified_topup != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                raise RuntimeError("魔道入侵 topup_confirmed 地图已不再是 500，拒绝探查")
            progress["state"] = "explore_armed"
            _set_progress(occurrence, progress)
            context.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "探查")
            yield from context.wait_action_settle(3.0)
            phase = "explore_armed"

        result_full_text = ""
        if phase == "explore_armed":
            scene, _score, _frame = yield from _wait_scene(
                context,
                (
                    MAGIC_INVASION_RESULT_SCENE_ID,
                    MAGIC_INVASION_OVERFLOW_SCENE_ID,
                    MAGIC_INVASION_EVENT_SCENE_ID,
                ),
                timeout_seconds=30.0,
            )
            if scene == MAGIC_INVASION_OVERFLOW_SCENE_ID:
                context.click_shape_center(MAGIC_INVASION_OVERFLOW_SCENE_ID, "确认覆盖")
                yield from _wait_scene(context, (MAGIC_INVASION_RESULT_SCENE_ID,), timeout_seconds=30.0)
                scene = MAGIC_INVASION_RESULT_SCENE_ID
            if scene == MAGIC_INVASION_RESULT_SCENE_ID:
                result_frame = context.cur_frame(update=True)
                # 结果页包含御灵等加成，可能大于本批的 500 次基础探查。
                result_explore_count = yield from read_magic_invasion_result_count(
                    context,
                    batch_index=batch_index,
                )
                result_full_text = context.ocr_text(result_frame)
                result_source = "result_page"
                task_after: dict[str, Any] = {}
            else:
                if scene == MAGIC_INVASION_EVENT_SCENE_ID:
                    context.click_shape(MAGIC_INVASION_EVENT_SCENE_ID, "稍后处理")
                    yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,))
                available_after = _read_available_explore_count()
                if available_after != 0:
                    raise RuntimeError(
                        "魔道入侵 explore_armed 后置事实不确定，禁止重放探查"
                    )
                task_after = _compact_task_snapshot(occurrence.activity_id)
                task_before = dict(evidence.get("task_progress_before") or {})
                if not _task_snapshot_did_not_regress(task_before, task_after):
                    raise RuntimeError("魔道入侵探查后任务权威快照发生回退，拒绝提交")
                result_explore_count = MAGIC_INVASION_EXPLORE_BATCH_SIZE
                result_source = "map_zero_and_task_non_regression"
            evidence.update({
                "result_explore_count": result_explore_count,
                "result_source": result_source,
                "task_progress_after": task_after,
                "result_full_text": result_full_text[:1000],
            })
            progress.update({"state": "result_observed", "transaction_evidence": evidence})
            _set_progress(occurrence, progress)
            phase = "result_observed"

        if phase == "result_observed":
            scene, _score, _frame = yield from _wait_scene(
                context,
                (
                    MAGIC_INVASION_RESULT_SCENE_ID,
                    MAGIC_INVASION_MAP_SCENE_ID,
                    MAGIC_INVASION_EVENT_SCENE_ID,
                ),
                timeout_seconds=30.0,
            )
            if scene == MAGIC_INVASION_RESULT_SCENE_ID:
                context.click_shape(MAGIC_INVASION_RESULT_SCENE_ID, "确定")
                scene, _score, _frame = yield from _wait_scene(
                    context,
                    (MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_EVENT_SCENE_ID),
                    timeout_seconds=30.0,
                )
            if scene == MAGIC_INVASION_EVENT_SCENE_ID:
                context.click_shape(MAGIC_INVASION_EVENT_SCENE_ID, "稍后处理")
                yield from _wait_scene(context, (MAGIC_INVASION_MAP_SCENE_ID,))
            available_count_after = _read_available_explore_count()
            # The result page proves the batch; natural regeneration may
            # already have added counts while its animation was displayed.
            if available_count_after < 0:
                raise RuntimeError("魔道入侵探查次数无效")
        result_explore_count = int(evidence.get("result_explore_count") or 0)
        task_after = dict(evidence.get("task_progress_after") or {})
        confirmed.append(
            {
                "batch_index": batch_index,
                "base_explore_before": base_explore_before,
                "base_explore_count": MAGIC_INVASION_EXPLORE_BATCH_SIZE,
                "base_explore_after": base_explore_before + MAGIC_INVASION_EXPLORE_BATCH_SIZE,
                "result_explore_count": result_explore_count,
                **evidence,
                "task_progress_after": task_after,
                "white_dragon": _white_dragon_result(str(evidence.get("result_full_text") or "")),
                "available_explore_count_after_result": available_count_after,
                "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        progress.update(
            {
                "state": "confirmed",
                "confirmed_batches": confirmed,
                "base_explore_count": len(confirmed) * MAGIC_INVASION_EXPLORE_BATCH_SIZE,
            }
        )
        for key in (
            "armed_batch_index", "armed_at", "armed_base_count", "armed_evidence",
            "batch_index", "transaction_evidence",
        ):
            progress.pop(key, None)
        _set_progress(occurrence, progress)

        if len(confirmed) == MAGIC_INVASION_TARGET_BATCHES:
            reward_after_three = _reward_currency_snapshot(occurrence)
            reward_plan = plan_magic_invasion_reward_batches(
                completed_batches=len(confirmed),
                currency_before=baseline_amount,
                currency_after=int(reward_after_three.get("exchange_currency") or 0),
            )
            progress.update({
                "reward_currency_after_three": reward_after_three,
                "reward_currency_delta_after_three": reward_plan.currency_delta,
                "reward_currency_observed_after_three": reward_plan.reward_observed,
                "fourth_batch_required": reward_plan.fourth_batch_required,
            })
            required_batches = reward_plan.required_batches
            _set_progress(occurrence, progress)
            runner._log(
                "success" if reward_plan.reward_observed else "info",
                (
                    "魔道前三批兑币对账："
                    f"{baseline_amount}->{int(reward_after_three['exchange_currency'])}，"
                    f"增量 {reward_plan.currency_delta}；"
                    + (
                        "已触发兑币奖励，不执行第 4 批"
                        if reward_plan.reward_observed
                        else "未触发兑币奖励，仅追加第 4 批"
                    )
                ),
            )
        inventory_before_count = int(
            dict(evidence.get("tianyan_before") or {}).get("count") or 0
        )
        inventory_after_count = int(
            dict(evidence.get("tianyan_after_topup") or {}).get("count")
            or inventory_before_count
        )
        runner._log(
            "success",
            f"魔道第 {batch_index}/{required_batches} 批对账：基础探查 "
            f"{base_explore_before}->{base_explore_before + 500}，"
            f"可用探查次数 {evidence['available_explore_count_before']}->500->{available_count_after}，"
            f"天眼符 {inventory_before_count}->{inventory_after_count}，"
            f"白龙马={'触发' if confirmed[-1]['white_dragon']['observed'] else '未触发'}",
        )

    progress["state"] = "complete"
    progress["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _set_progress(occurrence, progress)

    # Departure is best effort after the business terminal is durably stored.
    completed_count = len(confirmed)
    completed_explores = completed_count * MAGIC_INVASION_EXPLORE_BATCH_SIZE
    try:
        yield from _leave_magic_invasion_map(context)
    except Exception as exc:
        runner._log(
            "info",
            f"魔道入侵 {completed_explores} 次已提交；返回世界留待通用恢复：{exc}",
        )

    white_dragon_batches = [
        int(item.get("batch_index") or 0)
        for item in confirmed
        if bool(dict(item.get("white_dragon") or {}).get("observed"))
    ]
    white_dragon_message = (
        f"白龙马于第 {','.join(str(value) for value in white_dragon_batches)} 批触发"
        if white_dragon_batches
        else f"本次 {completed_explores} 次未从结果文字观察到白龙马"
    )
    reward_delta = int(progress.get("reward_currency_delta_after_three") or 0)
    reward_message = (
        f"前三批兑币增量 {reward_delta}，不补第 4 批"
        if reward_delta > 0
        else "前三批兑币无正增量，已且仅已补第 4 批"
    )
    message = (
        f"魔道入侵 {occurrence.mode} 实例 {occurrence.occurrence_id}："
        f"完成 {completed_count}×500={completed_explores} 次基础探查；"
        f"{reward_message}；{white_dragon_message}；"
        "邮件为可选独立流程"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "occurrence": asdict(occurrence),
        "progress": progress,
        "white_dragon": {
            "observed": bool(white_dragon_batches),
            "batch_indexes": white_dragon_batches,
            "message": white_dragon_message,
        },
    }


__all__ = [
    "MAGIC_INVASION_PROGRESS_KEY",
    "MagicInvasionOccurrence",
    "current_magic_invasion_occurrence",
    "enter_magic_invasion_challenge_page",
    "ensure_magic_invasion_explore_batch_ready",
    "execute_magic_invasion_explore_job",
    "parse_magic_invasion_result_explore_count",
    "load_magic_invasion_occurrence_evidence",
    "load_magic_invasion_occurrence_progress",
    "magic_invasion_occurrences",
    "next_magic_invasion_probe_time",
    "parse_available_explore_count",
    "parse_owned_item_count",
    "parse_selected_item_count",
    "store_magic_invasion_occurrence_evidence",
    "wait_magic_invasion_cover_after_schedule_entry",
]
