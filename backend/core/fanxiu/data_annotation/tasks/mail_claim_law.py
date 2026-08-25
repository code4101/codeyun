from __future__ import annotations

"""Closed loop for a law attached to a mail reward.

The duration is intentionally never inferred from a cross number or a fixed
number of days.  The only scheduling fact is the live item's ``end_time``.
"""

import time
from datetime import datetime
from typing import Any, Iterable

from backend.core.fanxiu.behavior_tree.runtime import fanxiu_data_annotation_world_facts_path
from backend.core.fanxiu.data_annotation.state import (
    read_data_annotation_world_facts,
    write_data_annotation_world_facts,
)
from backend.core.fanxiu.instrumentation.backpack_ui import read_backpack_ui_snapshot
from backend.core.fanxiu.mail.policy import fanxiu_mail_rewards_from_payload
from backend.core.fanxiu.mail.runtime_store import current_runtime_mail_sequence_snapshot
from backend.core.fanxiu.runtime_gui import (
    StorageBagGrid,
    plan_storage_bag_item_click,
    plan_storage_bag_scroll,
    register_storage_bag_viewport_from_quantity_ocr,
    verify_storage_bag_item_detail,
    visible_storage_bag_cells,
    quantity_observations_from_ocr,
)

MAIL_CLAIM_LAW_TASK_ID = "mail-claim-law"


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _law_rewards(mail: dict[str, Any]) -> list[dict[str, Any]]:
    payload = mail.get("payload") if isinstance(mail.get("payload"), dict) else mail
    rewards = fanxiu_mail_rewards_from_payload(payload)
    if not rewards and isinstance(payload.get("rewards"), list):
        rewards = [item for item in payload["rewards"] if isinstance(item, dict)]
    return [
        reward for reward in rewards
        if isinstance(reward, dict)
        and (str(reward.get("item_type") or "") == "法则" or _as_int(reward.get("extra_mark")) == 7)
        and _as_int(reward.get("item_id") or reward.get("base_id")) is not None
    ]


def select_oldest_claimable_law_mail(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Select exactly one oldest eligible mail and preserve its concrete reward."""

    candidates: list[dict[str, Any]] = []
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        if not (
            str(item.get("runtime_status") or "") == "unclaimed"
            and bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and str(item.get("action_policy") or "") == "claim"
        ):
            continue
        rewards = _law_rewards(item)
        if len(rewards) != 1:
            continue
        reward = rewards[0]
        candidates.append({
            "mail_id": str(item.get("id") or item.get("mail_id") or ""),
            "mail_key": str(item.get("mail_key") or ""),
            "title": str(item.get("title") or ""),
            "create_time_ms": _as_int(item.get("create_time_ms")) or 0,
            "base_id": _as_int(reward.get("item_id") or reward.get("base_id")),
            "name": str(reward.get("item_name") or reward.get("name") or ""),
        })
    candidates = [item for item in candidates if item["mail_id"] and item["base_id"] is not None and item["name"]]
    return min(candidates, key=lambda item: (int(item["create_time_ms"]), str(item["mail_id"]))) if candidates else None


def active_law_end_time(snapshot: dict[str, Any], *, now_ms: int | None = None) -> dict[str, Any] | None:
    """A live future ``end_time`` is the authoritative active-law fact.

    The backpack panel exposes this field on the activated law instance.  More
    than one live timer is an ambiguity, not permission to guess.
    """

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    active = [item for item in snapshot.get("items") or [] if isinstance(item, dict) and (_as_int(item.get("end_time")) or 0) > now]
    if len(active) != 1:
        return None
    item = active[0]
    return {"instance_id": str(item.get("instance_id") or ""), "base_id": _as_int(item.get("base_id")), "end_time_ms": _as_int(item.get("end_time"))}


def law_next_time(end_time_ms: int) -> str:
    return datetime.fromtimestamp(int(end_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")


class MailClaimLawTaskMixin:
    def _remembered_law(self) -> dict[str, Any] | None:
        facts = read_data_annotation_world_facts(fanxiu_data_annotation_world_facts_path())
        fact = ((facts.get("discoveries") or {}).get("task") or {}).get(MAIL_CLAIM_LAW_TASK_ID)
        end_time_ms = _as_int((fact or {}).get("end_time_ms")) if isinstance(fact, dict) else None
        return dict(fact) if end_time_ms and end_time_ms > int(time.time() * 1000) else None

    def _open_storage_bag(self, runtime: Any):
        """#525 has no graph edge: use the named #34 entry, then verify #525."""
        yield from runtime.wait_click(34, "储物袋", timeout=8.0, label="邮件_领法则：进入储物袋")
        yield from runtime.wait_view(525, timeout=12.0, label="邮件_领法则：等待储物袋")

    def _remember_law(self, fact: dict[str, Any]) -> None:
        path = fanxiu_data_annotation_world_facts_path()
        facts = read_data_annotation_world_facts(path)
        task_facts = facts.setdefault("discoveries", {}).setdefault("task", {})
        task_facts[MAIL_CLAIM_LAW_TASK_ID] = {**fact, "updated_at": time.time()}
        write_data_annotation_world_facts(path, facts)

    def _schedule_active_law(self, active: dict[str, Any], *, payload: dict[str, Any], source: str) -> str:
        end_time_ms = int(active["end_time_ms"])
        next_time = law_next_time(end_time_ms)
        self._remember_law({**active, "next_time": next_time, "source": source})
        self._persist_scheduler_task_next_time(str(payload.get("__scheduler_task_id") or MAIL_CLAIM_LAW_TASK_ID), next_time)
        self._log("success", f"邮件_领法则：读取 Runtime end_time，法则持续到 {next_time}")
        return "success"

    def _storage_bag_shape_map(self, view: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        def visit(items: Iterable[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                if title:
                    result[title] = item
                visit(item.get("children") or [])
        visit((view.raw or {}).get("shapes") or [])
        return result

    def _use_claimed_law_from_bag(self, runtime: Any, *, base_id: int, name: str, stop_event: Any) -> dict[str, Any]:
        yield from self._open_storage_bag(runtime)
        view = runtime.view(525)
        grid = StorageBagGrid.from_shapes(self._storage_bag_shape_map(view), frame_width=900, frame_height=1600)
        snapshot = read_backpack_ui_snapshot()
        if not snapshot.get("complete"):
            raise RuntimeError(f"邮件_领法则：储物袋 Runtime 未完整加载：{snapshot.get('reason')}")
        reference = runtime.ocr_fragments(runtime.cur_frame(update=True))
        window = view.get_shape("窗口")
        if window is None:
            raise RuntimeError("邮件_领法则：缺少 #525 窗口标注")
        for _ in range(40):
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            viewport = register_storage_bag_viewport_from_quantity_ocr(reference, fragments, grid=grid)
            if not viewport.aligned:
                raise RuntimeError(f"邮件_领法则：当前格行定位失败：{viewport.reason}")
            cells = visible_storage_bag_cells(grid, viewport)
            plan = plan_storage_bag_item_click(snapshot, target_base_id=base_id, cells=cells, observations=quantity_observations_from_ocr(cells, fragments))
            if plan.ready:
                runtime.click_frame_point(525, *plan.point)
                yield from runtime.wait_view(567, timeout=12.0, label="邮件_领法则：等待法则详情")
                detail = verify_storage_bag_item_detail(plan, expected_name=name, detail_title_texts=[part.get("text") or "" for part in runtime.ocr_fragments(runtime.cur_frame(update=True))])
                if not detail.confirmed:
                    raise RuntimeError(f"邮件_领法则：详情二次核验失败：{detail.reason}")
                pre_use = next((item for item in snapshot.get("items") or [] if isinstance(item, dict) and item.get("base_id") == base_id and (_as_int(item.get("end_time")) or 0) > int(time.time() * 1000)), None)
                if pre_use is None:
                    raise RuntimeError("邮件_领法则：使用前未读取到目标法则的动态 end_time")
                yield from runtime.wait_click(567, "使用", timeout=8.0, label="邮件_领法则：使用已核验法则")
                yield from runtime.wait_view(177, timeout=12.0, label="邮件_领法则：等待领取结果")
                yield from runtime.wait_click(177, "继续", timeout=8.0, label="邮件_领法则：关闭领取结果")
                yield from runtime.goto_view(34)
                yield from self._open_storage_bag(runtime)
                return {"snapshot": read_backpack_ui_snapshot(), "activated": pre_use}
            if plan.status != "target_not_visible" or plan.viewport_runtime_start is None:
                raise RuntimeError(f"邮件_领法则：储物袋 Runtime-GUI 对齐失败：{plan.reason}")
            directive = plan_storage_bag_scroll(target_runtime_index=int(plan.runtime_index or -1), viewport_runtime_start=plan.viewport_runtime_start, visible_cell_count=len(cells))
            if directive.direction == "none":
                raise RuntimeError("邮件_领法则：目标应可见但未生成点击计划")
            runtime.drag_shape_content(window, direction=directive.direction, ratio=0.60 if directive.mode == "coarse" else 0.28, duration=0.55)
            yield from runtime.wait_action_settle(1.0)
        raise RuntimeError("邮件_领法则：储物袋滚动 40 次仍未定位目标")

    def _execute_mail_claim_law_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        payload = dict(payload or {})
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.goto_view(34)
        yield from self._open_storage_bag(runtime)
        active = active_law_end_time(read_backpack_ui_snapshot()) or self._remembered_law()
        if active is not None:
            yield from runtime.goto_view(34)
            return self._schedule_active_law(active, payload=payload, source="already_active")
        yield from runtime.goto_view(34)
        if not self._refresh_recent_mail_packets_for_runtime_log("法则邮件选择", flush_capture=False):
            raise RuntimeError("邮件_领法则：动态邮件模型不可用")
        from backend.db import engine
        selected = select_oldest_claimable_law_mail(current_runtime_mail_sequence_snapshot(lambda: engine))
        if selected is None:
            self._persist_scheduler_task_next_time(str(payload.get("__scheduler_task_id") or MAIL_CLAIM_LAW_TASK_ID), None)
            self._log("success", "邮件_领法则：没有可领取的法则邮件，作业休眠")
            return "success"
        claim_payload = {**payload, "target_mail_ids": [selected["mail_id"]]}
        yield from self._execute_mail_selective_claim_task(ctx, stop_event, claim_payload)
        use_result = yield from self._use_claimed_law_from_bag(runtime, base_id=int(selected["base_id"]), name=str(selected["name"]), stop_event=stop_event)
        active = active_law_end_time(use_result.get("snapshot") or {}) or {
            "instance_id": str((use_result.get("activated") or {}).get("instance_id") or ""),
            "base_id": _as_int((use_result.get("activated") or {}).get("base_id")),
            "end_time_ms": _as_int((use_result.get("activated") or {}).get("end_time")),
        }
        if active is None or active.get("base_id") != selected["base_id"]:
            raise RuntimeError("邮件_领法则：使用后未在 Runtime 读到目标法则 end_time")
        return self._schedule_active_law({**active, "mail_id": selected["mail_id"], "name": selected["name"]}, payload=payload, source="claimed_and_used")
