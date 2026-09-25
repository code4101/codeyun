from __future__ import annotations

"""Closed loop for a law attached to a mail reward.

The duration is intentionally never inferred from a cross number or a fixed
number of days.  The only scheduling fact is the live item's ``end_time``.
"""

import time
from datetime import datetime
from typing import Any, Iterable

from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_world_facts_path
from backend.core.fanxiu.data_annotation.state import (
    read_data_annotation_world_facts,
    edit_data_annotation_world_facts,
)
from backend.core.fanxiu.instrumentation.backpack_ui import read_backpack_ui_snapshot
from backend.core.fanxiu.instrumentation.role_progression import read_role_profile_from_memory
from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_reward_is_faze,
    fanxiu_mail_rewards_from_payload,
)
from backend.core.fanxiu.mail.runtime_store import current_runtime_mail_sequence_snapshot
from backend.core.fanxiu.runtime_gui import (
    StorageBagGrid,
    plan_storage_bag_item_click,
    plan_storage_bag_scroll,
    register_storage_bag_viewport_from_quantity_ocr,
    upscaled_ocr_fragments,
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
        and fanxiu_mail_reward_is_faze(reward)
        and _as_int(reward.get("item_id") or reward.get("base_id")) is not None
    ]


def select_oldest_claimable_law_mail(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Select exactly one oldest eligible mail and preserve its concrete reward."""

    candidates: list[dict[str, Any]] = []
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        if not (
            str(item.get("execution_status") or "") == "unclaimed"
            and bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
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


def active_law_end_time(
    snapshot: dict[str, Any],
    *,
    active_faze_id: int,
    active_item_base_id: int | None = None,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """Return expiry only with both active-state and catalog identity evidence.

    A future backpack ``end_time`` alone is merely the unused item's expiry;
    treating that as activation was the original false-positive.  RoleMgr's
    ``V_FazeId`` is a law-resource id (for example 10020), not an item base id
    (for example 10080014), so callers must not compare those namespaces.
    """

    if not int(active_faze_id or 0) or active_item_base_id is None:
        return None
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    active = [
        item
        for item in snapshot.get("items") or []
        if isinstance(item, dict)
        and _as_int(item.get("base_id")) == int(active_item_base_id)
        and (_as_int(item.get("end_time")) or 0) > now
    ]
    if len(active) != 1:
        return None
    item = active[0]
    return {"instance_id": str(item.get("instance_id") or ""), "base_id": _as_int(item.get("base_id")), "end_time_ms": _as_int(item.get("end_time"))}


def select_claimed_law_from_backpack(
    mail_snapshot: dict[str, Any],
    backpack_snapshot: dict[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """Recover an already claimed but not yet used law from current facts."""

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    names_by_base_id: dict[int, str] = {}
    for item in mail_snapshot.get("items") or []:
        if not isinstance(item, dict) or str(item.get("execution_status") or "") != "claimed":
            continue
        rewards = _law_rewards(item)
        if len(rewards) != 1:
            continue
        base_id = _as_int(rewards[0].get("item_id") or rewards[0].get("base_id"))
        name = str(rewards[0].get("item_name") or rewards[0].get("name") or "")
        if base_id is not None and name:
            names_by_base_id[base_id] = name

    candidates = [
        {
            "instance_id": str(item.get("instance_id") or ""),
            "base_id": _as_int(item.get("base_id")),
            "name": names_by_base_id.get(_as_int(item.get("base_id")) or -1, ""),
            "end_time_ms": _as_int(item.get("end_time")),
            "ui_index": _as_int(item.get("ui_index")) or 0,
        }
        for item in backpack_snapshot.get("items") or []
        if isinstance(item, dict)
        and not bool(item.get("is_padding"))
        and (_as_int(item.get("base_id")) or -1) in names_by_base_id
        and (_as_int(item.get("end_time")) or 0) > now
    ]
    return min(candidates, key=lambda item: (int(item["end_time_ms"] or 0), int(item["ui_index"]))) if candidates else None


def current_role_faze_id() -> int:
    profile = read_role_profile_from_memory(MumuProcessMemory.discover_cached())
    if not profile.get("ok"):
        raise RuntimeError(f"邮件_领法则：RoleMgr 法则状态不可用：{profile.get('reason')}")
    faze_id = _as_int(profile.get("faze"))
    if faze_id is None:
        raise RuntimeError("邮件_领法则：RoleMgr V_FazeId 缺失")
    return faze_id


def law_next_time(end_time_ms: int) -> str:
    return datetime.fromtimestamp(int(end_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")


def law_detail_title_texts(
    tokens: Iterable[dict[str, Any]],
    *,
    frame_width: int = 900,
    frame_height: int = 1600,
) -> tuple[str, ...]:
    """Read only the dynamic title row of the law-detail overlay.

    #567 deliberately has no item-name identity Shape because the name is
    dynamic.  Its title row is nevertheless stable, so the dedicated law
    transaction can use that narrow ROI as independent evidence after the
    Runtime-aligned bag click.
    """

    left, right = frame_width * 0.28, frame_width * 0.62
    top, bottom = frame_height * 0.14, frame_height * 0.23
    accepted = [
        token
        for token in tokens
        if isinstance(token, dict)
        and left <= float(token.get("x") or 0) + float(token.get("w") or 0) / 2 <= right
        and top <= float(token.get("y") or 0) + float(token.get("h") or 0) / 2 <= bottom
        and str(token.get("text") or "").strip()
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for token in accepted:
        line_id = str(token.get("parent_line_id") or f"y:{round(float(token.get('y') or 0) / 12)}")
        grouped.setdefault(line_id, []).append(token)
    return tuple(
        "".join(
            str(token.get("text") or "").strip()
            for token in sorted(line, key=lambda item: (float(item.get("x") or 0), int(item.get("order") or 0)))
        )
        for line in grouped.values()
    )


def finish_law_activation(context: Any, transition_timeout: float = 12.0):
    """Finish the post-``使用`` law flow and verify every scene landing.

    A normal law lands directly on the ``#177`` reward popup.  A 天姿 law
    instead opens ``#751`` first-companion selection, then ``#752`` companion
    detail with a ``缔结天契`` action, then ``#753`` success, and finally
    ``#177``.  The only accepted evidence is the scene id returned by the
    layered ``wait_scene``.  A missing or unexpected landing raises; no action
    is ever repeated at a guessed coordinate.
    """

    timeout = max(6.0, float(transition_timeout))

    def require(match: Any, expected: tuple[int, ...], stage: str) -> int:
        scene_id = int(getattr(match, "scene_id", 0) or 0)
        if scene_id not in expected:
            expected_text = "/".join(f"#{item}" for item in expected)
            raise RuntimeError(
                f"邮件_领法则：{stage} 落点错误：期望 {expected_text}，实际 #{scene_id or 'unknown'}"
            )
        return scene_id

    match = yield from context.wait_scene(
        [177, 751, 752, 753],
        wait=timeout,
        label="邮件_领法则：等待使用结果",
    )
    scene_id = require(match, (177, 751, 752, 753), "使用后")

    if scene_id == 751:
        yield from context.wait_click(
            751, "第一位仙缘", timeout=8.0, label="邮件_领法则：选择第一位仙缘"
        )
        match = yield from context.wait_scene(
            [752], wait=timeout, label="邮件_领法则：等待人物详情"
        )
        scene_id = require(match, (752,), "选择仙缘后")

    if scene_id == 752:
        yield from context.wait_click(
            752, "缔结天契", timeout=8.0, label="邮件_领法则：缔结天契"
        )
        match = yield from context.wait_scene(
            [753], wait=timeout, label="邮件_领法则：等待结契成功"
        )
        scene_id = require(match, (753,), "缔结天契后")

    if scene_id == 753:
        yield from context.wait_click(
            753, "继续", timeout=8.0, label="邮件_领法则：确认结契成功"
        )
        match = yield from context.wait_scene(
            [177], wait=timeout, label="邮件_领法则：等待领取结果"
        )
        scene_id = require(match, (177,), "结契成功后")

    yield from context.wait_click(
        177, "继续", timeout=8.0, label="邮件_领法则：关闭领取结果"
    )
    match = yield from context.wait_scene(
        [34, 525], wait=timeout, label="邮件_领法则：等待返回世界或储物袋"
    )
    scene_id = require(match, (34, 525), "关闭领取结果后")
    if scene_id == 525:
        yield from context.go_scene(34)
    match = yield from context.wait_scene([34], wait=timeout, label="邮件_领法则：确认世界闭环")
    return require(match, (34,), "法则收尾")


class MailClaimLawTaskMixin:
    def _remembered_law(self) -> dict[str, Any] | None:
        facts = read_data_annotation_world_facts(fanxiu_data_annotation_world_facts_path())
        fact = ((facts.get("discoveries") or {}).get("task") or {}).get(MAIL_CLAIM_LAW_TASK_ID)
        end_time_ms = _as_int((fact or {}).get("end_time_ms")) if isinstance(fact, dict) else None
        return dict(fact) if end_time_ms and end_time_ms > int(time.time() * 1000) else None

    def _open_storage_bag(self, context: Any):
        """#525 has no graph edge: use the named #34 entry, then verify #525."""
        yield from context.wait_click(34, "储物袋", timeout=8.0, label="邮件_领法则：进入储物袋")
        yield from context.wait_scene([525], wait=12.0, label="邮件_领法则：等待储物袋")

    def _remember_law(self, fact: dict[str, Any]) -> None:
        path = fanxiu_data_annotation_world_facts_path()
        with edit_data_annotation_world_facts(path) as facts:
            task_facts = facts.setdefault("discoveries", {}).setdefault("task", {})
            task_facts[MAIL_CLAIM_LAW_TASK_ID] = {**fact, "updated_at": time.time()}

    def _schedule_active_law(
        self,
        active: dict[str, Any],
        *,
        active_faze_id: int,
        payload: dict[str, Any],
        source: str,
    ) -> str:
        end_time_ms = int(active["end_time_ms"])
        next_time = law_next_time(end_time_ms)
        self._remember_law({
            **active,
            "active_faze_id": int(active_faze_id),
            "next_time": next_time,
            "source": source,
        })
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

    def _wait_verified_law_detail(self, context: Any, plan: Any, *, name: str):
        """Wait for #567 by its action gate plus exact dynamic item title.

        Whole-scene recognition remains useful for navigation, but the live
        law overlay can miss its ``效果说明`` identity OCR.  Here the preceding
        unique Runtime/grid alignment supplies the click authorization, while
        the fresh title ROI and visible ``使用`` button prove the successor.
        """

        deadline = time.monotonic() + 12.0
        last_reason = "详情尚未出现"
        while time.monotonic() < deadline:
            frame = context.cur_frame(update=True)
            use_gate = context.shape_matches(567, "使用", frame_data_url=frame)
            title_texts = law_detail_title_texts(context.full_frame_ocr_tokens(frame))
            detail = verify_storage_bag_item_detail(
                plan,
                expected_name=name,
                detail_title_texts=title_texts,
            )
            if use_gate is not None and detail.confirmed:
                self._log("detail", f"邮件_领法则：详情页动态标题核验通过：{detail.reason}")
                return
            last_reason = f"使用按钮={'已识别' if use_gate is not None else '未识别'}；{detail.reason}"
            yield from context.wait_action_settle(0.5)
        raise RuntimeError(f"邮件_领法则：等待法则详情失败：{last_reason}")

    def _use_claimed_law_from_bag(self, context: Any, *, base_id: int, name: str, stop_event: Any) -> dict[str, Any]:
        yield from self._open_storage_bag(context)
        view = context.view(525)
        grid = StorageBagGrid.from_shapes(self._storage_bag_shape_map(view), frame_width=900, frame_height=1600)
        snapshot = read_backpack_ui_snapshot()
        if not snapshot.get("complete"):
            raise RuntimeError(f"邮件_领法则：储物袋 Runtime 未完整加载：{snapshot.get('reason')}")
        # The per-cell stack counts are tiny white digits the native detector
        # misses; OCR them at 2x so the Runtime sequence has usable soft
        # anchors.  The alignment itself stays Runtime->GUI geometry.
        reference = upscaled_ocr_fragments(context, context.cur_frame(update=True))
        window = view.get_shape("窗口")
        if window is None:
            raise RuntimeError("邮件_领法则：缺少 #525 窗口标注")
        geometry_retries = 0
        for scroll_index in range(40):
            frame = context.cur_frame(update=True)
            fragments = upscaled_ocr_fragments(context, frame)
            viewport = register_storage_bag_viewport_from_quantity_ocr(reference, fragments, grid=grid)
            if not viewport.aligned:
                # A transient mid-animation frame can still starve the
                # calibration; re-baseline the reference a bounded number of
                # times before failing closed.
                if scroll_index < 3:
                    reference = fragments
                    yield from context.wait_action_settle(0.8)
                    continue
                raise RuntimeError(f"邮件_领法则：当前格行定位失败：{viewport.reason}")
            cells = visible_storage_bag_cells(grid, viewport)
            plan = plan_storage_bag_item_click(snapshot, target_base_id=base_id, cells=cells, observations=quantity_observations_from_ocr(cells, fragments))
            if plan.ready:
                context.click_frame_point(525, *plan.point)
                yield from self._wait_verified_law_detail(context, plan, name=name)
                pre_use = next((item for item in snapshot.get("items") or [] if isinstance(item, dict) and item.get("base_id") == base_id and (_as_int(item.get("end_time")) or 0) > int(time.time() * 1000)), None)
                if pre_use is None:
                    raise RuntimeError("邮件_领法则：使用前未读取到目标法则的动态 end_time")
                yield from context.wait_click(567, "使用", timeout=8.0, label="邮件_领法则：使用已核验法则")
                # 普通法则直接落到 #177；天姿法则走 #751→#752→#753→#177。
                # 每个落点都由 finish_law_activation 严格核对，缺失或错误即抛错。
                yield from finish_law_activation(context, transition_timeout=12.0)
                return {"activated": pre_use}
            if plan.status == "target_not_visible" and plan.viewport_runtime_start is not None:
                directive = plan_storage_bag_scroll(target_runtime_index=int(plan.runtime_index or -1), viewport_runtime_start=plan.viewport_runtime_start, visible_cell_count=len(cells))
                if directive.direction == "none":
                    raise RuntimeError("邮件_领法则：目标应可见但未生成点击计划")
                # 统一使用框架默认滚动手势（ratio 0.5、duration 1.5s）。2026-09-22
                # 实测 #525 对短于 1s 的快速手势会整格不动。
                context.drag_shape_content(window, direction=directive.direction)
                yield from context.wait_action_settle(1.0)
                continue
            # The count OCR is a soft anchor: a clustered frame can leave only
            # one or two usable observations.  Sweep the list a little to expose
            # more distinct counts before failing closed, instead of dying on
            # the first ambiguous frame.
            geometry_retries += 1
            if geometry_retries > 8:
                raise RuntimeError(f"邮件_领法则：储物袋 Runtime-GUI 对齐失败：{plan.reason}")
            direction = "down" if geometry_retries % 2 else "up"
            context.drag_shape_content(window, direction=direction)
            yield from context.wait_action_settle(0.8)
        raise RuntimeError("邮件_领法则：储物袋滚动 40 次仍未定位目标")

    def _execute_mail_claim_law_task(self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None):
        payload = dict(payload or {})
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from context.go_scene(34)
        yield from self._open_storage_bag(context)
        backpack_snapshot = read_backpack_ui_snapshot()
        active_faze_id = current_role_faze_id()
        remembered = self._remembered_law()
        if active_faze_id:
            yield from context.go_scene(34)
            if remembered is None:
                raise RuntimeError(
                    f"邮件_领法则：RoleMgr 已有法则 {active_faze_id}，但缺少其可靠结束时间"
                )
            remembered_faze_id = _as_int(remembered.get("active_faze_id"))
            if remembered_faze_id not in {None, active_faze_id}:
                raise RuntimeError(
                    "邮件_领法则：RoleMgr 当前法则与记忆中的法则不一致，拒绝沿用旧结束时间；"
                    f"current={active_faze_id} remembered={remembered_faze_id}"
                )
            return self._schedule_active_law(
                remembered,
                active_faze_id=active_faze_id,
                payload=payload,
                source="already_active",
            )
        yield from context.go_scene(34)
        if not self._refresh_runtime_mail_snapshot("法则邮件选择", force_refresh=True):
            raise RuntimeError("邮件_领法则：动态邮件模型不可用")
        from backend.db import engine
        mail_snapshot = current_runtime_mail_sequence_snapshot(lambda: engine)
        recovered = select_claimed_law_from_backpack(mail_snapshot, backpack_snapshot)
        if recovered is not None:
            yield from context.go_scene(34)
            use_result = yield from self._use_claimed_law_from_bag(
                context,
                base_id=int(recovered["base_id"]),
                name=str(recovered["name"]),
                stop_event=stop_event,
            )
            activated_faze_id = current_role_faze_id()
            if activated_faze_id == 0:
                raise RuntimeError("邮件_领法则：使用后 RoleMgr V_FazeId 仍为 0")
            return self._schedule_active_law(
                {**recovered, "end_time_ms": recovered["end_time_ms"]},
                active_faze_id=activated_faze_id,
                payload=payload,
                source="recovered_claimed_item",
            )

        selected = select_oldest_claimable_law_mail(mail_snapshot)
        if selected is None:
            self._persist_scheduler_task_next_time(str(payload.get("__scheduler_task_id") or MAIL_CLAIM_LAW_TASK_ID), None)
            self._log("success", "邮件_领法则：没有可领取的法则邮件，作业休眠")
            return "success"
        claim_payload = {**payload, "target_mail_ids": [selected["mail_id"]]}
        selected_mail_id = str(selected["mail_id"])
        selected_base_id = int(selected["base_id"])

        def authorize_selected_law_mail(item: dict[str, Any]) -> bool:
            item_id = str(item.get("id") or item.get("mail_id") or "")
            rewards = _law_rewards(item)
            return (
                item_id == selected_mail_id
                and len(rewards) == 1
                and _as_int(rewards[0].get("item_id") or rewards[0].get("base_id")) == selected_base_id
            )

        yield from self._execute_mail_selective_claim_task(
            ctx,
            stop_event,
            claim_payload,
            protected_claim_authorizer=authorize_selected_law_mail,
            cleanup_after_claim=False,
        )
        use_result = yield from self._use_claimed_law_from_bag(context, base_id=int(selected["base_id"]), name=str(selected["name"]), stop_event=stop_event)
        activated_faze_id = current_role_faze_id()
        if activated_faze_id == 0:
            raise RuntimeError("邮件_领法则：使用后 RoleMgr V_FazeId 仍为 0")
        active = {
            "instance_id": str((use_result.get("activated") or {}).get("instance_id") or ""),
            "base_id": _as_int((use_result.get("activated") or {}).get("base_id")),
            "end_time_ms": _as_int((use_result.get("activated") or {}).get("end_time")),
        }
        if active is None or active.get("base_id") != selected["base_id"]:
            raise RuntimeError("邮件_领法则：使用后未在 Runtime 读到目标法则 end_time")
        return self._schedule_active_law(
            {**active, "mail_id": selected["mail_id"], "name": selected["name"]},
            active_faze_id=activated_faze_id,
            payload=payload,
            source="claimed_and_used",
        )
