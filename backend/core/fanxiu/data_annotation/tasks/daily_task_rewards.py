from __future__ import annotations

"""Idempotent aggregate workflow for daily activity task rewards."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import threading
from typing import Any, Protocol

from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    read_all_activity_task_reward_snapshots,
    read_activity_task_reward_fast_snapshot,
    read_activity_task_reward_snapshot,
)
from backend.core.fanxiu.data_annotation.tasks.daily_task_reward_navigation import (
    navigate_to_daily_task_reward_cover,
)


DAILY_TASK_REWARD_TRIGGER = (6, 30)
DAILY_TASK_REWARD_DOMAIN_ORDER = ("lundao", "qixi_mojie", "lingmai")


class TaskRewardGuiAdapter(Protocol):
    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]: ...


TaskRewardReader = Callable[[str], dict[str, Any]]
FastTaskRewardReader = Callable[..., dict[str, Any]]


DAILY_TASK_REWARD_GUI = {
    "lundao": {
        "cover_scene_ids": (296, 549),
        "entry_shape": "任务",
        "task_scene_id": 550,
        "click_point": (560.0, 365.0),
    },
    "qixi_mojie": {
        "cover_scene_ids": (319,),
        "entry_shape": "联盟任务",
        "task_scene_id": 551,
        "click_point": (470.0, 245.0),
    },
    "lingmai": {
        "cover_scene_ids": (285,),
        "entry_shape": "任务",
        # #552 is deliberately required.  Until that formal asset exists the
        # wait fails closed; this workflow never substitutes a guessed frame.
        "task_scene_id": 552,
        # Real #552 first-row body; the reward icon child sits farther right.
        "click_point": (470.0, 245.0),
    },
}


def claim_first_row_until_clear(
    *,
    domain: str,
    scene_id: int,
    runtime: Any,
    reader: FastTaskRewardReader,
    initial_snapshot: dict[str, Any] | None = None,
    click_x: float = 560.0,
    click_y: float = 365.0,
    max_claims: int = 24,
) -> dict[str, Any]:
    """Claim a removing first-row task list with strict Runtime verification.

    Activity ``RankTaskItem`` removes a row after a successful claim, so the
    next authorized task moves into the same safe body coordinate.  The reward
    icon is deliberately excluded because its child click opens item details.
    Every click must move exactly the current first authorized task into the
    claimed set and leave the remaining authorization list unchanged in order.
    """

    claimed_now: list[int] = []
    before = initial_snapshot
    for _index in range(max(1, int(max_claims))):
        if before is None:
            before = reader(domain)
        status, reason = _domain_decision(before, adapter_available=True)
        if status in {"already_claimed", "nothing_claimable"}:
            return {"ok": True, "claimed_task_ids": claimed_now, "reason": reason}
        if status != "ready":
            return {"ok": False, "claimed_task_ids": claimed_now, "reason": reason}
        authorized = list(before["authorized_claim_task_ids"])
        expected = int(authorized[0])
        runtime.click_frame_point(scene_id, click_x, click_y)
        yield from runtime.wait_action_settle(1.2)
        after = reader(domain, expected_claimed_task_id=expected)
        claimed_after = set(after.get("claimed_task_ids") or [])
        remaining_after = list(after.get("authorized_claim_task_ids") or [])
        expected_flag = after.get("expected_task_claimed")
        if (
            not after.get("ok")
            or not after.get("available")
            or not after.get("complete")
            or expected not in claimed_after
            or expected_flag is not True
            or remaining_after != authorized[1:]
        ):
            return {
                "ok": False,
                "claimed_task_ids": claimed_now,
                "reason": f"taskId={expected} 点击后未形成精确单步状态迁移",
            }
        claimed_now.append(expected)
        before = after
    return {
        "ok": False,
        "claimed_task_ids": claimed_now,
        "reason": f"达到单域领取上限 {max_claims} 后仍有可领取任务",
    }


def next_daily_task_reward_time(now: datetime | None = None) -> datetime:
    """Return the next day's 06:30; this job never installs another trigger."""

    current = now or datetime.now()
    tomorrow = current + timedelta(days=1)
    return tomorrow.replace(
        hour=DAILY_TASK_REWARD_TRIGGER[0],
        minute=DAILY_TASK_REWARD_TRIGGER[1],
        second=0,
        microsecond=0,
    )


def _domain_decision(snapshot: dict[str, Any], *, adapter_available: bool) -> tuple[str, str]:
    if not snapshot.get("available") or not snapshot.get("ok"):
        return "fail_closed", str(snapshot.get("reason") or "活动任务事实不可用")
    if not snapshot.get("complete") or snapshot.get("state") == "ambiguous":
        return "fail_closed", "活动任务事实不完整，拒绝领取"

    authorized = snapshot.get("authorized_claim_task_ids")
    if not isinstance(authorized, list):
        return "fail_closed", "缺少严格授权任务列表"
    if not authorized:
        if snapshot.get("state") == "already_claimed":
            return "already_claimed", "全部奖励已经领取"
        return "nothing_claimable", "当前没有可领取档位"
    if not adapter_available:
        return "pending_research", "尚无真实 GUI 页面与领取 Shape 证据"
    return "ready", "可由已验收 GUI 适配器领取"


def _run_domain(
    domain: str,
    *,
    reader: TaskRewardReader,
    adapter: TaskRewardGuiAdapter | None,
) -> dict[str, Any]:
    try:
        before = reader(domain)
    except Exception as exc:
        return {"domain": domain, "status": "fail_closed", "reason": f"reader异常：{exc}"}

    status, reason = _domain_decision(before, adapter_available=adapter is not None)
    result: dict[str, Any] = {
        "domain": domain,
        "status": status,
        "reason": reason,
        "before_state": before.get("state"),
        "claimable_task_ids": list(before.get("authorized_claim_task_ids") or []),
    }
    if status != "ready":
        return result

    intended = set(result["claimable_task_ids"])
    try:
        adapter_result = adapter(before) if adapter is not None else {}
    except Exception as exc:
        return {**result, "status": "failed", "reason": f"GUI适配器异常：{exc}"}
    if not isinstance(adapter_result, dict) or not adapter_result.get("ok"):
        adapter_reason = (
            str(adapter_result.get("reason") or "GUI适配器未报告成功")
            if isinstance(adapter_result, dict)
            else "GUI适配器返回结构无效"
        )
        return {
            **result,
            "status": "failed",
            "reason": adapter_reason,
        }

    # A successful click is not completion evidence. Re-read QuestMgr and
    # require every originally authorized task to move into the claimed set.
    try:
        after = reader(domain)
    except Exception as exc:
        return {**result, "status": "unverified", "reason": f"领取后复读异常：{exc}"}
    claimed_after = set(after.get("claimed_task_ids") or [])
    verified = bool(
        after.get("ok")
        and after.get("available")
        and after.get("complete")
        and intended.issubset(claimed_after)
    )
    if not verified:
        return {
            **result,
            "status": "unverified",
            "reason": "领取后 QuestMgr 未确认原授权任务全部已领取",
            "after_state": after.get("state"),
        }
    return {
        **result,
        "status": "claimed",
        "reason": "领取后 QuestMgr 已确认",
        "after_state": after.get("state"),
        "claimed_task_ids": sorted(intended),
    }


def run_daily_task_rewards_job(
    *,
    reader: TaskRewardReader | None = None,
    gui_adapters: Mapping[str, TaskRewardGuiAdapter] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run isolated domain reward handlers without guessing missing GUI routes."""

    adapters = dict(gui_adapters or {})
    if reader is None:
        # All three domains live in the same QuestMgr activity-task table.
        # Decode it once for the initial authorization boundary; only a domain
        # that actually clicks rewards performs its own post-action re-read.
        batch = read_all_activity_task_reward_snapshots()
        initial = {
            domain: dict((batch.get("domains") or {}).get(domain) or {})
            for domain in DAILY_TASK_REWARD_DOMAIN_ORDER
        }
        initial_pending = set(initial)

        def shared_reader(domain: str) -> dict[str, Any]:
            if domain in initial_pending:
                initial_pending.remove(domain)
                return initial[domain]
            return read_activity_task_reward_snapshot(domain)

        effective_reader = shared_reader
    else:
        effective_reader = reader
    domains = [
        _run_domain(domain, reader=effective_reader, adapter=adapters.get(domain))
        for domain in DAILY_TASK_REWARD_DOMAIN_ORDER
    ]
    # 洞天未取收益已有每天 05:00 自动邮件证据，不占用 UI，也不进入
    # QuestMgr 领取链。未来若事实变化，应新增独立证据而不是猜一个入口。
    domains.append(
        {
            "domain": "dongtian",
            "status": "skipped_auto_mail",
            "reason": "05:00 未取之宝由邮件自动发放，当前不接 UI",
        }
    )
    next_time = next_daily_task_reward_time(now)
    return {
        "job": "日常_任务奖励",
        "status": "completed_with_pending"
        if any(item["status"] in {"pending_research", "fail_closed", "failed", "unverified"} for item in domains)
        else "completed",
        "domains": domains,
        "next_time": next_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


class DailyTaskRewardsTaskMixin:
    """Scheduler integration for the three isolated reward domains."""

    def _execute_daily_task_rewards_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_task_rewards",
            label="日常_任务奖励",
            flow=self.日常任务奖励流程,
        )

    def 日常任务奖励流程(self, runtime: Any):
        stop_event = runtime.stop_event or threading.Event()
        payload = runtime.payload
        batch = read_all_activity_task_reward_snapshots()
        initial = batch.get("domains") if isinstance(batch, dict) else None
        if not isinstance(initial, dict):
            raise RuntimeError("日常_任务奖励：QuestMgr 初始三域批量事实不可用")

        domain_results: list[dict[str, Any]] = []
        any_ui_attempted = False
        for domain in DAILY_TASK_REWARD_DOMAIN_ORDER:
            snapshot = dict(initial.get(domain) or {})
            status, reason = _domain_decision(snapshot, adapter_available=True)
            row: dict[str, Any] = {
                "domain": domain,
                "status": status,
                "reason": reason,
                "claimable_task_ids": list(snapshot.get("authorized_claim_task_ids") or []),
            }
            if status in {"already_claimed", "nothing_claimable"}:
                domain_results.append(row)
                continue
            if status != "ready":
                domain_results.append(row)
                continue

            gui = DAILY_TASK_REWARD_GUI[domain]
            ui_attempted = False
            try:
                ui_attempted = True
                any_ui_attempted = True
                navigation = yield from navigate_to_daily_task_reward_cover(
                    self,
                    runtime.ctx,
                    stop_event,
                    payload,
                    runtime,
                    domain,
                )
                cover_scene_id = int(navigation["scene_id"])
                if cover_scene_id not in gui["cover_scene_ids"]:
                    raise RuntimeError(
                        f"{domain} 奖励入口落在未授权封面 #{cover_scene_id}"
                    )
                yield from runtime.wait_click_then_view(
                    cover_scene_id,
                    gui["entry_shape"],
                    [gui["task_scene_id"]],
                    settle_seconds=1.2,
                    timeout=25.0,
                )
                click_x, click_y = gui["click_point"]
                claim_result = yield from claim_first_row_until_clear(
                    domain=domain,
                    scene_id=gui["task_scene_id"],
                    runtime=runtime,
                    reader=read_activity_task_reward_fast_snapshot,
                    initial_snapshot=snapshot,
                    click_x=click_x,
                    click_y=click_y,
                )
                if not claim_result.get("ok"):
                    raise RuntimeError(str(claim_result.get("reason") or "领取状态迁移未确认"))
                row.update(
                    status="claimed",
                    reason="QuestMgr 逐次确认领取完成",
                    claimed_task_ids=list(claim_result.get("claimed_task_ids") or []),
                )
            except (InterruptedError, GeneratorExit):
                raise
            except Exception as exc:
                row.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
            finally:
                if ui_attempted:
                    try:
                        yield from runtime.goto_view(34)
                    except (InterruptedError, GeneratorExit):
                        raise
                    except Exception as exc:
                        row["departure_warning"] = f"未安全回到 #34：{type(exc).__name__}: {exc}"
                        if row.get("status") == "claimed":
                            row.update(status="failed", reason=row["departure_warning"])
            domain_results.append(row)

        domain_results.append(
            {
                "domain": "dongtian",
                "status": "skipped_auto_mail",
                "reason": "05:00 未取之宝由邮件自动发放，当前不接 UI",
            }
        )
        incomplete = [
            row
            for row in domain_results
            if row["status"]
            not in {"already_claimed", "nothing_claimable", "claimed", "skipped_auto_mail"}
        ]
        if incomplete:
            summary = "；".join(
                f"{row['domain']}={row['status']}({row['reason']})" for row in incomplete
            )
            raise RuntimeError(f"日常_任务奖励部分域未完成：{summary}")

        next_time = next_daily_task_reward_time().strftime("%Y-%m-%d %H:%M:%S")
        runtime.set_next_time(next_time)
        claimed_count = sum(len(row.get("claimed_task_ids") or []) for row in domain_results)
        runtime.set_completion_message(
            f"日常_任务奖励：三域幂等完成，本次领取 {claimed_count} 项；"
            "洞天05:00未取收益由邮件自动承接"
        )
        return {
            # A fully idempotent run deliberately performs no GUI read, so it
            # must not fabricate #34 as a visual fact in Runtime status.
            "current_scene": 34 if any_ui_attempted else None,
            "domains": domain_results,
        }
