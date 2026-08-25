from __future__ import annotations

"""Shared draw/claim workflow for Bothdraw-based activities."""

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.core.fanxiu.data_annotation.tasks.draw_claim_cycle import (
    run_draw_claim_cycle,
)
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    build_draw_before_observation,
    build_paired_draw_observations,
)


SnapshotReader = Callable[[], dict[str, Any]]
PageOpener = Callable[[Any], Any]
PageReader = Callable[[Any], Any]
SnapshotRecorder = Callable[[dict[str, Any], str], None]
InstanceIdResolver = Callable[[str], str]


@dataclass(frozen=True)
class BothdrawLotterySpec:
    activity_label: str
    main_scene_id: int
    draw_shape: str
    cumulative_reward_shape: str
    cumulative_reward_slots: int
    draw_result_scene_id: int | None
    draw_result_close_shape: str
    main_page_name: str
    open_main_page: PageOpener
    read_page: PageReader
    read_lottery: SnapshotReader
    read_cumulative_rewards: SnapshotReader
    resolve_instance_id: InstanceIdResolver
    record_snapshot: SnapshotRecorder
    # A legacy panel is a single horizontal row.  Activities with a verified
    # multi-row reward grid must declare every center explicitly instead of
    # reusing that visual assumption for ``visible_slot`` values.
    cumulative_reward_slot_centers: tuple[tuple[float, float], ...] = ()

    def require_executable_assets(self) -> None:
        """Require the result-page proof needed for a draw action."""

        if not self.draw_result_scene_id:
            raise RuntimeError(
                f"{self.activity_label}抽奖结果页尚无独立且已验收的场景资产，拒绝开始抽奖"
            )

    def require_cumulative_claim_assets(self) -> None:
        """Require only the independent assets needed to claim milestones.

        A cumulative reward may be claimable from a prior draw, a free grant or
        an externally completed task.  Its click and Runtime read-back do not
        depend on this activity having a captured draw-result page, so that
        page must not accidentally turn an otherwise safe free claim into a
        no-op.
        """

        if not str(self.cumulative_reward_shape or "").strip():
            raise RuntimeError(f"{self.activity_label}累计奖励容器尚无已验收动作资产")
        if int(self.cumulative_reward_slots) <= 0:
            raise RuntimeError(f"{self.activity_label}累计奖励槽位数无效")
        if self.cumulative_reward_slot_centers and (
            len(self.cumulative_reward_slot_centers)
            != int(self.cumulative_reward_slots)
        ):
            raise RuntimeError(
                f"{self.activity_label}累计奖励槽位中心数量与 Runtime 槽位数不一致"
            )

    def cumulative_reward_slot_center(self, slot: int) -> tuple[float, float]:
        """Return a normalized center inside ``cumulative_reward_shape``.

        ``visible_slot`` is a Runtime-to-GUI index, not a proof that all
        activity panels are one row.  Preserve the established legacy layout
        only when no activity-specific grid has been observed.
        """

        slot = int(slot)
        if slot < 1 or slot > self.cumulative_reward_slots:
            raise RuntimeError(f"累计奖励可领取槽位异常：{slot}")
        if self.cumulative_reward_slot_centers:
            if len(self.cumulative_reward_slot_centers) != self.cumulative_reward_slots:
                raise RuntimeError(
                    f"{self.activity_label}累计奖励槽位中心数量与 Runtime 槽位数不一致"
                )
            x_ratio, y_ratio = self.cumulative_reward_slot_centers[slot - 1]
            if not (0.0 < x_ratio < 1.0 and 0.0 < y_ratio < 1.0):
                raise RuntimeError(
                    f"{self.activity_label}累计奖励槽位中心超出容器：slot={slot}"
                )
            return float(x_ratio), float(y_ratio)
        return (slot - 0.5) / self.cumulative_reward_slots, 0.30


def draw_bothdraw_once(
    runtime: Any,
    spec: BothdrawLotterySpec,
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 0.5,
    requested_batch_size: int | None = None,
) -> dict[str, Any]:
    """Draw exactly once and persist only the observed cumulative delta."""

    spec.require_executable_assets()
    spec.open_main_page(runtime)
    before = spec.read_lottery()
    if not before.get("complete"):
        raise RuntimeError(str(before.get("reason") or "抽奖前运行态数据不完整"))
    before_resources = spec.read_cumulative_rewards()
    action_id = uuid.uuid4().hex
    before = _merge_draw_observation(
        before,
        before_resources,
        observation_kind="before_draw",
        action_id=action_id,
    )
    requested = int(
        requested_batch_size
        if requested_batch_size is not None
        else (10 if int(before.get("available_draws") or 0) >= 10 else 1)
    )
    draw_mode = "ten_draw" if requested == 10 else "single_draw"
    before = build_draw_before_observation(
        before,
        action_id=action_id,
        draw_mode=draw_mode,
        requested_batch_size=requested,
    )
    instance_id = spec.resolve_instance_id(str(before["captured_at"]))
    spec.record_snapshot(before, instance_id)

    frame = runtime.cur_frame(update=True)
    runtime.click_shape(spec.main_scene_id, spec.draw_shape, frame_data_url=frame)

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last: dict[str, Any] | None = None
    while True:
        last = spec.read_lottery()
        if last.get("complete") and int(last.get("x") or 0) > int(before["x"]):
            break
        if time.monotonic() >= deadline:
            reason = str((last or {}).get("reason") or "累计抽数未增加")
            raise RuntimeError(f"点击{spec.draw_shape}后未取得新抽奖点：{reason}")
        time.sleep(max(0.05, float(poll_seconds)))

    after = last
    assert after is not None
    if int(after.get("activity_id") or 0) != int(before.get("activity_id") or 0):
        raise RuntimeError("抽奖前后活动实例发生变化，拒绝记录")
    before_reward = before.get("selected_big_reward") or {}
    after_reward = after.get("selected_big_reward") or {}
    if int(after_reward.get("library_id") or 0) != int(before_reward.get("library_id") or 0):
        raise RuntimeError("抽奖前后已选大奖发生变化，拒绝记录")
    dx = int(after["x"]) - int(before["x"])
    dy = int(after["y"]) - int(before["y"])
    if dx <= 0 or dx > 10 or dy < 0 or dy > dx:
        raise RuntimeError(f"抽奖增量超出安全范围：dx={dx}, dy={dy}")
    after_resources = spec.read_cumulative_rewards()
    after = _merge_draw_observation(
        after,
        after_resources,
        observation_kind="after_draw",
        action_id=action_id,
        batch_size=dx,
        draw_mode="ten_draw" if dx == 10 else "single_draw" if dx == 1 else "unknown_batch",
        before_observation=before,
    )
    _paired_before, after = build_paired_draw_observations(
        before,
        after,
        action_id=action_id,
        draw_mode=draw_mode,
        requested_batch_size=requested,
    )
    spec.record_snapshot(after, instance_id)
    return {
        "result": "success",
        "instance_id": instance_id,
        "before": {"x": int(before["x"]), "y": int(before["y"])},
        "after": {"x": int(after["x"]), "y": int(after["y"])},
        "dx": dx,
        "dy": dy,
        "selected_big_reward": dict(after_reward),
        "draw_mode": str(after["draw_mode"]),
        "available_currency_before": before.get("available_currency"),
        "available_currency_after": after.get("available_currency"),
        "progress_before": before.get("progress"),
        "progress_after": after.get("progress"),
    }


def _merge_draw_observation(
    lottery: dict[str, Any],
    resources: dict[str, Any],
    *,
    observation_kind: str,
    action_id: str,
    batch_size: int | None = None,
    draw_mode: str | None = None,
    before_observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join probability and wallet/milestone facts from one activity state."""

    if not resources.get("complete"):
        raise RuntimeError(str(resources.get("reason") or "抽奖资源运行态数据不完整"))
    lottery_activity = int(lottery.get("activity_id") or 0)
    resource_activity = int(resources.get("activity_id") or 0)
    lottery_x = int(lottery.get("x") or 0)
    resource_x = int(resources.get("x") or 0)
    if lottery_activity != resource_activity or lottery_x != resource_x:
        raise RuntimeError(
            "抽奖概率点与资源点不属于同一状态："
            f"activity={lottery_activity}/{resource_activity}, x={lottery_x}/{resource_x}"
        )
    merged = {
        **lottery,
        "observation_kind": observation_kind,
        "action_phase": observation_kind,
        "action_id": action_id,
        "batch_size": batch_size,
        "draw_mode": draw_mode,
        "available_currency": int(resources.get("available_currency") or 0),
        "available_draws": int(resources.get("available_draws") or 0),
        "cost_type": int(resources.get("cost_type") or 0),
        "cost_per_draw": int(resources.get("cost_per_draw") or 0),
        "progress": int(resources.get("progress") or 0),
        "claimed_count": int(resources.get("claimed_count") or 0),
        "claimed_ids": [int(value) for value in resources.get("claimed_ids") or []],
    }
    if before_observation is not None:
        merged.update(
            {
                "available_currency_before": before_observation.get(
                    "available_currency"
                ),
                "available_currency_after": merged["available_currency"],
                "available_draws_before": before_observation.get("available_draws"),
                "available_draws_after": merged["available_draws"],
            }
        )
    return merged


def claim_bothdraw_cumulative_rewards(
    runtime: Any,
    spec: BothdrawLotterySpec,
    *,
    timeout_seconds: float = 15.0,
    poll_seconds: float = 0.5,
    max_clicks: int = 16,
) -> dict[str, Any]:
    """Claim only milestones proven claimable by the read-only runtime model."""

    spec.require_cumulative_claim_assets()
    spec.open_main_page(runtime)
    clicks: list[dict[str, int]] = []
    activity_id: int | None = None
    for _attempt in range(max(1, int(max_clicks))):
        before = spec.read_cumulative_rewards()
        if not before.get("complete"):
            raise RuntimeError(str(before.get("reason") or "累抽奖励运行态数据不完整"))
        current_activity_id = int(before.get("activity_id") or 0)
        if activity_id is None:
            activity_id = current_activity_id
        elif current_activity_id != activity_id:
            raise RuntimeError(f"领取过程中{spec.activity_label}活动实例发生变化")
        visible = list(before.get("visible_claimable") or [])
        if not visible:
            if before.get("claimable"):
                raise RuntimeError("存在可领取累计奖励，但当前可见槽位没有对应档位")
            return {
                "result": "success",
                "activity_id": activity_id,
                "progress": int(before.get("progress") or 0),
                "claimed_count": int(before.get("claimed_count") or 0),
                "available_draws": int(before.get("available_draws") or 0),
                "clicked_count": len(clicks),
                "clicks": clicks,
                "stop_reason": "all_current_rewards_claimed",
            }

        target = visible[0]
        slot = int(target.get("visible_slot") or 0)
        x_ratio, y_ratio = spec.cumulative_reward_slot_center(slot)
        before_count = int(before.get("claimed_count") or 0)
        claim_action_id = uuid.uuid4().hex
        claim_instance_id = _record_claim_observation_if_supported(
            spec,
            before,
            action_id=claim_action_id,
            action_phase="before_claim",
        )
        runtime.click_shape_center(
            spec.main_scene_id,
            spec.cumulative_reward_shape,
            x_ratio=x_ratio,
            y_ratio=y_ratio,
        )

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = spec.read_cumulative_rewards()
            if (
                last.get("complete")
                and int(last.get("activity_id") or 0) == activity_id
                and int(last.get("claimed_count") or 0) > before_count
            ):
                break
            time.sleep(max(0.05, float(poll_seconds)))
        else:
            reason = str((last or {}).get("reason") or "已领取数量没有增加")
            raise RuntimeError(f"点击累计奖励后动态状态未确认领取成功：{reason}")

        assert last is not None
        if claim_instance_id is not None:
            after_claim = {
                **last,
                "observation_kind": "after_claim",
                "action_phase": "after_claim",
                "action_id": claim_action_id,
            }
            spec.record_snapshot(after_claim, claim_instance_id)
        clicks.append(
            {
                "reward_id": int(target.get("id") or 0),
                "threshold": int(target.get("threshold") or 0),
                "slot": slot,
                "claimed_before": before_count,
                "claimed_after": int(last.get("claimed_count") or 0),
            }
        )
    raise RuntimeError(f"累计奖励领取超过安全点击上限：{max_clicks}")


def _record_claim_observation_if_supported(
    spec: BothdrawLotterySpec,
    snapshot: dict[str, Any],
    *,
    action_id: str,
    action_phase: str,
) -> str | None:
    """Persist claim phases when the runtime supplies the joined lottery state.

    Older test fixtures and saved resource snapshots predate the selected-prize
    fields.  They remain readable, but only a current joined runtime snapshot is
    eligible to become a new action observation.
    """

    captured_at = str(snapshot.get("captured_at") or "")
    if not captured_at or not isinstance(snapshot.get("selected_big_reward"), dict):
        return None
    instance_id = spec.resolve_instance_id(captured_at)
    spec.record_snapshot(
        {
            **snapshot,
            "observation_kind": action_phase,
            "action_phase": action_phase,
            "action_id": action_id,
        },
        instance_id,
    )
    return instance_id


def close_bothdraw_result(
    runtime: Any,
    spec: BothdrawLotterySpec,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.25,
    retry_click_seconds: float = 2.0,
    max_clicks: int = 3,
) -> dict[str, Any]:
    """Close a verified result page with bounded fresh-frame retries.

    Result animations may consume an early tap.  Every retry first proves that
    the current fresh frame is still the same reliable result scene; once the
    main page appears no further result-page click is possible.
    """

    spec.require_executable_assets()
    result_scene_id = int(spec.draw_result_scene_id or 0)
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    clicked_count = 0
    last_click_at: float | None = None
    while time.monotonic() < deadline:
        last_scene, last_score, frame = runtime.current_scene(
            [result_scene_id], update=True
        )
        if int(last_scene or 0) == result_scene_id and float(last_score or 0) >= 90.0:
            now = time.monotonic()
            if (
                clicked_count < max(1, int(max_clicks))
                and (
                    last_click_at is None
                    or now - last_click_at >= max(0.25, float(retry_click_seconds))
                )
            ):
                runtime.click_shape(
                    result_scene_id,
                    spec.draw_result_close_shape,
                    frame_data_url=frame,
                )
                clicked_count += 1
                last_click_at = now
        page = spec.read_page(runtime)
        if page is not None and page.page == spec.main_page_name:
            return {
                "result": "success",
                "page": page.page,
                "scene_id": page.scene_id,
                "score": page.score,
                "clicked_count": clicked_count,
            }
        time.sleep(max(0.05, float(poll_seconds)))
    if clicked_count == 0:
        raise RuntimeError(
            f"{spec.draw_shape}后未可靠识别 #{result_scene_id}"
            f"[{spec.draw_result_close_shape}]：scene={last_scene}, score={last_score:.1f}"
        )
    raise RuntimeError(
        f"#{result_scene_id}[{spec.draw_result_close_shape}] 点击 {clicked_count} 次后"
        f"未回到{spec.activity_label}主页"
    )


def complete_bothdraw_lottery(
    runtime: Any,
    spec: BothdrawLotterySpec,
    *,
    max_rounds: int = 256,
) -> dict[str, Any]:
    """Run the bounded draw/claim fixed-point loop."""

    # Fail before navigation or any consumptive action when a new activity has
    # not yet supplied its independently verified result-page asset.
    spec.require_executable_assets()
    spec.open_main_page(runtime)
    return run_draw_claim_cycle(
        read_snapshot=spec.read_cumulative_rewards,
        draw_once=lambda: draw_bothdraw_once(runtime, spec),
        close_draw_result=lambda: close_bothdraw_result(runtime, spec),
        claim_rewards=lambda: claim_bothdraw_cumulative_rewards(runtime, spec),
        max_rounds=max_rounds,
    )


__all__ = [
    "BothdrawLotterySpec",
    "claim_bothdraw_cumulative_rewards",
    "close_bothdraw_result",
    "complete_bothdraw_lottery",
    "draw_bothdraw_once",
]
