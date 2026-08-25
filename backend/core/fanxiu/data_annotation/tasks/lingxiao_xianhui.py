from __future__ import annotations

"""Policy and scheduling facts for the Lingxiao Xianhui activity."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import re

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_fraction_numbers
from backend.core.fanxiu.instrumentation.bothdraw import (
    derive_bothdraw_ordinary_draw_delta,
    read_bothdraw_basic_runtime,
    read_bothdraw_cumulative_rewards_runtime,
    read_bothdraw_revenue_task_runtime,
)
from backend.core.fanxiu.instrumentation.bothdraw_toggle import (
    read_bothdraw_ticket_bindings_runtime,
    read_bothdraw_ten_draw_runtime,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.instrumentation.lingxiao_fuling import (
    read_lingxiao_daily_task_ui_runtime,
    read_lingxiao_fuling_panel_runtime,
)
from backend.core.fanxiu.instrumentation.lingxiao_special_offer import (
    read_lingxiao_special_offer_runtime,
)
from backend.core.fanxiu.activity.lingxiao_xianhui_lottery import (
    lingxiao_instance_id,
    record_lingxiao_lottery_point,
)
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    build_paired_draw_observations,
)
from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryPolicy,
    decide_lottery_action,
)


LINGXIAO_XIANHUI_TASK_ID = "lingxiao-xianhui"
LINGXIAO_ACTIVITY_ID = 3001003
LINGXIAO_DRAW_CURRENCY_TYPE = 29726
# #569 was captured with a vertically shifted header, so its OCR-only identity
# cannot recognize later live frames.  #575 anchors the stable title+rule header.
LINGXIAO_MAIN_SCENE_ID = 575
LINGXIAO_TASK_SCENE_ID = 570
LINGXIAO_FULING_SCENE_ID = 571
LINGXIAO_FULING_ACTIVATION_RESULT_SCENE_ID = 581
LINGXIAO_COVER_SCENE_ID = 574
LINGXIAO_SPECIAL_RECHARGE_SCENE_ID = 577
LINGXIAO_SPECIAL_RECHARGE_RESULT_SCENE_ID = 578
# #20「绿瓶」is a legitimate world-root variant, not a failed #34 return.
# It has its own verified “回到世界” action to normalize back to #34 before
# a #34-owned activity entry shape is used.
LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID = 20


@dataclass(frozen=True)
class LingxiaoDrawDecision:
    action: str
    draw_count: int
    reason: str


@dataclass(frozen=True)
class LingxiaoSpecialRechargeDecision:
    """The only allowed action for the user-selected no-recharge policy."""

    action: str
    reason: str


def _normalize_lingxiao_world(runtime: Any, *, label: str):
    """Accept either verified world-root landing, then normalize #20 to #34.

    A child-page return can legally take the historical #575→#34 edge or the
    #575→#20 green-bottle branch.  They are not interchangeable click owners:
    the Lingxiao entry is owned by #34, whereas #20 has a verified return
    shape.  Preserve both branches instead of weakening #34 recognition.
    """

    landing = yield from runtime.wait_view(
        34,
        LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID,
        timeout=12.0,
        label=label,
    )
    landing_id = int(getattr(landing, "id", landing))
    if landing_id == LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID:
        yield from runtime.wait_click(
            LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID,
            "回到世界",
            timeout=8.0,
            label=f"{label}：从绿瓶回到世界",
        )
        yield from runtime.wait_view(34, timeout=12.0, label=f"{label}：确认世界主页")
    elif landing_id != 34:
        raise RuntimeError(f"{label}：世界落点不受支持：#{landing_id}")
    return 34


def read_lingxiao_gui_ticket_draws(
    text: str,
    *,
    cost_per_draw: int | None,
) -> int | None:
    """Extract total available draws from #575's two distinct ticket pools.

    The left label is the bound/free pool and the right label is the paid
    pool. Both spend one ticket per draw. Their owned counts are intentionally
    independent (for example ``13/1`` and ``0/1``). This parser is strictly
    an operation-layer observation: the Runtime ticket reader is the business
    truth for eligibility and the Runtime before/after delta is the truth for
    how many draws the server actually accepted.
    """

    if cost_per_draw is None or int(cost_per_draw) <= 0:
        return None
    normalized = str(text or "")
    fractions = re.findall(r"\d+\s*[/|丨｜]\s*\d+", normalized)
    if len(fractions) != 2:
        return None
    values = [parse_ocr_fraction_numbers(item) for item in fractions]
    if any(item is None for item in values):
        return None
    pairs = [(int(item[0]), int(item[1])) for item in values if item is not None]
    if any(owned < 0 or cost != int(cost_per_draw) for owned, cost in pairs):
        return None
    return sum(owned for owned, _cost in pairs)


def read_lingxiao_gui_ticket_draws_from_tokens(
    tokens: list[dict[str, Any]],
    *,
    cost_per_draw: int | None,
) -> int | None:
    """Recover paired ticket labels from OCR line geometry before parsing.

    The #575 backdrop may concatenate nearby labels into one string (for
    example ``1/10/1``).  OCR's ``parent_line_id`` preserves the two actual
    label rows, so group it first and pass only two independently reconstructed
    fractions to the same conservative parser.
    """

    lines: dict[str, list[dict[str, Any]]] = {}
    for token in tokens:
        if not isinstance(token, dict):
            continue
        line_id = str(token.get("parent_line_id") or "")
        if not line_id:
            continue
        lines.setdefault(line_id, []).append(token)
    fractions: list[str] = []
    for line in lines.values():
        text = "".join(
            str(item.get("text") or "")
            for item in sorted(line, key=lambda item: float(item.get("x") or 0))
        )
        if re.fullmatch(r"\d+\s*[/|丨｜]\s*\d+", text):
            fractions.append(text)
    return read_lingxiao_gui_ticket_draws(
        " ".join(fractions), cost_per_draw=cost_per_draw
    )


def read_lingxiao_ticket_runtime() -> dict[str, Any]:
    """Read both Lingxiao ticket pools from Runtime, not OCR.

    FestivalTreasure exposes the primary wallet resource and its bound Item
    replacement on the active panel.  Their identities are first read from
    that panel, then each count is read from the corresponding loaded model.
    Missing WalletVO at zero is deliberately interpreted with the exact
    client-side ``GetCurrencyByType`` zero semantics; an unloaded WalletData
    model still fails closed in the lower-level reader.
    """

    bindings = read_bothdraw_ticket_bindings_runtime(
        expected_activity_id=LINGXIAO_ACTIVITY_ID
    )
    if not bindings.get("complete"):
        return {**bindings, "source": "lingxiao.ticket_runtime"}
    cost = int(bindings["cost_per_draw"])
    try:
        wallet = read_wallet_currency_snapshot(
            int(bindings["primary_resource_id"]), missing_as_zero=True
        )
        bound_counts, backpack = read_backpack_item_counts(
            [int(bindings["bound_replacement_item_id"])],
            manager_key=f"lingxiao-ticket-{int(bindings['activity_id'])}",
        )
    except Exception as exc:
        return {
            "ok": False, "available": False, "complete": False,
            "source": "lingxiao.ticket_runtime", "reason": str(exc),
            "bindings": bindings,
        }
    paid_currency = int(wallet["exchange_currency"])
    bound_item_count = int(bound_counts[int(bindings["bound_replacement_item_id"])])
    return {
        "ok": True, "available": True, "complete": True,
        "source": "lingxiao.ticket_runtime",
        "activity_id": int(bindings["activity_id"]),
        "cost_per_draw": cost,
        "primary_resource_id": int(bindings["primary_resource_id"]),
        "bound_replacement_item_id": int(bindings["bound_replacement_item_id"]),
        "wallet_draws": paid_currency // cost,
        "bound_item_draws": bound_item_count // cost,
        "available_draws": (paid_currency + bound_item_count) // cost,
        "evidence": {"bindings": bindings.get("evidence"), "wallet": wallet.get("evidence"), "backpack": backpack},
    }


def read_lingxiao_free_track_gui_state(text: str) -> str | None:
    """Read #571's free-track activation state from its own action label.

    ``FreeBuyBtn`` changes from the zero-cost action label ``免费奖励`` to
    ``已激活`` after the normal track has been activated.  This is a direct
    GUI state, unlike the aggregate red dot; anything else is deliberately
    incomplete rather than treated as an invitation to click.
    """

    normalized = re.sub(r"\s+", "", str(text or ""))
    if "已激活" in normalized:
        return "activated"
    if "免费奖励" in normalized:
        return "unactivated"
    return None


def lingxiao_free_track_claim_target_ids(
    snapshot: dict[str, Any], *, free_track_gui_state: str | None
) -> tuple[int, ...]:
    """Return the exact normal-track rewards a single claim action must get.

    The client-side card handler is ``ClaimAllRewards``: clicking one active
    normal-track card claims *all* reached normal rewards.  The click is only
    safe when Runtime can enumerate the complete target set first; a bright
    first card is merely the GUI carrier for that batch action.
    """

    if free_track_gui_state != "activated" or not snapshot.get("complete"):
        return ()
    track = snapshot.get("normal_track_state")
    if not isinstance(track, dict) or track.get("complete") is not True:
        return ()
    if track.get("activated") is not True:
        return ()
    targets = [
        int(row.get("reward_id") or 0)
        for row in snapshot.get("normal_items") or []
        if isinstance(row, dict)
        and row.get("is_box") is False
        and row.get("logical_left_mask_active") is True
    ]
    if any(reward_id <= 0 for reward_id in targets) or len(set(targets)) != len(targets):
        raise RuntimeError("灵霄仙会：免费普通轨 Runtime 目标 reward_id 不完整")
    return tuple(sorted(targets))


def should_try_lingxiao_free_track_claim(
    snapshot: dict[str, Any], *, free_track_gui_state: str | None
) -> bool:
    """Compatibility predicate for callers that only need a batch/no-op gate."""

    return bool(
        lingxiao_free_track_claim_target_ids(
            snapshot, free_track_gui_state=free_track_gui_state
        )
    )


def decide_lingxiao_special_recharge(
    *,
    runtime_round: int | None,
    runtime_first_free_claimed: bool | None,
    gui_first_free_claimable: bool | None,
) -> LingxiaoSpecialRechargeDecision:
    """Claim exactly the first-round gift, never infer later cards are free.

    The GUI reuses the word ``免费`` after paid round gates.  A later visible
    free card is not independently actionable: under the no-recharge policy it
    remains unreachable until its paid prerequisite has been satisfied.
    """

    if runtime_round is None or runtime_first_free_claimed is None:
        return LingxiaoSpecialRechargeDecision("wait", "连充轮次或首轮领取状态尚未由 Runtime 确认")
    if runtime_round != 1:
        return LingxiaoSpecialRechargeDecision("stop", "已离开首轮；后续免费卡受付费轮次前置锁定")
    if runtime_first_free_claimed:
        return LingxiaoSpecialRechargeDecision("stop", "第1轮免费奖励已经领取")
    if gui_first_free_claimable is not True:
        return LingxiaoSpecialRechargeDecision("wait", "GUI 未确认第1轮免费按钮可领")
    return LingxiaoSpecialRechargeDecision("claim_first_free", "第1轮免费奖励已由 Runtime 与 GUI 共同确认")


def decide_lingxiao_draw(
    *,
    activity_id: int,
    runtime_draws: int | None,
    ten_draw_enabled: bool | None,
) -> LingxiaoDrawDecision:
    """Always use the ten-draw control while either ticket pool has a draw.

    The game caps a ten-draw request to the remaining total automatically.
    Therefore three tickets still authorize the same ten-draw action; the
    requested batch is 10 while the verified post-action Runtime delta is 3.
    """

    if int(activity_id) != LINGXIAO_ACTIVITY_ID:
        return LingxiaoDrawDecision("wait", 0, "当前不是灵霄仙会活动实例")
    if runtime_draws is None:
        return LingxiaoDrawDecision("wait", 0, "Runtime 未完整确认两类寻宝券总数")
    if int(runtime_draws) <= 0:
        return LingxiaoDrawDecision("wait", 0, "免费与付费寻宝券均已用尽")
    if ten_draw_enabled is not True:
        return LingxiaoDrawDecision("enable_ten", 0, "先复验并开启寻宝十次")
    decision = decide_lottery_action(
        {
            "complete": True,
            "available_draws": int(runtime_draws),
            "progress": 0,
            "hit_count": 0,
            "claimable": [],
        },
        policy=LotteryPolicy(
            goal=LotteryGoal("exhaust_all"), remainder_mode="capped_ten"
        ),
    )
    return LingxiaoDrawDecision("draw", decision.requested_batch_size, decision.reason)


def next_lingxiao_check_time(
    *,
    now: datetime | None = None,
    activity_end: datetime | None = None,
) -> str | None:
    """Schedule the next observation without sleeping through a live activity.

    Daily reset is the low-churn default while there is room for another reset.
    On the final calendar day it may already be after 00:05 even though the
    activity is still open for many hours.  Returning ``None`` in that state
    silently strands unfinished free claims and newly earned tickets.  Use a
    bounded in-activity recheck instead; only the confirmed closed boundary
    is allowed to disable the task.
    """

    current = now or datetime.now().astimezone()
    candidate = (current + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0
    )
    if activity_end is not None:
        end = activity_end.astimezone() if activity_end.tzinfo else activity_end
        if (candidate.tzinfo is None) != (end.tzinfo is None):
            # Scheduler test/dispatch values may be naive while live OCR
            # parsing is local-time aware.  They describe the same local wall
            # clock, so normalize only for the comparison.
            if candidate.tzinfo is None:
                end = end.replace(tzinfo=None)
            else:
                candidate = candidate.replace(tzinfo=None)
        if current >= end:
            return None
        if candidate >= end:
            # Keep a final-day observation alive without trying to dispatch
            # at or after the closed boundary.  Two hours avoids churn while
            # still picking up free task/reward changes before the event ends.
            intra_day = current + timedelta(hours=2)
            latest_safe = end - timedelta(minutes=5)
            candidate = min(intra_day, latest_safe)
            if candidate <= current:
                return None
    return candidate.strftime("%Y-%m-%d %H:%M:%S")


def activity_end_from_text(value: str) -> datetime | None:
    """Parse the observed ``MM/DD HH:MM:SS-MM/DD HH:MM:SS`` activity range."""

    text = str(value or "").strip()
    try:
        right = text.rsplit("-", 1)[1].strip()
        current = datetime.now().astimezone()
        return datetime.strptime(f"{current.year}/{right}", "%Y/%m/%d %H:%M:%S").replace(tzinfo=current.tzinfo)
    except (IndexError, ValueError):
        return None


def _activity_range_from_fragments(fragments: list[dict[str, Any]]) -> str:
    text = " ".join(str(item.get("text") or "") for item in fragments)
    match = re.search(r"\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s*-\s*\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}", text)
    if match:
        return match.group(0)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in fragments:
        grouped.setdefault(str(item.get("parent_line_id") or ""), []).append(item)
    for line in grouped.values():
        compact = "".join(
            re.sub(r"[^\d-]", "", str(item.get("text") or ""))
            for item in sorted(line, key=lambda item: float(item.get("x") or 0))
        )
        numeric = re.search(r"(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", compact)
        if numeric:
            left = "/".join(numeric.group(index) for index in (1, 2)) + " " + ":".join(numeric.group(index) for index in (3, 4, 5))
            right = "/".join(numeric.group(index) for index in (6, 7)) + " " + ":".join(numeric.group(index) for index in (8, 9, 10))
            return f"{left}-{right}"
    return ""


def _record_scatter_snapshot(snapshot: dict[str, Any], activity_end: datetime | None) -> None:
    """Persist only a coherent live observation tied to this activity instance."""

    if activity_end is None or not snapshot.get("complete"):
        return
    from sqlmodel import Session

    from backend.db import engine

    with Session(engine) as session:
        record_lingxiao_lottery_point(
            session,
            snapshot=snapshot,
            instance_id=lingxiao_instance_id(activity_end),
        )


def build_lingxiao_ten_draw_observations(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    action_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the two persistent Runtime observations for one ten-draw request.

    The button request is always ten-draw.  The game is allowed to cap the
    actual server-side count to the remaining total, so ``batch_size`` is the
    verified ``Δx`` (including 1..9), while ``draw_mode`` remains
    ``ten_draw``.  ``y`` and the per-prize increments come exclusively from
    the ordinary-pool Runtime counters; result-page artwork is never read as
    reward truth.
    """

    if not str(action_id or "").strip():
        raise ValueError("灵霄十连记录缺少动作 identity")
    delta = derive_bothdraw_ordinary_draw_delta(before, after)
    actual_count = int(delta["draw_delta"])
    if actual_count > 10:
        raise ValueError(f"灵霄十连实际抽数超过请求上限：{actual_count}")

    after_evidence = dict(after.get("evidence") or {})
    after_evidence["ordinary_big_prize_delta"] = {
        "draw_delta": actual_count,
        "big_delta": int(delta["big_delta"]),
        "items": list(delta["hit_big_prize_items"]),
    }
    paired_after = {
        **after,
        "hit_big_prize_items": list(delta["hit_big_prize_items"]),
        "evidence": after_evidence,
    }
    # Request semantics stay ten-draw even if only three tickets were left.
    # The shared ledger derives ``batch_size`` from the authoritative Δx and
    # verifies wallet/progress deltas before either point is persisted.
    pool_identity = {
        "library_id": LINGXIAO_ACTIVITY_ID,
        "item_id": LINGXIAO_ACTIVITY_ID,
        "name": "凌霄仙会活动奖池",
    }
    return build_paired_draw_observations(
        {**before, "selected_big_reward": pool_identity},
        {**paired_after, "selected_big_reward": pool_identity},
        action_id=str(action_id),
        draw_mode="ten_draw",
        requested_batch_size=10,
    )


def read_lingxiao_cumulative_rewards_runtime() -> dict[str, Any]:
    """Read the live, non-formula cumulative ladder for ordinary-pool Lingxiao."""

    snapshot = read_bothdraw_cumulative_rewards_runtime(
        include_selected_big_reward=False,
        # #575's current main page exposes exactly four clickable slots.
        # Later tiers require a fresh viewport/scroll mapping rather than an
        # invented second row.
        visible_slot_count=4,
    )
    if not snapshot.get("complete"):
        # Preserve the real typed reason (for example a manager not loaded).
        # A missing activity_id on an error snapshot is not evidence that a
        # different activity is active.
        return snapshot
    if int(snapshot.get("activity_id") or 0) != LINGXIAO_ACTIVITY_ID:
        return {
            **snapshot,
            "ok": False,
            "available": False,
            "complete": False,
            "reason": "当前累计奖励 Runtime 不是灵霄仙会",
        }
    return snapshot


def claim_lingxiao_cumulative_rewards(
    runtime: Any, *, initial_snapshot: dict[str, Any] | None = None
):
    """Claim every currently visible Runtime-authorized cumulative reward.

    Runtime owns both the reward id and its ``visible_slot``.  The four GUI
    shapes only materialize that already-authorized slot; every click is
    followed by a read-back of the exact same reward id.
    """

    claimed: list[int] = []
    for _attempt in range(4):
        snapshot = (
            initial_snapshot
            if _attempt == 0 and isinstance(initial_snapshot, dict)
            else read_lingxiao_cumulative_rewards_runtime()
        )
        if not snapshot.get("complete"):
            raise RuntimeError(
                "灵霄仙会：累计寻宝 Runtime 未完整加载："
                f"{snapshot.get('reason') or 'unknown'}"
            )
        candidates = [
            item
            for item in snapshot.get("visible_claimable") or []
            if isinstance(item, dict)
        ]
        if not candidates:
            return claimed
        target = min(candidates, key=lambda item: int(item.get("visible_slot") or 99))
        reward_id = int(target.get("id") or 0)
        slot = int(target.get("visible_slot") or 0)
        if reward_id <= 0 or slot not in (1, 2, 3, 4):
            raise RuntimeError("灵霄仙会：可领累计奖励缺少已验证 GUI 槽位")
        yield from runtime.wait_click(
            LINGXIAO_MAIN_SCENE_ID,
            f"累计寻宝第{slot}档（Runtime slot={slot}）",
            timeout=8.0,
            label=f"灵霄仙会：领取累计寻宝 reward_id={reward_id}",
        )
        yield from runtime.wait_action_settle(2.0)
        after = read_lingxiao_cumulative_rewards_runtime()
        if reward_id not in {int(value) for value in after.get("claimed_ids") or []}:
            raise RuntimeError(f"灵霄仙会：累计 reward_id={reward_id} 点击后未确认已领取")
        claimed.append(reward_id)
    raise RuntimeError("灵霄仙会：累计奖励领取超过已验证的四槽上限")


def read_lingxiao_fuling_tasks_runtime() -> dict[str, Any]:
    """Read #570's live task groups from its activity-owned Runtime model.

    The current client presents the four ``日常`` rows as group 4 and the
    60-row ``进阶`` ladder as group 5.  This association was verified against
    the live #570 frame.  A future client layout that changes either group is
    intentionally not guessed: task clicks require a fresh GUI mapping.
    """

    snapshot = read_bothdraw_revenue_task_runtime(
        expected_activity_id=LINGXIAO_ACTIVITY_ID
    )
    if not snapshot.get("complete"):
        return snapshot
    groups = {
        int(group.get("group_id") or 0): group
        for group in snapshot.get("task_groups") or []
        if isinstance(group, dict)
    }
    daily = groups.get(4)
    advanced = groups.get(5)
    if not isinstance(daily, dict) or not isinstance(advanced, dict):
        return {
            **snapshot,
            "ok": False,
            "available": False,
            "complete": False,
            "reason": "灵霄福令任务 Runtime 分组与已验证的日常/进阶布局不一致",
        }
    if int(daily.get("task_count") or 0) != 4 or int(advanced.get("task_count") or 0) <= 0:
        return {
            **snapshot,
            "ok": False,
            "available": False,
            "complete": False,
            "reason": "灵霄福令任务 Runtime 行数与当前 GUI 布局不一致",
        }
    return {**snapshot, "daily": daily, "advanced": advanced}


def read_lingxiao_fuling_rewards_runtime() -> dict[str, Any]:
    """Read #571's current free-track eligibility without claiming anything."""

    return read_lingxiao_fuling_panel_runtime(
        expected_activity_id=LINGXIAO_ACTIVITY_ID
    )


def choose_lingxiao_daily_claim(task_snapshot: dict[str, Any], ui_snapshot: dict[str, Any]) -> dict[str, int] | None:
    """Join the authority task state to #570's *current* visual row order."""
    tasks = (task_snapshot.get("daily") or {}).get("tasks") or []
    states = {int(row["task_id"]): str(row.get("state")) for row in tasks if isinstance(row, dict) and row.get("task_id")}
    rows = ui_snapshot.get("rows") or []
    if len(states) != 4 or len(rows) != 4 or {int(row.get("task_id") or 0) for row in rows} != set(states):
        raise RuntimeError("灵霄仙会：福令日常 Runtime 与当前 ScrollView 行集合不一致")
    for row in rows:
        task_id = int(row["task_id"])
        if states[task_id] == "claimable" and row.get("is_finished") is False:
            return {"task_id": task_id, "ui_index": int(row["ui_index"])}
    return None


def read_lingxiao_special_recharge_runtime() -> dict[str, Any]:
    """Read #577's actual first-reachable package without purchasing it."""

    return read_lingxiao_special_offer_runtime(
        expected_parent_activity_id=LINGXIAO_ACTIVITY_ID
    )


def claim_lingxiao_special_recharge_first_free(
    runtime: Any, *, initial_snapshot: dict[str, Any] | None = None
):
    """Claim only the one currently reachable zero-price SpecialOffer pack.

    "Free" copy elsewhere is not actionable.  This action uses the loaded
    package chain to prove the exact offer id, then checks that same offer's
    purchase count increased after the activity-specific result layer closes.
    It never clicks a package with choice configuration or a positive pay id.
    """

    before = initial_snapshot or read_lingxiao_special_recharge_runtime()
    if not before.get("complete"):
        raise RuntimeError(
            "灵霄仙会：特惠连充 Runtime 未完整加载："
            f"{before.get('reason') or 'unknown'}"
        )
    offer = before.get("first_reachable")
    if before.get("state") != "free_claimable" or not isinstance(offer, dict):
        return None
    offer_id = int(offer.get("id") or 0)
    pay_id = int(offer.get("payId") or 0)
    choice_count = int(offer.get("optPacCount") or 0)
    buy_before = int(offer.get("buy_num") or 0)
    limit = int(offer.get("personlimit") or 0)
    if offer_id <= 0 or pay_id > 0 or choice_count != 0 or not 0 <= buy_before < limit:
        raise RuntimeError("灵霄仙会：特惠连充首个可达包不满足无付费领取契约")
    yield from runtime.wait_click(
        LINGXIAO_SPECIAL_RECHARGE_SCENE_ID,
        "第1轮免费领取",
        timeout=8.0,
        label=f"灵霄仙会：领取特惠连充免费 offer_id={offer_id}",
    )
    yield from runtime.wait_view(
        LINGXIAO_SPECIAL_RECHARGE_RESULT_SCENE_ID,
        timeout=12.0,
        label="灵霄仙会：确认特惠连充免费领取结果",
    )
    yield from runtime.wait_click(
        LINGXIAO_SPECIAL_RECHARGE_RESULT_SCENE_ID,
        "继续",
        timeout=8.0,
        label="灵霄仙会：关闭特惠连充免费领取结果",
    )
    yield from runtime.wait_view(
        LINGXIAO_SPECIAL_RECHARGE_SCENE_ID,
        timeout=12.0,
        label="灵霄仙会：确认回到特惠连充",
    )
    after = read_lingxiao_special_recharge_runtime()
    if not after.get("complete"):
        raise RuntimeError("灵霄仙会：特惠连充免费领取后 Runtime 未完整加载")
    purchased = next(
        (
            row for row in after.get("packages") or []
            if isinstance(row, dict) and int(row.get("id") or 0) == offer_id
        ),
        None,
    )
    if not isinstance(purchased, dict) or int(purchased.get("buy_num") or 0) != buy_before + 1:
        raise RuntimeError(
            f"灵霄仙会：特惠连充免费 offer_id={offer_id} 领取后购买次数未精确递增"
        )
    return offer_id


def _runtime_process_identity(snapshot: dict[str, Any]) -> tuple[int, int] | None:
    """Extract one process incarnation from direct or composed evidence.

    The Lingxiao state reader joins three independent strict-read probes.  A
    game restart between them would make same-looking counters unsafe to join,
    so the readers' process identity is an invariant, not diagnostics only.
    Older/offline fixtures may omit evidence entirely; they remain useful for
    strategy tests but production readers always provide it.
    """

    evidence = snapshot.get("evidence")
    if not isinstance(evidence, dict):
        return None
    candidates = [evidence]
    candidates.extend(
        value for value in evidence.values() if isinstance(value, dict)
    )
    identities = {
        (int(value["pid"]), int(value["process_start_ticks"]))
        for value in candidates
        if value.get("pid") is not None and value.get("process_start_ticks") is not None
    }
    if len(identities) > 1:
        raise RuntimeError("灵霄 Runtime 单个探针混入多个游戏进程身份")
    return next(iter(identities), None)


def read_lingxiao_runtime_state() -> dict[str, Any]:
    """Join the ordinary-pool point and the live cumulative ladder in #575.

    Bothdraw's cumulative manager is page-bound for this activity.  It must be
    read while the main treasure page is still live, rather than after the Job
    has returned to #34 where another activity may become the manager's active
    instance.  The two projections are accepted only when they name the same
    activity and draw count.
    """

    basic = read_bothdraw_basic_runtime()
    if int(basic.get("activity_id") or 0) != LINGXIAO_ACTIVITY_ID:
        return {
            **basic,
            "complete": False,
            "reason": "当前普通奖池 Runtime 不是灵霄仙会",
        }
    tickets = read_lingxiao_ticket_runtime()
    if not tickets.get("complete"):
        return {
            **basic,
            "tickets": tickets,
            "complete": False,
            "reason": str(tickets.get("reason") or "灵霄双券池 Runtime 不完整"),
        }
    cumulative = read_lingxiao_cumulative_rewards_runtime()
    if not cumulative.get("complete"):
        return {
            **basic, "tickets": tickets,
            "cumulative": cumulative,
            "complete": False,
            "reason": str(cumulative.get("reason") or "灵霄累计奖励 Runtime 不完整"),
        }
    if int(cumulative.get("x") or 0) != int(basic.get("x") or 0):
        return {
            **basic, "tickets": tickets,
            "cumulative": cumulative,
            "complete": False,
            "reason": "灵霄普通奖池与累计奖励抽数不一致",
        }
    try:
        process_identities = {
            identity
            for identity in (
                _runtime_process_identity(basic),
                _runtime_process_identity(tickets),
                _runtime_process_identity(cumulative),
            )
            if identity is not None
        }
    except RuntimeError as exc:
        return {
            **basic, "tickets": tickets, "cumulative": cumulative,
            "complete": False, "reason": str(exc),
        }
    if len(process_identities) > 1:
        return {
            **basic, "tickets": tickets, "cumulative": cumulative,
            "complete": False,
            "reason": "灵霄 Runtime 联合快照跨越了不同游戏进程，拒绝合并",
        }
    return {
        **basic,
        "tickets": tickets,
        # This is the only draw qualification exposed to callers.  The older
        # basic reader's single-wallet value remains evidence only.
        "available_draws": int(tickets["available_draws"]),
        "cumulative": cumulative,
        "complete": True,
    }


def execute_lingxiao_xianhui_job(
    runner: Any, ctx: dict[str, Any], payload: dict[str, Any], stop_event: Any
):
    """Claim verified free resources and schedule the next safe activity pass.

    Draws remain deliberately fail-closed until a verified ticket balance,
    live ten-draw toggle and dedicated result-page closure are all available.
    """

    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    task_id = str(payload.get("__scheduler_task_id") or LINGXIAO_XIANHUI_TASK_ID)
    scene_id, _score, _frame = runtime.current_scene(
        [34, LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID, LINGXIAO_COVER_SCENE_ID, LINGXIAO_MAIN_SCENE_ID, LINGXIAO_TASK_SCENE_ID, LINGXIAO_FULING_SCENE_ID], update=True,
        handle_interruptions=False,
    )
    if scene_id not in {LINGXIAO_COVER_SCENE_ID, LINGXIAO_MAIN_SCENE_ID, LINGXIAO_TASK_SCENE_ID, LINGXIAO_FULING_SCENE_ID}:
        # A popup such as #530 is an optional overlay, not evidence that the
        # underlying business page vanished.  Let the standard interruption
        # loop close it, then resume from whichever verified Lingxiao/world
        # page is actually revealed.  Forcing ``goto_view(34)`` here would
        # incorrectly require a #574→#34 navigation edge after the popup.
        revealed = yield from runtime.wait_view(
            34,
            LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID,
            LINGXIAO_COVER_SCENE_ID,
            LINGXIAO_MAIN_SCENE_ID,
            LINGXIAO_TASK_SCENE_ID,
            LINGXIAO_FULING_SCENE_ID,
            timeout=15.0,
            label="灵霄仙会：等待中断关闭后的业务页面",
        )
        scene_id = int(getattr(revealed, "id", revealed))
    if scene_id == LINGXIAO_GREEN_BOTTLE_WORLD_SCENE_ID:
        scene_id = yield from _normalize_lingxiao_world(
            runtime,
            label="灵霄仙会：从绿瓶世界页归一化",
        )
    if scene_id == LINGXIAO_FULING_SCENE_ID:
        # A prior interrupted run or a short AI read-only probe may leave the
        # shared game slot on #571.  Its own verified return lands at #34;
        # never wait for #571 to disappear or reuse #575 coordinates there.
        yield from runtime.wait_click(
            LINGXIAO_FULING_SCENE_ID,
            "返回",
            timeout=8.0,
            label="灵霄仙会：从残留仙门福令返回世界",
        )
        yield from _normalize_lingxiao_world(runtime, label="灵霄仙会：确认残留仙门福令返回世界")
        scene_id = 34
    if scene_id == 34:
        yield from runtime.wait_click(34, "灵霄仙会", timeout=8.0, label="灵霄仙会：进入活动")
        yield from runtime.wait_view(LINGXIAO_COVER_SCENE_ID, timeout=12.0, label="灵霄仙会：等待活动封面")
        scene_id = LINGXIAO_COVER_SCENE_ID
    if scene_id == LINGXIAO_COVER_SCENE_ID:
        yield from runtime.wait_click(LINGXIAO_COVER_SCENE_ID, "仙门寻宝", timeout=8.0, label="灵霄仙会：打开仙门寻宝")
        yield from runtime.wait_view(LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：等待寻宝主页")
    elif scene_id == LINGXIAO_TASK_SCENE_ID:
        # A previous attempt may have stopped on #570.  Rejoin its parent
        # explicitly instead of treating a task-page frame as the draw page.
        yield from runtime.wait_click(LINGXIAO_TASK_SCENE_ID, "切换仙门寻宝", timeout=8.0, label="灵霄仙会：从任务页回到寻宝")
        yield from runtime.wait_view(LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：确认寻宝主页")
    # Read every page-bound Runtime model before leaving #575.  In particular,
    # the cumulative ladder cannot be reconstructed safely once a different
    # activity has become the active Bothdraw instance on #34.
    snapshot = read_lingxiao_runtime_state()
    claimed_cumulative_reward_ids = yield from claim_lingxiao_cumulative_rewards(
        runtime, initial_snapshot=snapshot.get("cumulative")
    )
    # The claim above mutates the page-bound Revenue model.  Refresh the
    # joined snapshot before recording or deciding subsequent work.
    snapshot = read_lingxiao_runtime_state()
    # Scene-bound OCR deliberately excludes the dynamic time strip.  Read the
    # shared full-frame token result instead; only its numeric range is used.
    range_text = _activity_range_from_fragments(
        runtime.full_frame_ocr_tokens(runtime.cur_frame(update=True))
    )
    end = activity_end_from_text(range_text)
    _record_scatter_snapshot(snapshot, end)
    # OCR ticket labels are an alignment aid only.  They may be sampled by a
    # dedicated Runtime-GUI calibration routine, but never decide whether a
    # draw is legal: the two ticket pools and their total belong to Runtime.
    ten_draw_snapshot = read_bothdraw_ten_draw_runtime(
        expected_activity_id=LINGXIAO_ACTIVITY_ID,
        expected_x=snapshot.get("x"),
        expected_y=snapshot.get("y"),
    )
    decision = decide_lingxiao_draw(
        activity_id=int(snapshot.get("activity_id") or 0),
        runtime_draws=snapshot.get("available_draws"),
        ten_draw_enabled=(
            ten_draw_snapshot.get("ten_draw_enabled")
            if ten_draw_snapshot.get("complete")
            else None
        ),
    )
    # ``免费`` on the special-recharge tab is only copywriting.  The dedicated
    # reader joins the active #577 identity with SpecialOffer's synchronized
    # purchases and loaded package chain.  This observation is intentionally
    # before any future click branch exists.
    yield from runtime.wait_click(LINGXIAO_MAIN_SCENE_ID, "特惠连充（付费，仅观察）", timeout=8.0, label="灵霄仙会：打开特惠连充（只读）")
    yield from runtime.wait_view(LINGXIAO_SPECIAL_RECHARGE_SCENE_ID, timeout=12.0, label="灵霄仙会：等待特惠连充（只读）")
    special_recharge_snapshot = read_lingxiao_special_recharge_runtime()
    claimed_special_offer_id = yield from claim_lingxiao_special_recharge_first_free(
        runtime, initial_snapshot=special_recharge_snapshot
    )
    yield from runtime.wait_click(LINGXIAO_SPECIAL_RECHARGE_SCENE_ID, "返回", timeout=8.0, label="灵霄仙会：从特惠连充返回世界")
    yield from _normalize_lingxiao_world(runtime, label="灵霄仙会：确认连充页返回世界")
    # #577's verified return destination is #34.  Re-enter through the
    # activity cover before using any #575 shape; fixed screen coordinates on
    # the world page are not a valid substitute for scene ownership.
    yield from runtime.wait_click(34, "灵霄仙会", timeout=8.0, label="灵霄仙会：从连充重新进入活动")
    yield from runtime.wait_view(LINGXIAO_COVER_SCENE_ID, timeout=12.0, label="灵霄仙会：确认连充后活动封面")
    yield from runtime.wait_click(LINGXIAO_COVER_SCENE_ID, "仙门寻宝", timeout=8.0, label="灵霄仙会：从连充重新打开寻宝")
    # The entry can be normal ``#574→#575`` or can briefly show an optional
    # confirm popup that the guard closes back to #574.  Both are valid edges:
    # after the latter, retry the same verified #574 action once instead of
    # declaring the normal path wrong or clicking a #575 coordinate on #574.
    landing = yield from runtime.wait_view(LINGXIAO_MAIN_SCENE_ID, LINGXIAO_COVER_SCENE_ID, timeout=12.0, label="灵霄仙会：确认连充后寻宝主页或封面")
    landing_id = int(getattr(landing, "id", landing))
    if landing_id == LINGXIAO_COVER_SCENE_ID:
        yield from runtime.wait_click(LINGXIAO_COVER_SCENE_ID, "仙门寻宝", timeout=8.0, label="灵霄仙会：关闭可选弹窗后重试寻宝")
        yield from runtime.wait_view(LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：确认重试后寻宝主页")
    # #571 has a child activity identity distinct from its #575 parent.  The
    # dedicated reader validates that containment and only certifies its
    # observed no-reward state; a red dot never authorizes a click here.
    yield from runtime.wait_click(LINGXIAO_MAIN_SCENE_ID, "仙门福令", timeout=8.0, label="灵霄仙会：打开仙门福令")
    fuling_landing = yield from runtime.wait_view(LINGXIAO_FULING_SCENE_ID, LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：等待仙门福令或中断返回主页")
    if int(getattr(fuling_landing, "id", fuling_landing)) == LINGXIAO_MAIN_SCENE_ID:
        # A transient disconnect prompt can close back to the initiating
        # #575 page.  Retrying its verified entry once is safe; a second
        # unexpected #575 is still a real timeout rather than a click loop.
        yield from runtime.wait_click(LINGXIAO_MAIN_SCENE_ID, "仙门福令", timeout=8.0, label="灵霄仙会：中断关闭后重试仙门福令")
        yield from runtime.wait_view(LINGXIAO_FULING_SCENE_ID, timeout=12.0, label="灵霄仙会：确认重试后仙门福令")
    free_track_gui_state = read_lingxiao_free_track_gui_state(
        runtime.ocr_text_in_shapes(
            LINGXIAO_FULING_SCENE_ID,
            ("免费奖励",),
            frame_data_url=runtime.cur_frame(update=True),
            padding=8,
        )
    )
    if free_track_gui_state == "unactivated":
        # Static client evidence: this exact ``FreeBuyBtn`` sends only the
        # normal/free-track activate message.  Paid tracks own distinct
        # controls and are never touched here.  The label itself is both the
        # precondition and postcondition, so a stale red dot cannot authorize
        # a click or falsely report success.
        # This is a state-changing action.  It must be one resumable behavior-
        # tree primitive: a bare click followed by a yielded wait may replay
        # after a tick resumes while the server is still changing the panel.
        yield from runtime.wait_click(
            LINGXIAO_FULING_SCENE_ID,
            "免费奖励",
            timeout=8.0,
            label="灵霄仙会：激活免费福令",
        )
        # The server confirms this zero-cost activation with its own result
        # layer.  Do not OCR the dimmed #571 background or click through a
        # generic overlay: #581 is a real captured scene with the specific
        # "免费福令已激活" + continuation evidence.
        yield from runtime.wait_view(
            LINGXIAO_FULING_ACTIVATION_RESULT_SCENE_ID,
            timeout=12.0,
            label="灵霄仙会：确认免费福令激活结果",
        )
        yield from runtime.wait_click(
            LINGXIAO_FULING_ACTIVATION_RESULT_SCENE_ID,
            "继续",
            timeout=8.0,
            label="灵霄仙会：关闭免费福令激活结果",
        )
        yield from runtime.wait_view(
            LINGXIAO_FULING_SCENE_ID,
            timeout=12.0,
            label="灵霄仙会：确认回到仙门福令",
        )
        activated_state = read_lingxiao_free_track_gui_state(
            runtime.ocr_text_in_shapes(
                LINGXIAO_FULING_SCENE_ID,
                ("免费奖励",),
                frame_data_url=runtime.cur_frame(update=True),
                padding=8,
            )
        )
        if activated_state != "activated":
            raise RuntimeError(
                "灵霄仙会：免费轨激活后未由 GUI 标签确认「已激活」，停止后续领取"
            )
        free_track_gui_state = activated_state
    fuling_reward_snapshot = read_lingxiao_fuling_rewards_runtime()
    free_reward_action = "未发现当前已解锁的免费普通轨卡"
    free_target_ids = lingxiao_free_track_claim_target_ids(
        fuling_reward_snapshot, free_track_gui_state=free_track_gui_state
    )
    if free_target_ids:
        # Unlike a normal text button, this card becomes a green completed
        # state after claim.  Match it once before click so a completed card is
        # a legitimate no-op instead of a wait timeout or a repeated claim.
        visual_gate = runtime.shape_matches(
            LINGXIAO_FULING_SCENE_ID,
            "当前免费奖励（领取门卫）",
        )
        if visual_gate is None:
            free_reward_action = "免费普通轨首卡未呈现可领取视觉态"
        else:
            yield from runtime.wait_click(
                LINGXIAO_FULING_SCENE_ID,
                "当前免费奖励（领取门卫）",
                timeout=8.0,
                label="灵霄仙会：领取当前免费普通轨",
            )
            yield from runtime.wait_action_settle(2.0)
            fuling_reward_snapshot = read_lingxiao_fuling_rewards_runtime()
            after_track = fuling_reward_snapshot.get("normal_track_state")
            claimed_ids = {
                int(value)
                for value in (after_track.get("claimed_reward_ids") or [])
            } if isinstance(after_track, dict) and after_track.get("complete") else set()
            missing = sorted(set(free_target_ids) - claimed_ids)
            if missing:
                raise RuntimeError(
                    "灵霄仙会：免费普通轨批量领取后未确认全部目标已领："
                    f"{missing}"
                )
            free_reward_action = f"已领取免费普通轨 Runtime targets={list(free_target_ids)}"
    yield from runtime.wait_click(LINGXIAO_FULING_SCENE_ID, "返回", timeout=8.0, label="灵霄仙会：从仙门福令返回世界")
    yield from _normalize_lingxiao_world(runtime, label="灵霄仙会：确认福令页返回世界")
    # #571's verified return lands at #34 rather than #575.  Re-enter through
    # the established parent path instead of assuming an unobserved tab hop.
    yield from runtime.wait_click(34, "灵霄仙会", timeout=8.0, label="灵霄仙会：重新进入活动")
    yield from runtime.wait_view(LINGXIAO_COVER_SCENE_ID, timeout=12.0, label="灵霄仙会：确认活动封面")
    yield from runtime.wait_click(LINGXIAO_COVER_SCENE_ID, "仙门寻宝", timeout=8.0, label="灵霄仙会：重新打开仙门寻宝")
    yield from runtime.wait_view(LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：确认寻宝主页")
    # #570 owns the RevenueTask model.  Read its complete group membership
    # before considering any dynamic-row click; at this stage the workflow is
    # observation-only, so an unproven task result cannot consume a reward.
    yield from runtime.wait_click(LINGXIAO_MAIN_SCENE_ID, "福令任务", timeout=8.0, label="灵霄仙会：打开福令任务")
    task_landing = yield from runtime.wait_view(LINGXIAO_TASK_SCENE_ID, LINGXIAO_MAIN_SCENE_ID, timeout=12.0, label="灵霄仙会：等待福令任务或中断返回主页")
    if int(getattr(task_landing, "id", task_landing)) == LINGXIAO_MAIN_SCENE_ID:
        yield from runtime.wait_click(LINGXIAO_MAIN_SCENE_ID, "福令任务", timeout=8.0, label="灵霄仙会：中断关闭后重试福令任务")
        yield from runtime.wait_view(LINGXIAO_TASK_SCENE_ID, timeout=12.0, label="灵霄仙会：确认重试后福令任务")
    task_snapshot = read_lingxiao_fuling_tasks_runtime()
    claimed_daily_task_ids: list[int] = []
    # Runtime config positions are not UI positions.  Re-read the active
    # ScrollView after every claim because completed rows may move to its tail.
    for _attempt in range(4):
        if not task_snapshot.get("complete"):
            break
        if not any(
            isinstance(row, dict) and row.get("state") == "claimable"
            for row in ((task_snapshot.get("daily") or {}).get("tasks") or [])
        ):
            break
        ui_snapshot = read_lingxiao_daily_task_ui_runtime(
            expected_activity_id=LINGXIAO_ACTIVITY_ID
        )
        if not ui_snapshot.get("complete"):
            raise RuntimeError(f"灵霄仙会：福令日常 UI 顺序未同步：{ui_snapshot.get('reason')}")
        choice = choose_lingxiao_daily_claim(task_snapshot, ui_snapshot)
        if choice is None:
            break
        task_id_to_claim = choice["task_id"]
        row_title = f"日常任务第{choice['ui_index']}行（动态）"
        # A bare click is not resumable under the behavior-tree tick model:
        # use the framework's atomic click node so a pending Runtime read
        # cannot replay this reward action on the next tick.
        yield from runtime.wait_click(
            LINGXIAO_TASK_SCENE_ID,
            row_title,
            timeout=8.0,
            label=f"灵霄仙会：领取日常 task_id={task_id_to_claim}",
        )
        task_snapshot = read_lingxiao_fuling_tasks_runtime()
        after_rows = (task_snapshot.get("daily") or {}).get("tasks") or []
        after = next((row for row in after_rows if isinstance(row, dict) and int(row.get("task_id") or 0) == task_id_to_claim), None)
        if not isinstance(after, dict) or after.get("state") != "claimed":
            raise RuntimeError(f"灵霄仙会：日常 task_id={task_id_to_claim} 点击后未确认已领取")
        claimed_daily_task_ids.append(task_id_to_claim)
    # UI transition is asynchronous.  A bare click followed by an immediate
    # scene read can still observe the old task frame, so the world return is
    # always confirmed by its own wait.
    yield from runtime.wait_click(LINGXIAO_TASK_SCENE_ID, "返回", timeout=8.0, label="灵霄仙会：从任务页返回世界")
    yield from _normalize_lingxiao_world(runtime, label="灵霄仙会：确认回到世界")
    # Lingxiao is currently an AI research workflow, not a self-scheduling
    # production duty.  A stale Scheduler dispatch must therefore clear its
    # own trigger rather than silently recreate the next check after the user
    # cancelled it.  Re-arming requires a future explicit product decision.
    scheduler_dispatched = bool(payload.get("__scheduler_task_id"))
    if scheduler_dispatched:
        runner._persist_scheduler_task_next_time(task_id, None)
    cumulative = snapshot.get("cumulative")
    cumulative_state = (
        f"累计档位已同步（可领 {len(cumulative.get('visible_claimable') or [])} 项）"
        if isinstance(cumulative, dict) and cumulative.get("complete")
        else f"累计档位未同步：{snapshot.get('reason') or '未加载'}"
    )
    if task_snapshot.get("complete"):
        daily_rows = (task_snapshot.get("daily") or {}).get("tasks") or []
        daily_claimable_count = sum(
            isinstance(row, dict) and row.get("state") == "claimable"
            for row in daily_rows
        )
        task_state = f"福令日常已同步（可领 {daily_claimable_count} 项）"
    else:
        task_state = f"福令任务未同步：{task_snapshot.get('reason') or '未加载'}"
    if fuling_reward_snapshot.get("complete"):
        normal_track = fuling_reward_snapshot.get("normal_track_state")
        if isinstance(normal_track, dict) and normal_track.get("complete"):
            normal_claimable = sum(
                isinstance(row, dict) and row.get("is_box") is False
                and row.get("logical_left_mask_active") is True
                for row in (fuling_reward_snapshot.get("normal_items") or [])
            )
            reward_state = (
                f"福令普通轨已同步（逻辑可领 {normal_claimable} 项，"
                "尚未完成 GUI 坐标对齐，未执行领取）"
            )
        else:
            detail = normal_track.get("reason") if isinstance(normal_track, dict) else "普通轨状态未加载"
            if isinstance(normal_track, dict) and normal_track.get("activity_vo_fields"):
                detail = f"{detail}; activity_vo_fields={normal_track['activity_vo_fields']}"
            reward_state = (
                "福令免费轨奖励项尚未完成 Runtime-GUI 对齐，未执行领取："
                f"{detail}"
            )
    else:
        reward_state = f"福令免费轨未同步：{fuling_reward_snapshot.get('reason') or '未加载'}"
    activation_state = (
        "免费轨 GUI 已激活"
        if free_track_gui_state == "activated"
        else "免费轨 GUI 尚未激活（零成本激活待后续独立回读）"
        if free_track_gui_state == "unactivated"
        else "免费轨 GUI 状态未确认"
    )
    if special_recharge_snapshot.get("complete"):
        special_state = (
            f"特惠连充免费已领 offer_id={claimed_special_offer_id}"
            if claimed_special_offer_id is not None
            else f"特惠连充={special_recharge_snapshot.get('state')}"
        )
    else:
        special_state = f"特惠连充未同步：{special_recharge_snapshot.get('reason') or '未加载'}"
    claimed_daily_state = (
        f"本轮已领取日常 {claimed_daily_task_ids}；" if claimed_daily_task_ids else ""
    )
    completion_message = (
        f"灵霄仙会：{decision.reason}；{cumulative_state}；{task_state}；"
        f"{claimed_daily_state}{activation_state}；{free_reward_action}；"
        f"{reward_state}；{special_state}；"
        f"{'已清除遗留调度，不自动续期' if scheduler_dispatched else '研发手动运行：未改动调度'}"
    )
    runner._log("success", completion_message)
    return {"result": "success", "message": completion_message, "snapshot": snapshot, "task_snapshot": task_snapshot, "claimed_daily_task_ids": claimed_daily_task_ids, "claimed_cumulative_reward_ids": claimed_cumulative_reward_ids, "fuling_reward_snapshot": fuling_reward_snapshot, "free_track_gui_state": free_track_gui_state, "free_reward_action": free_reward_action, "special_recharge_snapshot": special_recharge_snapshot, "claimed_special_offer_id": claimed_special_offer_id, "decision": decision.action, "activity_range": range_text, "final_scene": 34}


__all__ = [
    "LINGXIAO_ACTIVITY_ID",
    "LINGXIAO_DRAW_CURRENCY_TYPE",
    "LINGXIAO_FULING_SCENE_ID",
    "LINGXIAO_FULING_ACTIVATION_RESULT_SCENE_ID",
    "LINGXIAO_COVER_SCENE_ID",
    "LINGXIAO_MAIN_SCENE_ID",
    "LINGXIAO_SPECIAL_RECHARGE_SCENE_ID",
    "LINGXIAO_SPECIAL_RECHARGE_RESULT_SCENE_ID",
    "build_lingxiao_ten_draw_observations",
    "claim_lingxiao_special_recharge_first_free",
    "lingxiao_free_track_claim_target_ids",
    "LINGXIAO_TASK_SCENE_ID",
    "LINGXIAO_XIANHUI_TASK_ID",
    "LingxiaoDrawDecision",
    "LingxiaoSpecialRechargeDecision",
    "activity_end_from_text",
    "claim_lingxiao_cumulative_rewards",
    "decide_lingxiao_draw",
    "decide_lingxiao_special_recharge",
    "execute_lingxiao_xianhui_job",
    "next_lingxiao_check_time",
    "read_lingxiao_gui_ticket_draws",
    "read_lingxiao_gui_ticket_draws_from_tokens",
    "read_lingxiao_cumulative_rewards_runtime",
    "read_lingxiao_fuling_tasks_runtime",
    "read_lingxiao_free_track_gui_state",
    "should_try_lingxiao_free_track_claim",
    "read_lingxiao_fuling_rewards_runtime",
    "read_lingxiao_special_recharge_runtime",
    "read_lingxiao_runtime_state",
]
