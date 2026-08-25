from __future__ import annotations

import re
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.arena_schedule import (
    DAOFA_TASK_ID,
    daofa_scheduler_in_window,
    daofa_window_text,
    next_daofa_cycle_trigger_at,
    next_daofa_trigger_at,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values


_RANK_RE = re.compile(r"第\s*(\d+)\s*名")
def _now() -> datetime:
    return datetime.now()


def read_daofa_runtime_snapshot(
    *,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    """Read arena business facts from the loaded game model, without GUI."""

    from backend.core.fanxiu.instrumentation.arena import read_daofa_snapshot

    return read_daofa_snapshot(self_power_hint=self_power_hint)


def daofa_runtime_facts_advanced(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Accept only a complete Runtime snapshot that reflects the last round."""

    return bool(
        current.get("available")
        and current.get("complete")
        and (
            _int_or_none(current.get("remain_times"))
            != _int_or_none(previous.get("remain_times"))
            or _int_or_none(current.get("rank"))
            != _int_or_none(previous.get("rank"))
            or current.get("targets") != previous.get("targets")
        )
    )


def daofa_runtime_facts_ready(
    facts: dict[str, Any],
    *,
    force_finish: bool,
) -> bool:
    """Require only the facts needed by the current target-selection mode."""

    if not facts.get("available"):
        return False
    if force_finish:
        return bool(facts.get("base_complete", facts.get("complete")))
    return bool(facts.get("complete"))


def resolve_daofa_remaining_times(
    facts: dict[str, Any],
    ocr_remaining: int | None,
) -> int | None:
    """Prefer the latest instrumented count; OCR is only a fallback."""

    instrumented = _int_or_none(facts.get("remain_times"))
    if facts.get("available") and instrumented is not None:
        return instrumented
    return ocr_remaining


def daofa_no_target_retry_at(
    now: datetime,
    payload: dict[str, Any] | None = None,
) -> datetime:
    """Return the business-selected retry time when no safe target exists."""

    options = payload or {}
    retry_seconds = max(60, int(options.get("no_target_retry_seconds") or 3600))
    candidate = now + timedelta(seconds=retry_seconds)
    force_finish_at = daofa_force_finish_at(
        now,
        threshold_minutes=int(options.get("force_finish_minutes") or 30),
    )
    if now < force_finish_at:
        return min(candidate, force_finish_at)
    return candidate


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def select_daofa_target(
    facts: dict[str, Any],
    *,
    battle_score: float | None,
    force_finish: bool = False,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Choose a normal protected target or the weakest force-finish target."""

    current_rank = _int_or_none(facts.get("rank"))
    targets: list[dict[str, Any]] = []
    for raw in facts.get("targets") or []:
        if not isinstance(raw, dict):
            continue
        target = dict(raw)
        rank = _int_or_none(target.get("rank"))
        power = _number_or_none(target.get("power"))
        if rank is None or power is None:
            continue
        if not force_finish and (battle_score is None or power >= float(battle_score)):
            continue
        target["relation"] = classify_fanxiu_target_relation(
            is_npc=bool(target.get("is_npc")),
            server_id=target.get("server_id"),
            data_dir=data_dir,
        )
        targets.append(target)

    if force_finish:
        return min(
            targets,
            key=lambda target: (float(target["power"]), int(target["rank"])),
        ) if targets else None
    if current_rank is None:
        return None

    ahead = [target for target in targets if int(target["rank"]) < current_rank]
    group_order = {"non_friendly": 0, "ally": 1, "alliance": 2, "same_server": 3}

    def attack_key(target: dict[str, Any]) -> tuple[int, int, int]:
        relation = target["relation"]
        relation_key = str(relation.get("relation") or "other_server")
        # Config order is protection-desc; larger indexes are less protected.
        server_priority = relation.get("server_priority")
        protection_key = -int(server_priority) if isinstance(server_priority, int) else 0
        return group_order.get(relation_key, 0), protection_key, int(target["rank"])

    if ahead:
        return min(ahead, key=attack_key)
    return None


def daofa_settlement_at(now: datetime) -> datetime:
    # Sunday settles at 22:00; Monday-Saturday use the end of the day.
    if now.weekday() == 6:
        return now.replace(hour=22, minute=0, second=0, microsecond=0)
    return (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def daofa_force_finish_at(now: datetime, *, threshold_minutes: int = 30) -> datetime:
    return daofa_settlement_at(now) - timedelta(minutes=max(1, int(threshold_minutes)))


def should_force_finish_daofa(now: datetime, *, threshold_minutes: int = 30) -> bool:
    return now >= daofa_force_finish_at(now, threshold_minutes=threshold_minutes)


class DaofaTaskMixin:
    def daily_daofa_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        task_id = str(payload.get("__scheduler_task_id") or "")
        if task_id != DAOFA_TASK_ID:
            return None
        now = _now()
        if daofa_scheduler_in_window(now):
            return None
        window_text = daofa_window_text(now)
        next_time = next_daofa_trigger_at(now).strftime("%Y-%m-%d %H:%M:%S")
        weekly_closed = now.weekday() == 6 and now.time() >= dt_time(22, 0)
        reason = (
            "本周窗口已结束，禁止跨周补跑"
            if weekly_closed
            else "当前周期尚未开放，等待当日 10:00"
        )
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": (
                f"道法争锋：当前不在 {window_text} 窗口，"
                f"{reason}，未执行游戏操作"
            ),
            "next_time": next_time,
            "current_scene": None,
            "scheduler_incident": {
                "kind": "window_expired",
                "cycle_kind": "weekly" if weekly_closed else "daily",
                "window": window_text,
                "reason": reason,
            },
        })

    def _set_daofa_next_trigger(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        task_id = str(payload.get("__scheduler_task_id") or DAOFA_TASK_ID)
        next_time = next_daofa_cycle_trigger_at(now or _now()).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(task_id, next_time)
        return next_time

    def _set_daofa_retry(
        self,
        payload: dict[str, Any],
        *,
        seconds: int,
        now: datetime | None = None,
        latest_at: datetime | None = None,
    ) -> str:
        current = now or _now()
        task_id = str(payload.get("__scheduler_task_id") or DAOFA_TASK_ID)
        candidate = current + timedelta(seconds=max(60, int(seconds)))
        if latest_at is not None and current < latest_at < candidate:
            candidate = latest_at
        if (
            candidate.date() == current.date()
            and daofa_scheduler_in_window(candidate)
        ):
            next_at = candidate
        else:
            next_at = next_daofa_trigger_at(current)
        next_time = next_at.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(task_id, next_time)
        return next_time

    def _run_daofa_challenge_round(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        challenge_point: tuple[float, float] | None = None,
        prompt_timeout: float = 15.0,
        result_timeout: float = 600.0,
        return_timeout: float = 45.0,
    ):
        """Complete exactly one #376 -> (#377) -> #378 -> #376 round."""

        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        scene_id, _score, _frame = runtime.current_scene([376, 377, 378], update=True)
        prompt_seen = scene_id == 377
        if scene_id not in {376, 377, 378}:
            raise RuntimeError("道法争锋：小闭环只能从 #376、#377 或 #378 开始")

        # The optional confirmation and battle result are business foreground
        # views.  Keep the claim scoped to this transition so unrelated popups
        # remain eligible for the generic guard.
        with runtime.expect_views(377, 378):
            if scene_id == 376:
                if challenge_point is None:
                    raise RuntimeError("道法争锋：从 #376 开始挑战时必须提供目标挑战按钮落点")
                click_x, click_y = map(float, challenge_point)
                image376 = (ctx.get("images") or {}).get(376)
                if not isinstance(image376, dict):
                    raise RuntimeError("道法争锋：缺少 #376 资产标注")
                width, height = self._frame_size(image376)
                if not (0.0 <= click_x <= width and 0.0 <= click_y <= height):
                    raise ValueError(f"道法争锋：挑战落点越界 ({click_x:.1f}, {click_y:.1f})")
                runtime.click_frame_point(376, click_x, click_y)
                landed = yield from runtime.wait_scene(
                    377,
                    378,
                    # #377 is optional.  When it is suppressed a real-player
                    # fight may run for minutes before #378 appears, so this wait
                    # must cover the battle rather than only the prompt grace.
                    timeout=max(float(prompt_timeout), float(result_timeout)),
                    label="道法争锋：等待挑战确认或挑战结果",
                )
                scene_id = int(getattr(landed, "id", landed))
                prompt_seen = scene_id == 377
            if scene_id == 377:
                runtime.click_shape_center(377, "确认")
                yield from runtime.wait_scene(
                    378,
                    timeout=float(result_timeout),
                    label="道法争锋：确认挑战后等待战斗结果",
                )
        result_text = runtime.ocr_text(update=True)
        runtime.click_shape_center(378, "继续")
        yield from runtime.wait_scene(
            376,
            timeout=float(return_timeout),
            label="道法争锋：结果页继续并返回挑战页",
        )
        return {"status": "success", "prompt_seen": prompt_seen, "result_text": result_text, "final_scene": 376}

    def _daofa_remaining_from_ocr(self, runtime: Any) -> int | None:
        text = runtime.ocr_text_in_shapes(376, ("次数",), padding=12)
        fraction = parse_ocr_values(text, expected_count=2, allow_extra_numbers=True)
        if fraction is None:
            fraction = parse_ocr_values(runtime.ocr_text(update=True), expected_count=2, allow_extra_numbers=True)
        return fraction[0] if fraction is not None else None

    def _daofa_challenge_template_delta(self, ctx: dict[str, Any]) -> tuple[float, float, dict[str, float]]:
        image = (ctx.get("images") or {}).get(376)
        if not isinstance(image, dict):
            raise RuntimeError("道法争锋：缺少 #376 资产")
        rank_shape = self._find_shape(image, "第x名")
        challenge_shape = self._find_shape(image, "挑战")
        window_shape = self._find_shape(image, "窗口")
        if rank_shape is None or challenge_shape is None or window_shape is None:
            raise RuntimeError("道法争锋：#376 必须标注「窗口/第x名」和「窗口/挑战」")
        width, height = self._frame_size(image)
        rank_cx = (float(rank_shape.get("x") or 0) + float(rank_shape.get("w") or 0) / 2) * width
        rank_cy = (float(rank_shape.get("y") or 0) + float(rank_shape.get("h") or 0) / 2) * height
        challenge_cx = (float(challenge_shape.get("x") or 0) + float(challenge_shape.get("w") or 0) / 2) * width
        challenge_cy = (float(challenge_shape.get("y") or 0) + float(challenge_shape.get("h") or 0) / 2) * height
        return challenge_cx - rank_cx, challenge_cy - rank_cy, self._box(window_shape, image)

    def _daofa_visible_ranks(self, runtime: Any, window_box: dict[str, float]) -> list[tuple[int, float, float]]:
        left = float(window_box.get("x") or 0)
        top = float(window_box.get("y") or 0)
        right = left + float(window_box.get("w") or 0)
        bottom = top + float(window_box.get("h") or 0)
        matches: list[tuple[int, float, float]] = []
        # 首次进入和每次 scroll_shape_content 都已经留下当前稳定帧；这里
        # 复用该帧做 OCR，避免每页再额外抓一帧，造成长列表定位成倍变慢。
        for line in runtime.ocr_fragments(update=False):
            text = str(line.get("text") or "")
            match = _RANK_RE.search(text)
            if not match:
                continue
            cx = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((int(match.group(1)), cx, cy))
        return sorted(matches, key=lambda item: item[2])

    def _locate_daofa_challenge_point(
        self,
        runtime: Any,
        ctx: dict[str, Any],
        target_rank: int,
        *,
        max_scrolls: int = 16,
    ):
        delta_x, delta_y, window_box = self._daofa_challenge_template_delta(ctx)
        top = float(window_box.get("y") or 0) + 8.0
        bottom = float(window_box.get("y") or 0) + float(window_box.get("h") or 0) - 8.0
        direction = "down"
        for attempt in range(max(1, int(max_scrolls)) + 1):
            visible = self._daofa_visible_ranks(runtime, window_box)
            for rank, x, y in visible:
                if rank != int(target_rank):
                    continue
                point = (x + delta_x, y + delta_y)
                if top <= point[1] <= bottom:
                    return point
                direction = "down" if point[1] > bottom else "up"
                break
            else:
                ranks = [rank for rank, _x, _y in visible]
                if ranks:
                    if int(target_rank) < min(ranks):
                        direction = "up"
                    elif int(target_rank) > max(ranks):
                        direction = "down"
            if attempt >= int(max_scrolls):
                break
            changed = yield from runtime.scroll_shape_content(
                376,
                "窗口",
                direction=direction,
                ratio=0.38,
                unchanged_confirmations=2,
            )
            if not changed:
                direction = "up" if direction == "down" else "down"
        raise RuntimeError(f"道法争锋：在 #376 滚动窗口中未找到第 {target_rank} 名的可见挑战按钮")

    def _wait_daofa_runtime_advance(
        self,
        runtime: Any,
        *,
        previous: dict[str, Any],
        timeout: float = 45.0,
        reason: str,
    ):
        started = time.monotonic()
        while time.monotonic() - started < float(timeout):
            facts = read_daofa_runtime_snapshot(
                self_power_hint=_number_or_none(previous.get("self_power")),
            )
            if facts.get("available") and facts.get("complete") and daofa_runtime_facts_advanced(previous, facts):
                self._log("detail", f"道法争锋：Runtime 已推进，reason={reason}")
                return facts
            yield from runtime.wait_action_settle(1.0)
        return None

    def _wait_daofa_runtime_ready(
        self,
        runtime: Any,
        *,
        force_finish: bool,
        timeout: float = 45.0,
        poll_seconds: float = 2.0,
    ):
        """Wait for the naturally loaded arena model before safe selection."""

        started = time.monotonic()
        facts = read_daofa_runtime_snapshot()
        while not daofa_runtime_facts_ready(facts, force_finish=force_finish):
            elapsed = time.monotonic() - started
            if elapsed >= max(0.0, float(timeout)):
                return facts
            reason = str(facts.get("reason") or "安全选人字段尚未齐全")
            self._log(
                "wait",
                f"道法争锋：等待 Runtime 完整数据 {elapsed:.1f}/{float(timeout):.0f}s，{reason}",
            )
            yield from runtime.wait_action_settle(
                min(max(0.2, float(poll_seconds)), max(0.2, float(timeout) - elapsed))
            )
            facts = read_daofa_runtime_snapshot()
        return facts

    def _leave_daofa_to_world(self, runtime: Any):
        # #376 is an activity overlay. Reuse the already verified activity-page
        # return closure until its lower-left return shape is promoted in assets.
        yield from self._日常报名返回世界(runtime)

    def _finish_completed_daofa(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        completed_rounds: int,
        completion_evidence: str,
    ):
        """Record consumed attempts before best-effort navigation cleanup."""

        next_time = self._set_daofa_next_trigger(payload)
        cleanup_warning = ""
        final_scene = 34
        try:
            yield from self._leave_daofa_to_world(runtime)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            cleanup_warning = f"；挑战已完成，但离场未完成：{exc}"
            final_scene = 376
            self._log("warning", f"道法争锋：{completion_evidence}，已保存完成周期；离场未完成：{exc}")

        self._log(
            "success",
            f"道法争锋：{completion_evidence}，今日已完成 {completed_rounds} 次挑战，下次 {next_time}{cleanup_warning}",
        )
        return {
            "result": "success",
            "message": f"道法争锋已完成，下次 {next_time}{cleanup_warning}",
            "current_scene": final_scene,
        }

    def _execute_daily_daofa_task_managed(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
    ):
        """Let the task recover its own #376 transaction before other jobs run."""
        try:
            return (yield from self._execute_daily_daofa_task(ctx, stop_event, payload))
        except Exception:
            asset_tree_path = ctx.get("asset_tree_path")
            runtime = self._fanxiu_runtime(
                ctx,
                asset_tree_path if isinstance(asset_tree_path, Path) else None,
                stop_event=stop_event,
            )
            try:
                yield from self._leave_daofa_to_world(runtime)
            except Exception as cleanup_exc:
                self._log("warning", f"道法争锋：失败后返回世界未完成：{cleanup_exc}")
            raise

    def _execute_daily_daofa_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
    ):
        payload = dict(payload or {})
        task_label = "道法争锋"
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        runtime.attrs["payload"] = payload
        scene_id, _score, frame = runtime.current_scene([34, 69, 376], update=True)
        started_in_daofa = scene_id == 376

        text = runtime.ocr_text(frame)
        if scene_id != 376:
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx, runtime, stop_event, frame, scene_id, text, label=task_label
                )
            status = yield from runtime.open_daily_entry(
                label=task_label,
                title_pattern=r"道\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
                initial_checks=2,
            )
            if status != "open":
                next_time = self._set_daofa_retry(
                    payload,
                    seconds=int(payload.get("retry_seconds") or 1800),
                )
                yield from runtime.goto_view(34)
                return {"result": "success", "message": f"未找到道法入口，{next_time} 重试"}
            yield from runtime.wait_scene(376, timeout=30.0, label="道法争锋：等待挑战页 #376")

        # Completion is authoritative on the visible game UI and does not need
        # target/ranking data.  This also avoids waiting for packet persistence
        # after all attempts have already been consumed.
        if self._daofa_remaining_from_ocr(runtime) == 0:
            return (yield from self._finish_completed_daofa(
                runtime,
                payload,
                completed_rounds=0,
                completion_evidence="OCR 已确认今日剩余挑战次数为 0",
            ))

        force_finish_on_entry = should_force_finish_daofa(
            _now(),
            threshold_minutes=int(payload.get("force_finish_minutes") or 30),
        )
        facts = yield from self._wait_daofa_runtime_ready(
            runtime,
            force_finish=force_finish_on_entry,
            timeout=float(payload.get("runtime_ready_timeout") or 45.0),
            poll_seconds=float(payload.get("runtime_ready_poll_seconds") or 2.0),
        )
        if daofa_runtime_facts_ready(facts, force_finish=force_finish_on_entry):
            prefix = "已从" if started_in_daofa else "进入 #376 后已从"
            self._log(
                "detail",
                f"道法争锋：{prefix}游戏 Runtime 取得安全选人数据"
                f"（本人战力来源={facts.get('self_power_source') or 'unknown'}）",
            )
        else:
            next_time = self._set_daofa_retry(
                payload,
                seconds=int(payload.get("retry_seconds") or 1800),
            )
            yield from self._leave_daofa_to_world(runtime)
            return {
                "result": "success",
                "message": f"Runtime 等待后仍未取得安全选人数据，{next_time} 安全复查",
                "current_scene": 34,
            }
        battle_score: float | None = None
        if not force_finish_on_entry:
            battle_score = _number_or_none(facts.get("self_power"))
            if battle_score is None:
                next_time = self._set_daofa_retry(
                    payload,
                    seconds=int(payload.get("retry_seconds") or 1800),
                )
                yield from self._leave_daofa_to_world(runtime)
                return {
                    "result": "success",
                    "message": f"Runtime 未取得本账号战力，{next_time} 安全复查",
                    "current_scene": 34,
                }
        completed_rounds = 0
        while True:
            remaining_ocr = self._daofa_remaining_from_ocr(runtime)
            remaining = resolve_daofa_remaining_times(facts, remaining_ocr)
            if remaining is None:
                raise RuntimeError("道法争锋：OCR 和 Runtime 都未取到剩余挑战次数")
            if remaining <= 0:
                return (yield from self._finish_completed_daofa(
                    runtime,
                    payload,
                    completed_rounds=completed_rounds,
                    completion_evidence="剩余挑战次数为 0",
                ))

            now = _now()
            force_finish = force_finish_on_entry or should_force_finish_daofa(
                now,
                threshold_minutes=int(payload.get("force_finish_minutes") or 30),
            )
            if force_finish:
                target = select_daofa_target(
                    facts,
                    battle_score=None,
                    force_finish=True,
                )
            else:
                target = select_daofa_target(facts, battle_score=battle_score)
            if target is None:
                retry_at = daofa_no_target_retry_at(now, payload)
                retry_seconds = 60 if force_finish else int(payload.get("no_target_retry_seconds") or 3600)
                next_time = self._set_daofa_retry(
                    payload,
                    seconds=retry_seconds,
                    now=now,
                    latest_at=retry_at,
                )
                yield from self._leave_daofa_to_world(runtime)
                message = "当前可见候选尚未匹配" if force_finish else "排名前暂无可战胜目标"
                return {"result": "success", "message": f"{message}，{next_time} 复查", "current_scene": 34}

            relation = target.get("relation") or {}
            self._log(
                "action",
                f"道法争锋：剩余 {remaining} 次，选择第 {target['rank']} 名「{target['name']}」"
                f"（{relation.get('relation_label') or '未知关系'}，战力 {float(target['power']):.6g}）",
            )
            point = yield from self._locate_daofa_challenge_point(
                runtime,
                ctx,
                int(target["rank"]),
                max_scrolls=int(payload.get("rank_scrolls") or 16),
            )
            yield from self._run_daofa_challenge_round(
                ctx,
                stop_event,
                challenge_point=point,
                prompt_timeout=float(payload.get("prompt_timeout") or 15.0),
                result_timeout=float(payload.get("battle_timeout") or 600.0),
                return_timeout=float(payload.get("return_timeout") or 45.0),
            )
            refreshed_facts = read_daofa_runtime_snapshot()
            runtime_advanced = daofa_runtime_facts_advanced(
                facts,
                refreshed_facts,
            )
            if runtime_advanced:
                self._log("detail", "道法争锋：挑战后 Runtime 模型已推进，直接使用最新候选")
            else:
                refreshed_facts = yield from self._wait_daofa_runtime_advance(
                    runtime,
                    previous=facts,
                    timeout=float(payload.get("packet_timeout") or 120.0),
                    reason="daily-daofa-post-challenge",
                )
            completed_rounds += 1
            if refreshed_facts is None:
                remaining_after = self._daofa_remaining_from_ocr(runtime)
                if remaining_after == 0:
                    facts = {**facts, "rank": int(target["rank"]), "remain_times": 0, "targets": []}
                    self._log("warning", "道法争锋：Runtime 未及时推进，但 #376 OCR 已确认 0 次，按今日完成收尾")
                    continue
                post_round_now = _now()
                post_round_force_finish = force_finish_on_entry or should_force_finish_daofa(
                    post_round_now,
                    threshold_minutes=int(payload.get("force_finish_minutes") or 30),
                )
                next_time = self._set_daofa_retry(
                    payload,
                    seconds=60 if post_round_force_finish else int(payload.get("retry_seconds") or 1800),
                    now=post_round_now,
                )
                yield from self._leave_daofa_to_world(runtime)
                return {
                    "result": "success",
                    "message": f"挑战已完成但 Runtime 未及时推进，{next_time} 安全复查",
                    "current_scene": 34,
                }
            facts = refreshed_facts
            next_target = select_daofa_target(facts, battle_score=battle_score) if battle_score is not None else None
            if next_target is not None and int(facts.get("remain_times") or 0) > 0:
                self._log("detail", f"道法争锋：小循环完成，下一目标第 {next_target['rank']} 名「{next_target['name']}」")
