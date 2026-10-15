from __future__ import annotations

import time
import re
from datetime import date, datetime, timedelta
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    select_schedule_activity,
)


_ZHENXIE_PARTICIPATION_SECONDS = 30.0
# 单次落点重识别等待上限。窄期望集合（#34/#85/#186）不能凭剩余 deadline
# 把一次 wait_scene 拖满整段预算——这之前造成过 133s 的单步卡顿；外层循环
# 仍以总 deadline 反复重试。
_ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS = 10.0


# Job-owned idempotency fact: this account joined today's 镇邪 fight
# (#271 参加 → #272 → 前往 → #85/#186).  It stores business facts and a time
# boundary only — never a step cursor:
#   joined_at          wall time the field (#85/#186) was first observed
#   earliest_leave_at  joined_at + participation seconds
#   stay_verified_at   wall time the field was observed again at/after
#                      earliest_leave_at (no 离开 in between: leaving needs an
#                      explicit 离开 + #86 确认).  Elapsed clock alone never
#                      proves the stay.
# A later attempt of the same day never re-registers.
ZHENXIE_JOINED_MARK = "zhenxie_joined"
ZHENXIE_JOIN_SCHEMA = "zhenxie_join_v2"
_ZHENXIE_JOINED_EVIDENCE = "272_goto_field"


def _iso_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def zhenxie_joined_today(mark: Any, today: date) -> bool:
    """True only for a mark proving today's join by the formal entry chain."""

    return (
        isinstance(mark, dict)
        and mark.get("date") == today.isoformat()
        and mark.get("evidence") == _ZHENXIE_JOINED_EVIDENCE
    )


ZHENXIE_FIELD_SCENE_IDS = frozenset({85, 186})


def zhenxie_on_field(match: Any) -> bool:
    """True only when the observed formal scene itself is the 镇邪 field.

    ``wait_scene([85, 186], required=False)`` only seeds Layer 0; Layer 1/2
    can return any other known scene (e.g. #34 world, #272 home), so a
    non-None match is not field evidence.
    """

    scene_id = getattr(match, "scene_id", getattr(match, "id", match))
    return isinstance(scene_id, int) and not isinstance(scene_id, bool) and scene_id in ZHENXIE_FIELD_SCENE_IDS


def zhenxie_stay_status(mark: Any, today: date, now: datetime) -> dict[str, Any]:
    """Classify today's participation stay from the persisted facts only.

    ``stay`` is ``verified`` (field re-observed after the boundary),
    ``pending`` (boundary known, not yet verified; ``remaining_seconds`` may be
    0 but still needs a fresh field observation) or ``unknown`` (legacy mark
    without the v2 boundary).  ``joined`` is False for anything not today's.
    """

    if not zhenxie_joined_today(mark, today):
        return {"joined": False, "stay": None, "remaining_seconds": None}
    earliest = _iso_time(mark.get("earliest_leave_at"))
    joined_at = _iso_time(mark.get("joined_at"))
    if mark.get("schema") != ZHENXIE_JOIN_SCHEMA or earliest is None or joined_at is None:
        return {"joined": True, "stay": "unknown", "remaining_seconds": None}
    verified = _iso_time(mark.get("stay_verified_at"))
    if verified is not None and verified >= earliest:
        return {"joined": True, "stay": "verified", "remaining_seconds": 0.0}
    return {"joined": True, "stay": "pending",
            "remaining_seconds": max(0.0, (earliest - now).total_seconds())}


def zhenxie_stay_verified_mark(mark: dict[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    """Return the mark with ``stay_verified_at`` if this field observation proves it."""

    earliest = _iso_time(mark.get("earliest_leave_at"))
    if mark.get("schema") != ZHENXIE_JOIN_SCHEMA or earliest is None or observed_at < earliest:
        return None
    return {**mark, "stay_verified_at": observed_at.isoformat(timespec="seconds")}


def zhenxie_boss_is_defeated(text: str) -> bool:
    """解析镇邪主页独立状态行；不把按钮灰色或一般区域当完成证据。"""
    value = re.sub(r'\s+', '', str(text or ''))
    return re.fullmatch(r'[\u4e00-\u9fff、·]{2,30}已被击败', value) is not None


def zhenxie_landing_wait_seconds(deadline: float, now: float) -> float:
    """Bound one landing re-identification wait to a small fixed budget."""

    return min(
        _ZHENXIE_LEAVE_REDISCOVER_WAIT_SECONDS,
        max(1.0, float(deadline) - float(now)),
    )


class ZhenxieTaskMixin:
    def daily_zhenxie_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        window_start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        window_end = now.replace(hour=21, minute=5, second=0, microsecond=0)
        reconcile = payload.get('reconcile_after_window', False)
        if type(reconcile) is not bool:
            raise ValueError('镇邪窗口后现场收尾开关必须为布尔值')
        if window_start <= now <= window_end:
            return None
        if reconcile and now > window_end:
            # 显式故障恢复只允许下方 flow 收束当前现场，不能重新报名。
            return None
        next_run = window_start if now < window_start else window_start + timedelta(days=1)
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "日常_镇邪：当前不在 21:00:00-21:05:00 窗口，未执行游戏操作",
            "next_time": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    @staticmethod
    def _zhenxie_scene_id(value: Any) -> int | None:
        scene_id = getattr(value, "id", value)
        try:
            return int(scene_id) if scene_id is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _zhenxie_scene_shape_titles(context: Any, scene_id: int) -> set[str]:
        """列出该场景资产里的 Shape 标题，用于“这个入口是否在此页存在”。"""

        try:
            view = context.view(scene_id)
        except Exception:
            return set()
        raw = getattr(view, "raw", None)
        if not isinstance(raw, dict):
            return set()
        return {
            str(shape.get("title") or "")
            for shape in (raw.get("shapes") or [])
            if isinstance(shape, dict)
        }

    @staticmethod
    def _zhenxie_shape_visible(context: Any, scene_id: int, title: str, frame: str) -> bool:
        """探测某个 Shape 当前是否可见。

        shape_matches 在“本场景没有该标注”或“该标注没有图像/OCR 条件”时会抛错；
        这里是在做入口/状态的存在性探测，这两种情况都等价于“当前不可用”，
        不能让异常越过探测语义（实测 #63 只有无条件『前往』，异常会导致
        『前往』回退分支永远走不到，作业从封面页启动就必然失败）。
        """

        try:
            return context.shape_matches(scene_id, title, frame_data_url=frame) is not None
        except RuntimeError:
            return False

    def _enter_daily_zhenxie(self, context: Any):
        """Enter the event from any valid timed-event landing scene."""

        _wait_scene_match = yield from context.wait_scene([63, 271, 272, 85, 34, 66], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        current = scene_id
        # #85 only identifies a generic region (its sole identity is “离开”).
        # It is also visible underneath the loading elder dialogue and cannot
        # prove that this account joined 镇邪. Re-enter the dedicated page.
        if current not in {63, 271, 272}:
            yield from context.go_scene(34)
            yield from context.wait_click_then_scene(34, "日程", 66)
            yield from context.wait_action_settle(3.0)
            yield from select_schedule_activity(
                context,
                r"镇邪",
                enter=True,
                require_runtime_alignment=True,
                allow_unique_runtime_card_with_bad_time_ocr=True,
                now=job_now(),
            )
            current = self._zhenxie_scene_id(
                (
                    yield from context.wait_scene(
                        [63, 271, 272],
                        wait=20.0,
                        label="日常_镇邪：等待已校验的活动卡片进入镇邪场景",
                    )
                )
            )

        if current == 63:
            frame = context.cur_frame(update=True)
            # #63 是活动封面页，页内入口是资产事实（历史上 #63 只有『前往』，
            # 且它没有图像/OCR 条件，不能当视觉门卫）。页身份已由场景识别确认，
            # 因此按本页实际存在的入口选择，而不是先做视觉匹配。
            titles = self._zhenxie_scene_shape_titles(context, 63)
            if "参加宗门镇邪" in titles:
                schedule_shape = "参加宗门镇邪"
            elif "前往" in titles:
                schedule_shape = "前往"
            else:
                raise RuntimeError("日常_镇邪：#63 资产里没有“参加宗门镇邪/前往”入口")
            yield from context.wait_click(63, schedule_shape)
            yield from context.wait_action_settle(1.0)
            current = self._zhenxie_scene_id(
                (
                    yield from context.wait_scene(
                        [271],
                        wait=180.0,
                        label="日常_镇邪：#63[前往] 后等待 #271",
                    )
                )
            )
        if current == 271:
            # Navigation can consume the remaining admission window. Do not
            # click a stale entrance after it closes; leave through its Shape.
            now = job_now()
            if now > now.replace(hour=21, minute=5, second=0, microsecond=0):
                return False
            # 页面身份先于按钮/参战内容就绪；单帧阴性不代表业务不存在。
            # 同一有界观察循环也用于点击后的完成确认，避免把仍在源页
            # 的过渡帧当作立即失败，也不把源页当作参战终态。
            current, participation_shape = yield from self._wait_zhenxie_participation(context)
            if participation_shape is None:
                if current == 272:
                    return (yield from self._enter_zhenxie_fight_from_home(context))
                return True
            for attempt in range(2):
                # Free entrance, not a purchase/reward action. Retry only after
                # the full result budget and a fresh positive entrance fact.
                now = job_now()
                if now > now.replace(hour=21, minute=5, second=0, microsecond=0):
                    return False
                yield from context.wait_click(271, participation_shape)
                try:
                    current, _ = yield from self._wait_zhenxie_participation(context, after_click=True)
                    break
                except TimeoutError:
                    now = job_now()
                    if now > now.replace(hour=21, minute=5, second=0, microsecond=0):
                        return False
                    if attempt == 1:
                        raise
                    current, participation_shape = yield from self._wait_zhenxie_participation(context)
                    if participation_shape is None:
                        break
        if current == 272:
            return (yield from self._enter_zhenxie_fight_from_home(context))
        raise RuntimeError(
            f"日常_镇邪：未能到达 #272/#85，当前 #{current if current is not None else 'unknown'}"
        )

    def _observe_zhenxie_boss_defeated(self, context: Any):
        """两次新鲜 #272 状态行确认邪灵已败；只读取当前真实画面。"""
        for observation in range(2):
            yield from context.wait_scene([272], wait=5)
            frame = context.cur_frame(update=True)
            text = context.ocr_text_in_shapes(272, ('邪灵状态',), frame_data_url=frame, crop=True)
            if not zhenxie_boss_is_defeated(text):
                return False
            if observation == 0:
                yield from context.wait_action_settle(0.5)
        self._log('info', '日常_镇邪：实际主页状态行连续确认邪灵已被击败')
        return True

    def _enter_zhenxie_fight_from_home(self, context: Any):
        if (yield from self._observe_zhenxie_boss_defeated(context)):
            return 'boss_defeated'
        try:
            yield from context.wait_click_then_scene(272, '前往', [85, 186])
        except TimeoutError:
            # 邪灵也可能在点击等待期间被击败。仍在 #272 时重新取证，
            # 只有真实已败状态才收束，其他入口错误继续向上抛出。
            match = yield from context.wait_scene([272], wait=5, required=False)
            if match is not None and (yield from self._observe_zhenxie_boss_defeated(context)):
                return 'boss_defeated'
            raise
        return True

    def _wait_zhenxie_participation(self, context: Any, *, after_click: bool = False):
        """Wait for a real entrance or joined evidence without sending actions.

        #271's scene marker can match while its contents are still loading.
        After clicking, the source page is an intermediate observation until
        #272 appears. Generic region #85 and costume bonus announcements
        ("真元自然恢复速度提升") do not prove participation.
        """
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            match = yield from context.wait_scene(
                [271, 272], wait=min(5.0, max(1.0, deadline - time.monotonic())),
                required=False, label="日常_镇邪：等待参加入口或已参战事实",
            )
            if match is not None:
                current = match.scene_id
                if current == 272:
                    return current, None
                frame = match.frame_data_url
                if not after_click:
                    for title in ("参加宗门镇邪", "前往", "参加"):
                        if self._zhenxie_shape_visible(context, 271, title, frame):
                            return 271, title
            yield from context.wait_action_settle(0.5)
        raise TimeoutError(
            "日常_镇邪：等待 20 秒仍未确认参战" if after_click else
            "日常_镇邪：等待 20 秒仍无参加入口或镇邪主页证据"
        )

    def _leave_daily_zhenxie(self, context: Any):
        """Consume nested activity/confirmation layers until #34 is real."""

        deadline = time.monotonic() + 135.0
        landing = yield from context.wait_scene(
            [34,
            85,
            186,
            272,
            271],
            wait=90.0,
            label="日常_镇邪：参战后等待可离开的稳定场景",
        )
        current = self._zhenxie_scene_id(landing)
        for _step in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("日常_镇邪：多层离场超时，尚未回到 #34")
            if current == 34:
                return 34
            if current == 271:
                yield from context.wait_click(271, "返回")
                yield from context.wait_action_settle(2.0)
                landed = yield from context.wait_scene(
                    [34, 85, 186], wait=10.0,
                    label="日常_镇邪：关闭长老对话后确认安全落点",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            if current in {85, 186}:
                context.click_shape(current, "离开")
                yield from context.wait_action_settle(2.0)
                landed = yield from context.wait_scene(
                    [34,
                    85,
                    186],
                    wait=zhenxie_landing_wait_seconds(deadline, time.monotonic()),
                    label="日常_镇邪：点击离开后重新识别多层落点",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            if current == 272:
                yield from context.wait_click(272, "返回")
                yield from context.wait_action_settle(2.0)
                landed = yield from context.go_scene(34)
                current = self._zhenxie_scene_id(landed)
                continue
            if current is None:
                landed = yield from context.wait_scene(
                    [34,
                    85,
                    186],
                    wait=zhenxie_landing_wait_seconds(deadline, time.monotonic()),
                    label="日常_镇邪：重新识别多层离场上下文",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            raise RuntimeError(f"日常_镇邪：多层离场落点异常：#{current}")

        raise RuntimeError(f"日常_镇邪：多层离场动作次数耗尽，当前 #{current or 'unknown'}")

    def daily_zhenxie_flow(self, context: Any):
        now = job_now()
        window_start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        next_run_text = (window_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        window_end = now.replace(hour=21, minute=5, second=0, microsecond=0)
        task_id = str(context.payload.get("__scheduler_task_id") or "daily-zhenxie")
        joined_mark = context.payload.get(ZHENXIE_JOINED_MARK)
        if joined_mark is None:
            joined_mark = self._get_scheduler_task_payload_flag(task_id, ZHENXIE_JOINED_MARK)
        status = zhenxie_stay_status(joined_mark, now.date(), datetime.now())
        if status["joined"]:
            # A previous attempt already proved today's join.  Never register
            # again; finish an unverified stay only while still on the field.
            joined_text = (
                f"本日已由 attempt {joined_mark.get('attempt_id') or '?'} "
                f"于 {joined_mark.get('joined_at')} 确认参战"
            )
            stay = status["stay"]
            if stay == "pending":
                stay = yield from self._complete_zhenxie_stay(context, task_id, joined_mark)
            current = yield from self._leave_daily_zhenxie(context)
            context.set_next_time(next_run_text)
            verified = stay == "verified"
            return {
                "result": "success" if verified else "pass",
                "message": (
                    f"日常_镇邪：{joined_text}，停留已由战场复核满足 "
                    f"{_ZHENXIE_PARTICIPATION_SECONDS:.0f} 秒；本次未重复报名并安全离场"
                    if verified else
                    f"日常_镇邪：{joined_text}，但停留时长未能证明（{stay}）；"
                    "本次未重复报名并安全离场"
                ),
                "current_scene": current,
            }
        if context.payload.get('reconcile_after_window') is True and now > window_end:
            match = yield from context.wait_scene([34, 63, 271, 272, 85, 186], wait=5)
            entry_outcome = 'boss_defeated' if (
                match.scene_id == 272 and (yield from self._observe_zhenxie_boss_defeated(context))
            ) else False
            if match.scene_id == 63:
                yield from context.go_scene(34)
        else:
            entry_outcome = yield from self._enter_daily_zhenxie(context)
        entered = entry_outcome is True
        stay = None
        if entered:
            joined_at = datetime.now()
            joined = {
                "schema": ZHENXIE_JOIN_SCHEMA,
                "date": now.date().isoformat(),
                "joined_at": joined_at.isoformat(timespec="seconds"),
                "earliest_leave_at": (
                    joined_at + timedelta(seconds=_ZHENXIE_PARTICIPATION_SECONDS)
                ).isoformat(timespec="seconds"),
                "evidence": _ZHENXIE_JOINED_EVIDENCE,
                "attempt_id": context.payload.get("__scheduler_attempt_id"),
            }
            if self._set_scheduler_task_payload_flag(task_id, ZHENXIE_JOINED_MARK, joined):
                context.payload[ZHENXIE_JOINED_MARK] = joined
            else:
                # The join already happened; a missing fact only weakens a
                # later retry's report, so do not abort the safe exit.
                self._log("warning", "日常_镇邪：已参战，但参战事实未确认落盘")
            stay = yield from self._complete_zhenxie_stay(context, task_id, joined)
        current = yield from self._leave_daily_zhenxie(context)
        context.set_next_time(next_run_text)
        return {
            "result": "success" if entered and stay == "verified" else "pass",
            "message": (
                "日常_镇邪：实际邪灵已被击败，安全离场并排程次日" if entry_outcome == 'boss_defeated' else
                "日常_镇邪：已参战并经战场复核停留至少 30 秒，离开后返回主界面"
                if entered and stay == "verified" else
                f"日常_镇邪：已参战，但停留时长未能证明（{stay}），离开后返回主界面"
                if entered else
                "日常_镇邪：到达报名页时窗口已关闭，安全离场并排程次日"
            ),
            "current_scene": current,
        }

    def _complete_zhenxie_stay(self, context: Any, task_id: str, mark: dict[str, Any]):
        """Stay on the field until the boundary, then prove it by re-observation.

        Returns ``verified`` or ``unverified``.  A field that is no longer
        visible (left, unknown frame) leaves the stay unproven; nothing is
        clicked here and registration is never repeated.
        """

        remaining = zhenxie_stay_status(mark, date.fromisoformat(mark["date"]), datetime.now())
        if remaining["stay"] == "verified":
            return "verified"
        if remaining["stay"] != "pending":
            return "unverified"
        match = yield from context.wait_scene(
            [85, 186], wait=10.0, required=False,
            label="日常_镇邪：复核仍在战场后再完成剩余停留",
        )
        if not zhenxie_on_field(match):
            return "unverified"
        wait_seconds = float(remaining["remaining_seconds"] or 0.0)
        if wait_seconds > 0:
            yield from context.wait_action_settle(wait_seconds)
        match = yield from context.wait_scene(
            [85, 186], wait=10.0, required=False,
            label="日常_镇邪：停留边界后复核仍在战场",
        )
        updated = (
            zhenxie_stay_verified_mark(mark, datetime.now())
            if zhenxie_on_field(match) else None
        )
        if updated is None:
            return "unverified"
        if self._set_scheduler_task_payload_flag(task_id, ZHENXIE_JOINED_MARK, updated):
            context.payload[ZHENXIE_JOINED_MARK] = updated
        else:
            self._log("warning", "日常_镇邪：停留已复核，但复核事实未确认落盘")
        return "verified"
