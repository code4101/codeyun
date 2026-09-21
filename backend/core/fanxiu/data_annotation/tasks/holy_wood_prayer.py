from __future__ import annotations

"""Idempotent Holy Wood Prayer workflow for the Xianyuan Banquet event.

The workflow claims QuestMgr rewards, buys only the two explicitly approved
spirit-stone ticket packs, consumes only visible prayer tickets, claims reached
cumulative rewards, and verifies every mutation through read-only Runtime
state.  Re-running the job processes only remaining rows and tickets.
"""

import re
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryPolicy,
    LotteryRemainderMode,
)
from backend.core.fanxiu.activity.theme_lottery_policy import (
    ThemeLotteryPhase,
    ThemeLotteryTicket,
    TicketRetainability,
    resolve_theme_lottery_phase,
    resolve_theme_lottery_policy,
)
from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    _read_stable_store_scan,
)
from backend.core.fanxiu.instrumentation.activity_gift import (
    read_activity_gift_runtime_snapshot,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    read_bothdraw_basic_runtime,
    read_bothdraw_cumulative_rewards_runtime,
    read_bothdraw_task_runtime,
)
from backend.core.fanxiu.instrumentation.bothdraw_toggle import (
    read_bothdraw_ten_draw_runtime,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.xianyuan_banquet import (
    select_spirit_stone_store_offers,
)


HOLY_WOOD_ACTIVITY_ID = 30402
HOLY_WOOD_TASK_ID = "holy-wood-prayer"
HOLY_WOOD_TASK_TYPE = "holy_wood_prayer"
HOLY_WOOD_MAIN_SCENE_ID = 644
HOLY_WOOD_TASK_SCENE_ID = 645
HOLY_WOOD_STORE_SCENE_ID = 646
HOLY_WOOD_RESULT_SCENE_ID = 647
HOLY_WOOD_PRAYER_TASK_SCENE_ID = 648
HOLY_WOOD_KNOWN_SCENES = (644, 645, 646, 647, 648)
HOLY_WOOD_APPROVED_OFFERS = {3040201: 488, 3040202: 988}
HOLY_WOOD_DEFAULT_SPEND_BUDGET = 2952
# Both prayer-ticket items are user-confirmed non-retainable: this period's
# tickets must be consumed before the period ends, so the draw goal is always
# ``exhaust_all`` (a grand prize never stops the run).  The evidence is recorded
# here once instead of being re-guessed per call.
HOLY_WOOD_TICKET_ITEM_IDS = (40010, 40025)
HOLY_WOOD_TICKET_RETAINABILITY: TicketRetainability = "non_retainable"
HOLY_WOOD_TICKET_EVIDENCE = "用户确认圣木祈愿券不可跨期留存，本期必须用尽"
HOLY_WOOD_NORMAL_BATCH_SIZE = 10


def parse_holy_wood_ticket_draws(text: str, *, cost_per_draw: int) -> int | None:
    """Parse one or two visible ``owned/cost`` prayer-ticket counters."""

    cost = int(cost_per_draw)
    if cost <= 0:
        return None
    fractions = re.findall(r"(\d+)\s*[/|丨｜]\s*(\d+)", str(text or ""))
    if not 1 <= len(fractions) <= 2:
        return None
    pairs = [(int(owned), int(required)) for owned, required in fractions]
    if any(owned < 0 or required != cost for owned, required in pairs):
        return None
    return sum(owned for owned, _required in pairs)


def validate_holy_wood_store_increment(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    offer_id: int,
    unit_cost: int,
    wallet_before: int,
    wallet_after: int,
) -> None:
    """Require exactly one approved purchase and its exact wallet decrement."""

    def rows(snapshot: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
        return {
            int(row["id"]): dict(row)
            for row in snapshot.get("items") or []
            if isinstance(row, Mapping) and row.get("id") is not None
        }

    old_rows, new_rows = rows(before), rows(after)
    if old_rows.keys() != new_rows.keys():
        raise RuntimeError("圣木祈愿商店配置集在购买前后发生变化")
    changed = []
    for current_id, old in old_rows.items():
        delta = int(new_rows[current_id].get("purchased_times") or 0) - int(
            old.get("purchased_times") or 0
        )
        if delta:
            changed.append((current_id, delta))
    if changed != [(int(offer_id), 1)]:
        raise RuntimeError(f"圣木祈愿商店购买增量异常：{changed}")
    if int(wallet_before) - int(wallet_after) != int(unit_cost):
        raise RuntimeError(
            "圣木祈愿商店灵石扣减异常："
            f"{wallet_before}->{wallet_after}, expected={unit_cost}"
        )


def _wait_scene(context: Any, target: int, *, timeout: float = 20.0) -> tuple[int, float]:
    match = yield from context.wait_scene(
        [int(target)],
        wait=max(1.0, float(timeout)),
        label=f"圣木祈愿：等待 #{target}",
    )
    return int(match.scene_id), float(match.score or 0.0)


def _open_main(context: Any) -> None:
    _wait_scene_match = yield from context.wait_scene(list(HOLY_WOOD_KNOWN_SCENES), label='圣木祈愿：识别当前页面', wait=5.0, required=False)
    (scene, score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(scene or 0) == HOLY_WOOD_RESULT_SCENE_ID and float(score or 0) >= 80.0:
        yield from _close_result(context)
        return
    if int(scene or 0) == HOLY_WOOD_MAIN_SCENE_ID and float(score or 0) >= 80.0:
        return
    if int(scene or 0) in {
        HOLY_WOOD_TASK_SCENE_ID,
        HOLY_WOOD_STORE_SCENE_ID,
        HOLY_WOOD_PRAYER_TASK_SCENE_ID,
    } and float(score or 0) >= 80.0:
        context.click_shape(int(scene), "圣木祈愿页签", frame_data_url=frame)
        yield from _wait_scene(context, HOLY_WOOD_MAIN_SCENE_ID)
        return
    raise RuntimeError("当前不在可靠的圣木祈愿系列页面")


def _close_result(context: Any, *, timeout: float = 30.0, max_clicks: int = 4) -> None:
    """Close an animated result page with bounded fresh-frame retries."""

    deadline = time.monotonic() + max(1.0, float(timeout))
    clicks = 0
    last_click_at = 0.0
    while time.monotonic() < deadline:
        _wait_scene_match = yield from context.wait_scene([HOLY_WOOD_MAIN_SCENE_ID, HOLY_WOOD_RESULT_SCENE_ID], wait=5.0, required=False)
        (scene, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if int(scene or 0) == HOLY_WOOD_MAIN_SCENE_ID and float(score or 0) >= 80.0:
            return
        now = time.monotonic()
        if (
            int(scene or 0) == HOLY_WOOD_RESULT_SCENE_ID
            and float(score or 0) >= 90.0
            and clicks < max(1, int(max_clicks))
            and now - last_click_at >= 2.0
        ):
            context.click_shape(HOLY_WOOD_RESULT_SCENE_ID, "继续", frame_data_url=frame)
            clicks += 1
            last_click_at = now
        yield from context.wait_action_settle(0.25)
    raise RuntimeError(f"圣木祈愿结果页点击 {clicks} 次后仍未回主页")


def _open_tab(context: Any, scene_id: int, shape_title: str) -> int:
    """Open one Holy Wood tab, reusing an already-proven target page.

    A single-step caller may already be standing on the requested tab.  Detect
    that first and return directly instead of normalizing back to main and
    replaying the entrance, which keeps the existing tab and costs no action.
    """

    _pre_match = yield from context.wait_scene(
        list(HOLY_WOOD_KNOWN_SCENES),
        wait=5.0,
        required=False,
        label="圣木祈愿：识别当前页面",
    )
    (current, score, _pre_frame) = (
        (_pre_match.scene_id, _pre_match.score, _pre_match.frame_data_url)
        if _pre_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    current_scene = int(current or 0)
    confident = float(score or 0.0) >= 80.0
    if confident and scene_id == HOLY_WOOD_MAIN_SCENE_ID and current_scene == HOLY_WOOD_MAIN_SCENE_ID:
        return HOLY_WOOD_MAIN_SCENE_ID
    if confident and scene_id == HOLY_WOOD_STORE_SCENE_ID and current_scene == HOLY_WOOD_STORE_SCENE_ID:
        return HOLY_WOOD_STORE_SCENE_ID
    if confident and scene_id == HOLY_WOOD_TASK_SCENE_ID and current_scene in {
        HOLY_WOOD_TASK_SCENE_ID,
        HOLY_WOOD_PRAYER_TASK_SCENE_ID,
    }:
        return current_scene
    yield from _open_main(context)
    if scene_id == HOLY_WOOD_MAIN_SCENE_ID:
        return HOLY_WOOD_MAIN_SCENE_ID
    context.click_shape(
        HOLY_WOOD_MAIN_SCENE_ID,
        shape_title,
        frame_data_url=context.cur_frame(update=True),
    )
    if scene_id == HOLY_WOOD_TASK_SCENE_ID:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            _wait_scene_match = yield from context.wait_scene([HOLY_WOOD_TASK_SCENE_ID, HOLY_WOOD_PRAYER_TASK_SCENE_ID], wait=5.0, required=False)
            (landed, score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if int(landed or 0) in {
                HOLY_WOOD_TASK_SCENE_ID,
                HOLY_WOOD_PRAYER_TASK_SCENE_ID,
            } and float(score or 0) >= 80.0:
                return int(landed)
            yield from context.wait_action_settle(0.25)
        raise RuntimeError("等待圣木祈愿任务页超时")
    yield from _wait_scene(context, scene_id)
    return int(scene_id)


def claim_holy_wood_tasks(
    context: Any,
    *,
    max_clicks: int = 20,
    return_to_main: bool = True,
    strict_limit: bool = True,
) -> dict[str, Any]:
    """Claim all currently claimable QuestMgr rows with per-task readback.

    The defaults preserve the historical Job semantics: at the safety click
    limit raise, and normalize back to the main page when done.  A single-step
    caller may pass ``return_to_main=False, strict_limit=False`` to receive the
    actions confirmed so far with ``stop_reason="step_limit"`` and a
    ``remaining_count`` instead of failing, and to leave the current tab
    untouched; such a step never fabricates an ``all_claimed`` result.
    """

    snapshot = read_bothdraw_task_runtime(expected_activity_id=HOLY_WOOD_ACTIVITY_ID)
    if not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "圣木祈愿任务状态不完整"))
    if not list(snapshot.get("claimable") or []):
        if return_to_main:
            yield from _open_main(context)
        return {"clicked_count": 0, "stop_reason": "all_claimed"}
    task_scene_id = yield from _open_tab(context, HOLY_WOOD_TASK_SCENE_ID, "活动任务")
    if all(str(row.get("name") or "").startswith("圣木祈愿") for row in snapshot.get("claimable") or []):
        for _attempt in range(3):
            _wait_scene_match = yield from context.wait_scene([HOLY_WOOD_TASK_SCENE_ID, HOLY_WOOD_PRAYER_TASK_SCENE_ID], wait=5.0, required=False)
            (scene, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if (
                int(scene or 0) == HOLY_WOOD_PRAYER_TASK_SCENE_ID
                and float(score or 0) >= 80.0
            ):
                task_scene_id = HOLY_WOOD_PRAYER_TASK_SCENE_ID
                break
            context.click_shape(
                HOLY_WOOD_TASK_SCENE_ID,
                "祈愿",
                frame_data_url=frame,
            )
            yield from context.wait_action_settle(1.0)
        else:
            raise RuntimeError("圣木祈愿任务页未证明已切换到祈愿页签")
    clicked: list[int] = []
    while True:
        claimable = list(snapshot.get("claimable") or [])
        if not claimable:
            break
        if len(clicked) >= max(1, int(max_clicks)):
            if strict_limit:
                raise RuntimeError("圣木祈愿任务领取超过安全上限")
            if return_to_main:
                yield from _open_main(context)
            return {
                "clicked_count": len(clicked),
                "claimed_task_ids": clicked,
                "stop_reason": "step_limit",
                "remaining_count": len(claimable),
            }
        task_id = int(claimable[0].get("task_id") or 0)
        _wait_scene_match = yield from context.wait_scene([task_scene_id], wait=5.0, required=False)
        (scene, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if int(scene or 0) != task_scene_id or float(score or 0) < 80.0:
            raise RuntimeError("圣木祈愿任务点击前页面身份无效")
        context.click_shape(task_scene_id, "进度", frame_data_url=frame)
        yield from context.wait_action_settle(0.8)
        snapshot = read_bothdraw_task_runtime(expected_activity_id=HOLY_WOOD_ACTIVITY_ID)
        confirmed = next(
            (
                row
                for row in snapshot.get("tasks") or []
                if int(row.get("task_id") or 0) == task_id
            ),
            None,
        )
        if not snapshot.get("complete") or not confirmed or confirmed.get("state") != "claimed":
            raise RuntimeError(f"圣木祈愿任务 {task_id} 点击后未确认已领取")
        clicked.append(task_id)
    if return_to_main:
        yield from _open_main(context)
    return {"clicked_count": len(clicked), "claimed_task_ids": clicked, "stop_reason": "all_claimed"}


def buy_holy_wood_spirit_stone_packs(
    context: Any,
    *,
    spend_budget: int = HOLY_WOOD_DEFAULT_SPEND_BUDGET,
    max_clicks: int = 8,
    return_to_main: bool = True,
    strict_limit: bool = True,
) -> dict[str, Any]:
    """Buy only approved virtual-currency packs and verify each ledger delta.

    The defaults preserve the historical Job semantics: at the safety click
    limit raise, and normalize back to the main page when done.  A single-step
    caller may pass ``return_to_main=False, strict_limit=False`` to receive the
    purchases confirmed so far with ``stop_reason="step_limit"`` instead of
    failing, and to leave the current store tab untouched.  Every individual
    purchase is still validated against its exact ledger and wallet delta.
    """

    yield from _open_tab(context, HOLY_WOOD_STORE_SCENE_ID, "活动商店")
    spent = 0
    purchased: list[int] = []
    for _attempt in range(max(1, int(max_clicks))):
        before = read_activity_gift_runtime_snapshot([HOLY_WOOD_ACTIVITY_ID])
        wallet = read_wallet_currency_snapshot(1)
        if not before.get("complete"):
            raise RuntimeError(str(before.get("reason") or "圣木祈愿商店状态不完整"))
        rows = [dict(row) for row in before.get("items") or []]
        counts = {int(row["id"]): int(row.get("purchased_times") or 0) for row in rows}
        selection = select_spirit_stone_store_offers(
            [
                {
                    "id": row["id"],
                    "payId": row.get("pay_id"),
                    "costs": row.get("costs"),
                    "times": row.get("limit_times"),
                    "sort": row.get("sort"),
                }
                for row in rows
            ],
            purchase_counts=counts,
            wallet_balance=int(wallet["exchange_currency"]),
            spend_budget=max(0, int(spend_budget) - spent),
        )
        selected = [
            row
            for row in selection.get("selected") or []
            if HOLY_WOOD_APPROVED_OFFERS.get(int(row["offer_id"]))
            == int(row["unit_cost"])
        ]
        if not selected:
            if return_to_main:
                yield from _open_main(context)
            return {"purchased_offer_ids": purchased, "spent": spent, "stop_reason": "all_approved_packs_bought"}
        target = selected[0]
        offer_id, unit_cost = int(target["offer_id"]), int(target["unit_cost"])
        frame, scan = yield from _read_stable_store_scan(
            context,
            scene_id=HOLY_WOOD_STORE_SCENE_ID,
            region_title="区域",
            stability_timeout_seconds=12.0,
            stability_poll_seconds=0.25,
        )
        matches = [row for row in scan.targets if not row.is_cash and row.value == unit_cost]
        if len(matches) != 1:
            raise RuntimeError(f"圣木祈愿商店价格 {unit_cost} 可点击候选数为 {len(matches)}")
        context.click_frame_point(HOLY_WOOD_STORE_SCENE_ID, *matches[0].center)
        yield from _wait_scene(context, HOLY_WOOD_STORE_SCENE_ID)
        yield from context.wait_action_settle(0.5)
        after = read_activity_gift_runtime_snapshot([HOLY_WOOD_ACTIVITY_ID])
        after_wallet = read_wallet_currency_snapshot(1)
        validate_holy_wood_store_increment(
            before,
            after,
            offer_id=offer_id,
            unit_cost=unit_cost,
            wallet_before=int(wallet["exchange_currency"]),
            wallet_after=int(after_wallet["exchange_currency"]),
        )
        spent += unit_cost
        purchased.append(offer_id)
    if strict_limit:
        raise RuntimeError("圣木祈愿商店购买超过安全点击上限")
    if return_to_main:
        yield from _open_main(context)
    return {
        "purchased_offer_ids": purchased,
        "spent": spent,
        "stop_reason": "step_limit",
    }


def _visible_ticket_draws(context: Any, *, cost_per_draw: int) -> int:
    last_text = ""
    balances: dict[str, int] = {}
    for title, item_id in (("绑定", 40025), ("非绑定", 40010)):
        try:
            wallet = read_wallet_currency_snapshot(item_id)
            balances[title] = int(wallet["exchange_currency"]) // int(cost_per_draw)
        except FanxiuRuntimeMemoryError:
            # Unloaded currency is not zero. Only that counter falls back to
            # the current GUI; naturally loaded wallet values are authoritative.
            pass
    if len(balances) == 2:
        return sum(balances.values())
    # Runtime 恢复可能慢于 OCR 窗口；回退预算从真正开始识别时计算。
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        frame = context.cur_frame(update=True)
        counts: list[int] = []
        texts: list[str] = []
        # Full-frame OCR omits the small bound 1/1. Read each semantic
        # counter separately, and require both rather than treating omission
        # as zero. Cropping also excludes the neighboring ticket icons.
        for title in ("绑定", "非绑定"):
            if title in balances:
                counts.append(balances[title])
                texts.append(f"{title}:runtime={balances[title]}")
                continue
            tokens = context.ocr_tokens_in_shapes(
                HOLY_WOOD_MAIN_SCENE_ID, (title,), frame_data_url=frame,
                padding=4, crop=True,
            )
            text = "".join(str(token.get("text") or "") for token in tokens)
            texts.append(text)
            count = parse_holy_wood_ticket_draws(text, cost_per_draw=cost_per_draw)
            if count is not None:
                counts.append(count)
        last_text = " | ".join(texts)
        if len(counts) == 2:
            return sum(counts)
        time.sleep(0.25)
    raise RuntimeError(f"圣木祈愿券计数无法形成安全分数：{last_text!r}")


def _set_ten_draw(context: Any, *, enabled: bool, activity_id: int) -> None:
    before = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
    if not before.get("complete") or not isinstance(before.get("ten_draw_enabled"), bool):
        raise RuntimeError(str(before.get("reason") or "圣木祈愿十抽开关状态不完整"))
    if bool(before["ten_draw_enabled"]) == bool(enabled):
        return
    context.click_shape(
        HOLY_WOOD_MAIN_SCENE_ID,
        "寻宝十次开关",
        frame_data_url=context.cur_frame(update=True),
    )
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        after = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
        if after.get("complete") and after.get("ten_draw_enabled") is bool(enabled):
            return
        time.sleep(0.25)
    raise RuntimeError("圣木祈愿十抽开关切换后未由 V_UseTenTimes 确认")


def claim_holy_wood_cumulative_rewards(context: Any) -> dict[str, Any]:
    """Claim the visible 10/20/30/40 milestones with Runtime readback."""

    claimed: list[int] = []
    while True:
        before = read_bothdraw_cumulative_rewards_runtime(
            include_selected_big_reward=False,
            visible_slot_count=4,
            expected_activity_id=HOLY_WOOD_ACTIVITY_ID,
        )
        if not before.get("complete"):
            raise RuntimeError(str(before.get("reason") or "圣木祈愿累计奖励不完整"))
        visible = list(before.get("visible_claimable") or [])
        if not visible:
            return {"claimed_reward_ids": claimed, "stop_reason": "all_reached_rewards_claimed"}
        target = visible[0]
        slot = int(target.get("visible_slot") or 0)
        if not 1 <= slot <= 4:
            raise RuntimeError(f"圣木祈愿累计奖励槽位异常：{slot}")
        before_count = int(before.get("claimed_count") or 0)
        before_ids = {int(value) for value in before.get("claimed_ids") or []}
        context.click_shape_center(
            HOLY_WOOD_MAIN_SCENE_ID,
            "累计奖励",
            x_ratio=(slot - 0.5) / 4,
            y_ratio=0.35,
        )
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            after = read_bothdraw_cumulative_rewards_runtime(
                include_selected_big_reward=False,
                visible_slot_count=4,
                expected_activity_id=HOLY_WOOD_ACTIVITY_ID,
            )
            after_ids = {int(value) for value in after.get("claimed_ids") or []}
            target_id = int(target.get("id") or 0)
            if (
                after.get("complete")
                and int(after.get("claimed_count") or 0) > before_count
                and target_id in after_ids
            ):
                claimed.extend(sorted(after_ids - before_ids))
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("圣木祈愿累计奖励点击后未进入已领取账本")


def resolve_holy_wood_lottery_phase(
    *,
    final_day: date,
    now: datetime,
) -> ThemeLotteryPhase:
    """Resolve this period's Holy Wood draw phase from explicit clock inputs.

    Both ticket items (40010/40025) carry the user-confirmed
    ``non_retainable`` evidence, so the resolved goal is ``exhaust_all`` and the
    sub-ten remainder is ``defer`` until the final-day 21:00 tail, after which
    it becomes ``single``.  ``final_day`` and a timezone-aware ``now`` are
    required arguments: this helper never reads the clock and never hardcodes a
    period date, so the caller must pass the real current activity end date.
    """

    tickets = tuple(
        ThemeLotteryTicket(
            item_id=int(item_id),
            label="圣木祈愿券",
            retainability=HOLY_WOOD_TICKET_RETAINABILITY,
            detail=HOLY_WOOD_TICKET_EVIDENCE,
        )
        for item_id in HOLY_WOOD_TICKET_ITEM_IDS
    )
    resolution = resolve_theme_lottery_policy(tickets)
    return resolve_theme_lottery_phase(resolution, final_day=final_day, now=now)


def draw_all_holy_wood_tickets(
    context: Any,
    *,
    max_rounds: int = 64,
    remainder_mode: LotteryRemainderMode = "defer",
    strict_limit: bool = True,
) -> dict[str, Any]:
    """Consume all visible prayer tickets without buying draw currency.

    The non-retainable tickets use an ``exhaust_all`` goal, so a grand prize
    never stops the run.  ``remainder_mode`` defaults to ``defer``: a shortfall
    below a full ten is kept for the final-day tail instead of being spent on
    draws; pass ``remainder_mode="single"`` only to authorize the final tail.
    The shared policy calls that mode "single", but this GUI caps its ten-draw
    action to the available tickets, so even a short tail uses one ten-draw click.
    ``strict_limit=False`` returns ``stop_reason="step_limit"``
    at ``max_rounds`` so one Cell can process one bounded batch.
    """

    policy = LotteryPolicy(
        goal=LotteryGoal("exhaust_all"),
        remainder_mode=remainder_mode,
    )
    policy.validate()
    normal_batch_size = int(policy.normal_batch_size)
    yield from _open_main(context)
    draws: list[int] = []
    cumulative_claimed: list[int] = []
    for _round in range(max(1, int(max_rounds))):
        before = read_bothdraw_basic_runtime(expected_activity_id=HOLY_WOOD_ACTIVITY_ID)
        if not before.get("complete") or int(before.get("activity_id") or 0) != HOLY_WOOD_ACTIVITY_ID:
            raise RuntimeError(str(before.get("reason") or "圣木祈愿抽奖运行态不完整"))
        available = _visible_ticket_draws(context, cost_per_draw=int(before["cost_per_draw"]))
        if available <= 0:
            claim = claim_holy_wood_cumulative_rewards(context)
            cumulative_claimed.extend(claim["claimed_reward_ids"])
            if claim["claimed_reward_ids"]:
                continue
            return {
                "draw_batches": draws,
                "draw_count": sum(draws),
                "claimed_reward_ids": cumulative_claimed,
                "stop_reason": "no_prayer_tickets",
            }
        if available < normal_batch_size and policy.remainder_mode == "defer":
            claim = claim_holy_wood_cumulative_rewards(context)
            cumulative_claimed.extend(claim["claimed_reward_ids"])
            # Milestones may return tickets. Re-observe after any claim before
            # deciding that fewer than ten remain; 6 + 5 must draw again.
            if claim["claimed_reward_ids"]:
                continue
            return {
                "draw_batches": draws,
                "draw_count": sum(draws),
                "claimed_reward_ids": cumulative_claimed,
                "stop_reason": "terminal_remainder_deferred",
                "deferred_remainder": {"remaining_draws": int(available)},
            }
        # 实测余 3 张、保持十连，一次点击累计 +3；无需关闭十连逐张清尾。
        batch = min(normal_batch_size, available)
        _set_ten_draw(context, enabled=True, activity_id=HOLY_WOOD_ACTIVITY_ID)
        context.click_shape(
            HOLY_WOOD_MAIN_SCENE_ID,
            "寻宝",
            frame_data_url=context.cur_frame(update=True),
        )
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            after = read_bothdraw_basic_runtime(expected_activity_id=HOLY_WOOD_ACTIVITY_ID)
            if after.get("complete") and int(after.get("x") or 0) == int(before.get("x") or 0) + batch:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"圣木祈愿抽取未形成精确 +{batch} 次增量")
        yield from _wait_scene(context, HOLY_WOOD_RESULT_SCENE_ID)
        yield from _close_result(context)
        draws.append(batch)
        claim = claim_holy_wood_cumulative_rewards(context)
        cumulative_claimed.extend(claim["claimed_reward_ids"])
    if strict_limit:
        raise RuntimeError("圣木祈愿抽取超过安全轮数")
    return {
        "draw_batches": draws,
        "draw_count": sum(draws),
        "claimed_reward_ids": cumulative_claimed,
        "stop_reason": "step_limit",
    }


def execute_holy_wood_prayer_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Run the reusable Holy Wood Prayer fixed-point workflow."""

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("圣木祈愿作业缺少资产树路径")
    context = runner._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
    # 从当前真实页面重算整轮，不用旧 cursor，也不为幂等性绕回世界页。
    landing = yield from context.wait_scene(
        [*HOLY_WOOD_KNOWN_SCENES, 630, 34], wait=5, required=False,
        label="圣木祈愿：确认整轮起点",
    )
    if landing is None or landing.scene_id not in HOLY_WOOD_KNOWN_SCENES:
        yield from context.go_scene(630)
        yield from context.wait_click(630, "圣木祈愿", timeout=10.0, label="圣木祈愿：打开主页")
        yield from context.wait_scene([HOLY_WOOD_MAIN_SCENE_ID], wait=20.0, label="圣木祈愿：确认主页")
    yield from _open_main(context)
    task_rounds: list[dict[str, Any]] = []
    store: dict[str, Any] | None = None
    draw_rounds: list[dict[str, Any]] = []
    for _fixed_point_round in range(8):
        tasks = yield from claim_holy_wood_tasks(
            context, max_clicks=int(payload.get("max_task_clicks", 20))
        )
        if store is None:
            store = yield from buy_holy_wood_spirit_stone_packs(
                context,
                spend_budget=int(
                    payload.get("spend_budget", HOLY_WOOD_DEFAULT_SPEND_BUDGET)
                ),
            )
        draw = yield from draw_all_holy_wood_tickets(
            context, max_rounds=int(payload.get("max_draw_rounds", 64)),
            remainder_mode=payload.get("remainder_mode", "defer"),
        )
        task_rounds.append(tasks)
        draw_rounds.append(draw)
        if int(tasks["clicked_count"]) == 0 and int(draw["draw_count"]) == 0:
            break
    else:
        raise RuntimeError("圣木祈愿任务/抽取固定点超过 8 轮仍未收敛")
    if store is None:
        raise RuntimeError("圣木祈愿商店步骤未执行")
    yield from context.go_scene(34)
    task_count = sum(int(row["clicked_count"]) for row in task_rounds)
    draw_count = sum(int(row["draw_count"]) for row in draw_rounds)
    claimed_reward_ids = sorted(
        {
            int(value)
            for row in draw_rounds
            for value in row.get("claimed_reward_ids") or []
        }
    )
    message = (
        "圣木祈愿幂等完成："
        f"任务 {task_count} 项，灵石 {store['spent']}，"
        f"祈愿 {draw_count} 次，累计奖励 {len(claimed_reward_ids)} 项"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "task_rounds": task_rounds,
        "store": store,
        "draw_rounds": draw_rounds,
        "final_scene": 34,
    }


__all__ = [
    "HOLY_WOOD_ACTIVITY_ID",
    "HOLY_WOOD_DEFAULT_SPEND_BUDGET",
    "HOLY_WOOD_TASK_ID",
    "HOLY_WOOD_TASK_TYPE",
    "HOLY_WOOD_TICKET_EVIDENCE",
    "HOLY_WOOD_TICKET_ITEM_IDS",
    "HOLY_WOOD_TICKET_RETAINABILITY",
    "buy_holy_wood_spirit_stone_packs",
    "claim_holy_wood_tasks",
    "draw_all_holy_wood_tickets",
    "execute_holy_wood_prayer_task",
    "parse_holy_wood_ticket_draws",
    "resolve_holy_wood_lottery_phase",
    "validate_holy_wood_store_increment",
]
