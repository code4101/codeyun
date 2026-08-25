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
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    _read_stable_store_scan,
)
from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
    read_lingxiao_gui_ticket_draws_from_tokens,
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


def _wait_scene(runtime: Any, target: int, *, timeout: float = 20.0) -> tuple[int, float]:
    deadline = time.monotonic() + max(1.0, float(timeout))
    last_scene, last_score = None, 0.0
    while time.monotonic() < deadline:
        last_scene, last_score, _frame = runtime.current_scene(
            list(HOLY_WOOD_KNOWN_SCENES), update=True
        )
        if int(last_scene or 0) == int(target) and float(last_score or 0) >= 80.0:
            return int(last_scene), float(last_score)
        time.sleep(0.25)
    raise RuntimeError(
        f"等待圣木祈愿 #{target} 超时：scene={last_scene}, score={last_score:.1f}"
    )


def _open_main(runtime: Any) -> None:
    deadline = time.monotonic() + 6.0
    scene, score, frame = None, 0.0, ""
    while time.monotonic() < deadline:
        scene, score, frame = runtime.current_scene(
            list(HOLY_WOOD_KNOWN_SCENES), update=True
        )
        if int(scene or 0) in HOLY_WOOD_KNOWN_SCENES and float(score or 0) >= 80.0:
            break
        time.sleep(0.25)
    if int(scene or 0) == HOLY_WOOD_RESULT_SCENE_ID and float(score or 0) >= 80.0:
        _close_result(runtime)
        return
    if int(scene or 0) == HOLY_WOOD_MAIN_SCENE_ID and float(score or 0) >= 80.0:
        return
    if int(scene or 0) in {
        HOLY_WOOD_TASK_SCENE_ID,
        HOLY_WOOD_STORE_SCENE_ID,
        HOLY_WOOD_PRAYER_TASK_SCENE_ID,
    } and float(score or 0) >= 80.0:
        runtime.click_shape(int(scene), "圣木祈愿页签", frame_data_url=frame)
        _wait_scene(runtime, HOLY_WOOD_MAIN_SCENE_ID)
        return
    raise RuntimeError("当前不在可靠的圣木祈愿系列页面")


def _close_result(runtime: Any, *, timeout: float = 30.0, max_clicks: int = 4) -> None:
    """Close an animated result page with bounded fresh-frame retries."""

    deadline = time.monotonic() + max(1.0, float(timeout))
    clicks = 0
    last_click_at = 0.0
    while time.monotonic() < deadline:
        scene, score, frame = runtime.current_scene(
            [HOLY_WOOD_MAIN_SCENE_ID, HOLY_WOOD_RESULT_SCENE_ID], update=True
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
            runtime.click_shape(HOLY_WOOD_RESULT_SCENE_ID, "继续", frame_data_url=frame)
            clicks += 1
            last_click_at = now
        time.sleep(0.25)
    raise RuntimeError(f"圣木祈愿结果页点击 {clicks} 次后仍未回主页")


def _open_tab(runtime: Any, scene_id: int, shape_title: str) -> int:
    _open_main(runtime)
    if scene_id == HOLY_WOOD_MAIN_SCENE_ID:
        return HOLY_WOOD_MAIN_SCENE_ID
    runtime.click_shape(
        HOLY_WOOD_MAIN_SCENE_ID,
        shape_title,
        frame_data_url=runtime.cur_frame(update=True),
    )
    if scene_id == HOLY_WOOD_TASK_SCENE_ID:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            landed, score, _frame = runtime.current_scene(
                [HOLY_WOOD_TASK_SCENE_ID, HOLY_WOOD_PRAYER_TASK_SCENE_ID],
                update=True,
            )
            if int(landed or 0) in {
                HOLY_WOOD_TASK_SCENE_ID,
                HOLY_WOOD_PRAYER_TASK_SCENE_ID,
            } and float(score or 0) >= 80.0:
                return int(landed)
            time.sleep(0.25)
        raise RuntimeError("等待圣木祈愿任务页超时")
    _wait_scene(runtime, scene_id)
    return int(scene_id)


def claim_holy_wood_tasks(runtime: Any, *, max_clicks: int = 20) -> dict[str, Any]:
    """Claim all currently claimable QuestMgr rows with per-task readback."""

    snapshot = read_bothdraw_task_runtime()
    if not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "圣木祈愿任务状态不完整"))
    if not list(snapshot.get("claimable") or []):
        _open_main(runtime)
        return {"clicked_count": 0, "stop_reason": "all_claimed"}
    task_scene_id = _open_tab(runtime, HOLY_WOOD_TASK_SCENE_ID, "活动任务")
    if all(str(row.get("name") or "").startswith("圣木祈愿") for row in snapshot.get("claimable") or []):
        for _attempt in range(3):
            scene, score, frame = runtime.current_scene(
                [HOLY_WOOD_TASK_SCENE_ID, HOLY_WOOD_PRAYER_TASK_SCENE_ID], update=True
            )
            if (
                int(scene or 0) == HOLY_WOOD_PRAYER_TASK_SCENE_ID
                and float(score or 0) >= 80.0
            ):
                task_scene_id = HOLY_WOOD_PRAYER_TASK_SCENE_ID
                break
            runtime.click_shape(
                HOLY_WOOD_TASK_SCENE_ID,
                "祈愿",
                frame_data_url=frame,
            )
            time.sleep(1.0)
        else:
            raise RuntimeError("圣木祈愿任务页未证明已切换到祈愿页签")
    clicked: list[int] = []
    while True:
        claimable = list(snapshot.get("claimable") or [])
        if not claimable:
            break
        if len(clicked) >= max(1, int(max_clicks)):
            raise RuntimeError("圣木祈愿任务领取超过安全上限")
        task_id = int(claimable[0].get("task_id") or 0)
        scene, score, frame = runtime.current_scene([task_scene_id], update=True)
        if int(scene or 0) != task_scene_id or float(score or 0) < 80.0:
            raise RuntimeError("圣木祈愿任务点击前页面身份无效")
        runtime.click_shape(task_scene_id, "进度", frame_data_url=frame)
        time.sleep(0.8)
        snapshot = read_bothdraw_task_runtime()
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
    _open_main(runtime)
    return {"clicked_count": len(clicked), "claimed_task_ids": clicked, "stop_reason": "all_claimed"}


def buy_holy_wood_spirit_stone_packs(
    runtime: Any,
    *,
    spend_budget: int = HOLY_WOOD_DEFAULT_SPEND_BUDGET,
    max_clicks: int = 8,
) -> dict[str, Any]:
    """Buy only approved virtual-currency packs and verify each ledger delta."""

    _open_tab(runtime, HOLY_WOOD_STORE_SCENE_ID, "活动商店")
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
            _open_main(runtime)
            return {"purchased_offer_ids": purchased, "spent": spent, "stop_reason": "all_approved_packs_bought"}
        target = selected[0]
        offer_id, unit_cost = int(target["offer_id"]), int(target["unit_cost"])
        frame, scan = _read_stable_store_scan(
            runtime,
            scene_id=HOLY_WOOD_STORE_SCENE_ID,
            region_title="区域",
            stability_timeout_seconds=12.0,
            stability_poll_seconds=0.25,
        )
        matches = [row for row in scan.targets if not row.is_cash and row.value == unit_cost]
        if len(matches) != 1:
            raise RuntimeError(f"圣木祈愿商店价格 {unit_cost} 可点击候选数为 {len(matches)}")
        runtime.click_frame_point(HOLY_WOOD_STORE_SCENE_ID, *matches[0].center)
        _wait_scene(runtime, HOLY_WOOD_STORE_SCENE_ID)
        time.sleep(0.5)
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
    raise RuntimeError("圣木祈愿商店购买超过安全点击上限")


def _visible_ticket_draws(runtime: Any, *, cost_per_draw: int) -> int:
    deadline = time.monotonic() + 8.0
    last_text = ""
    while time.monotonic() < deadline:
        frame = runtime.cur_frame(update=True)
        tokens = runtime.ocr_tokens_in_shapes(
            HOLY_WOOD_MAIN_SCENE_ID,
            ("祈愿券计数",),
            frame_data_url=frame,
            padding=0,
        )
        draws = read_lingxiao_gui_ticket_draws_from_tokens(
            list(tokens), cost_per_draw=cost_per_draw
        )
        last_text = "".join(str(token.get("text") or "") for token in tokens)
        if draws is None:
            draws = parse_holy_wood_ticket_draws(
                last_text, cost_per_draw=cost_per_draw
            )
        if draws is not None:
            return draws
        time.sleep(0.25)
    raise RuntimeError(f"圣木祈愿券计数无法形成安全分数：{last_text!r}")


def _set_ten_draw(runtime: Any, *, enabled: bool, activity_id: int) -> None:
    before = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
    if not before.get("complete") or not isinstance(before.get("ten_draw_enabled"), bool):
        raise RuntimeError(str(before.get("reason") or "圣木祈愿十抽开关状态不完整"))
    if bool(before["ten_draw_enabled"]) == bool(enabled):
        return
    runtime.click_shape(
        HOLY_WOOD_MAIN_SCENE_ID,
        "寻宝十次开关",
        frame_data_url=runtime.cur_frame(update=True),
    )
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        after = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
        if after.get("complete") and after.get("ten_draw_enabled") is bool(enabled):
            return
        time.sleep(0.25)
    raise RuntimeError("圣木祈愿十抽开关切换后未由 V_UseTenTimes 确认")


def claim_holy_wood_cumulative_rewards(runtime: Any) -> dict[str, Any]:
    """Claim the visible 10/20/30/40 milestones with Runtime readback."""

    claimed: list[int] = []
    while True:
        before = read_bothdraw_cumulative_rewards_runtime(
            include_selected_big_reward=False, visible_slot_count=4
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
        runtime.click_shape_center(
            HOLY_WOOD_MAIN_SCENE_ID,
            "累计奖励",
            x_ratio=(slot - 0.5) / 4,
            y_ratio=0.35,
        )
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            after = read_bothdraw_cumulative_rewards_runtime(
                include_selected_big_reward=False, visible_slot_count=4
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


def draw_all_holy_wood_tickets(runtime: Any, *, max_rounds: int = 64) -> dict[str, Any]:
    """Consume all visible prayer tickets without buying draw currency."""

    _open_main(runtime)
    draws: list[int] = []
    cumulative_claimed: list[int] = []
    for _round in range(max(1, int(max_rounds))):
        before = read_bothdraw_basic_runtime()
        if not before.get("complete") or int(before.get("activity_id") or 0) != HOLY_WOOD_ACTIVITY_ID:
            raise RuntimeError(str(before.get("reason") or "圣木祈愿抽奖运行态不完整"))
        available = _visible_ticket_draws(runtime, cost_per_draw=int(before["cost_per_draw"]))
        if available <= 0:
            claim = claim_holy_wood_cumulative_rewards(runtime)
            cumulative_claimed.extend(claim["claimed_reward_ids"])
            return {
                "draw_batches": draws,
                "draw_count": sum(draws),
                "claimed_reward_ids": cumulative_claimed,
                "stop_reason": "no_prayer_tickets",
            }
        batch = 10 if available >= 10 else 1
        _set_ten_draw(runtime, enabled=batch == 10, activity_id=HOLY_WOOD_ACTIVITY_ID)
        runtime.click_shape(
            HOLY_WOOD_MAIN_SCENE_ID,
            "寻宝",
            frame_data_url=runtime.cur_frame(update=True),
        )
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            after = read_bothdraw_basic_runtime()
            if after.get("complete") and int(after.get("x") or 0) == int(before.get("x") or 0) + batch:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"圣木祈愿抽取未形成精确 +{batch} 次增量")
        _wait_scene(runtime, HOLY_WOOD_RESULT_SCENE_ID)
        _close_result(runtime)
        draws.append(batch)
        claim = claim_holy_wood_cumulative_rewards(runtime)
        cumulative_claimed.extend(claim["claimed_reward_ids"])
    raise RuntimeError("圣木祈愿抽取超过安全轮数")


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
    runtime = runner._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
    # A Job attempt owns no business cursor from an earlier Cell.  Even when
    # the live frame is one of the Holy Wood pages, normalize through the
    # stable world hub and replay the idempotent workflow from its real entry.
    yield from runtime.go_scene(34)
    yield from runtime.go_scene(630)
    yield from runtime.wait_click(630, "圣木祈愿", timeout=10.0, label="圣木祈愿：打开主页")
    yield from runtime.wait_view(HOLY_WOOD_MAIN_SCENE_ID, timeout=20.0, label="圣木祈愿：确认主页")
    _open_main(runtime)
    task_rounds: list[dict[str, Any]] = []
    store: dict[str, Any] | None = None
    draw_rounds: list[dict[str, Any]] = []
    for _fixed_point_round in range(8):
        tasks = claim_holy_wood_tasks(
            runtime, max_clicks=int(payload.get("max_task_clicks", 20))
        )
        if store is None:
            store = buy_holy_wood_spirit_stone_packs(
                runtime,
                spend_budget=int(
                    payload.get("spend_budget", HOLY_WOOD_DEFAULT_SPEND_BUDGET)
                ),
            )
        draw = draw_all_holy_wood_tickets(
            runtime, max_rounds=int(payload.get("max_draw_rounds", 64))
        )
        task_rounds.append(tasks)
        draw_rounds.append(draw)
        if int(tasks["clicked_count"]) == 0 and int(draw["draw_count"]) == 0:
            break
    else:
        raise RuntimeError("圣木祈愿任务/抽取固定点超过 8 轮仍未收敛")
    if store is None:
        raise RuntimeError("圣木祈愿商店步骤未执行")
    yield from runtime.go_scene(34)
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
    "buy_holy_wood_spirit_stone_packs",
    "claim_holy_wood_tasks",
    "draw_all_holy_wood_tickets",
    "execute_holy_wood_prayer_task",
    "parse_holy_wood_ticket_draws",
    "validate_holy_wood_store_increment",
]
