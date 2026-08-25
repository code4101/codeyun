from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.instrumentation.godsoul_boss import (
    read_godsoul_boss_challenge_snapshot,
    read_godsoul_boss_reward_snapshot,
)


MORNING_TRIGGER = (11, 59)
EVENING_TRIGGER = (17, 59)
MORNING_CHALLENGE_DEADLINE = (12, 20)
EVENING_CHALLENGE_DEADLINE = (18, 20)
REWARD_DEADLINE = (22, 0)
REWARD_EVIDENCE_RETRY_MINUTES = 10


def _at(now: datetime, clock: tuple[int, int]) -> datetime:
    return now.replace(
        hour=clock[0],
        minute=clock[1],
        second=0,
        microsecond=0,
    )


def next_moyu_challenge_time(now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    morning = _at(current, MORNING_TRIGGER)
    evening = _at(current, EVENING_TRIGGER)
    if current < morning:
        return morning
    if current < evening:
        return evening
    return morning + timedelta(days=1)


def select_moyu_reward(rewards: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        dict(item)
        for item in rewards
        if isinstance(item, dict)
        and item.get("claimed") is False
        and isinstance(item.get("round"), int)
        and item["round"] in {1, 2}
        and isinstance(item.get("activity_id"), int)
        and isinstance(item.get("rank"), int)
        and item["rank"] > 0
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (int(item["rank"]), -int(item["round"])),
    )


class MoyuChallengeTaskMixin:
    """Challenge both daily God Soul Boss rounds and claim the better rank."""

    def moyu_challenge_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        morning_start = _at(now, MORNING_TRIGGER)
        morning_end = _at(now, MORNING_CHALLENGE_DEADLINE)
        evening_start = _at(now, EVENING_TRIGGER)
        reward_end = _at(now, REWARD_DEADLINE)
        if morning_start <= now < morning_end or evening_start <= now < reward_end:
            return None
        next_time = next_moyu_challenge_time(now)
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "魔狱_挑战：当前不在挑战或晚间领奖窗口，未执行游戏操作",
            "next_time": next_time.strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    @staticmethod
    def _moyu_reward_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        # The live title is ``魔狱封阵奖励``.  The two round rows are verified
        # authoritatively by the strictly read-only reward snapshot immediately
        # after this GUI gate, so transient OCR of those rows must not keep the
        # page unrecognisable.
        return "魔狱封阵奖励" in compact or "魔狱奖励" in compact

    @staticmethod
    def _moyu_reward_claimed_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        return "1/1" in compact and "已领取" in compact

    @staticmethod
    def _moyu_reward_confirm_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        return "确认" in compact or "确定" in compact

    @staticmethod
    def _moyu_reward_claim_action_state(text: str) -> str | None:
        """Classify the live #466 claim button, preferring the terminal state."""
        compact = re.sub(r"\s+", "", str(text or ""))
        if "已领取" in compact:
            return "claimed"
        if "领取" in compact:
            return "claimable"
        return None

    def _moyu_open_activity(
        self,
        runtime: Any,
        payload: dict[str, Any] | None = None,
    ):
        options = dict(payload or {})
        yield from runtime.goto_view(34)
        yield from runtime.wait_click_then_view(34, "日常", 69)
        entry_result = yield from runtime.open_daily_entry(
            label="魔狱_挑战",
            title_pattern=r"魔狱|封阵",
            progress_can_mark_done=False,
            max_scrolls=30,
            initial_checks=2,
        )
        if entry_result != "open":
            raise RuntimeError("魔狱_挑战：#69 日常列表未找到“魔狱/封阵”入口")
        yield from runtime.wait_view_or_ocr(
            400,
            lambda text: (
                "大道外域" in re.sub(r"\s+", "", str(text or ""))
                and "魔狱封阵" in re.sub(r"\s+", "", str(text or ""))
            ),
            timeout=max(
                30.0,
                float(options.get("activity_entry_timeout_seconds") or 60.0),
            ),
            label="魔狱_挑战：等待大道外域 #400",
        )
        yield from runtime.wait_click_then_view(400, "封阵", 401)

    def _moyu_try_challenge(self, runtime: Any, payload: dict[str, Any]):
        preflight = read_godsoul_boss_challenge_snapshot()
        if preflight.get("complete") is True and preflight.get("settled") is True:
            return {
                "entered": False,
                "responded": True,
                "completed": True,
                "message": "Runtime 已确认本轮结算，未重复点击挑战",
                "runtime_snapshot": preflight,
            }

        # #401 reuses the same button for two activity states: before opening
        # it reads “报名”, after opening it reads “挑战”.  Inspect only the
        # annotated button region; full-frame OCR also contains “挑战要求” and
        # would therefore misclassify the “报名” state.
        action_shape = "报名"
        if hasattr(runtime, "ocr_text_in_shapes"):
            button_text = runtime.ocr_text_in_shapes(401, ("报名",), padding=12)
            if "挑战" in re.sub(r"\s+", "", str(button_text or "")):
                action_shape = "挑战"
        yield from runtime.wait_click(401, action_shape)
        try:
            landing = yield from runtime.wait_view(
                463,
                464,
                85,
                465,
                timeout=max(3.0, float(payload.get("entry_timeout_seconds") or 12.0)),
                label="魔狱_挑战：等待确认、战斗或结算响应",
            )
        except TimeoutError:
            snapshot = read_godsoul_boss_challenge_snapshot()
            return {
                "entered": False,
                "responded": False,
                "completed": bool(snapshot.get("settled")),
                "message": (
                    "点击挑战后无界面响应，判定本轮 Boss 已结束"
                    + ("；动态数据确认已有结算" if snapshot.get("settled") else "")
                ),
                "runtime_snapshot": snapshot,
            }

        landing_id = getattr(landing, "id", landing)
        battle_entered = landing_id in {85, 464}
        if landing_id == 463:
            yield from runtime.wait_click(463, "确认")
            try:
                # #401 remains visible briefly behind the confirmation while
                # the real battle #85 is still being created.  It is not a
                # valid post-confirm terminal and must never short-circuit the
                # battle wait.
                landing = yield from runtime.wait_view(
                    464,
                    85,
                    465,
                    timeout=30.0,
                    label="魔狱_挑战：确认后的战斗或结算响应",
                )
                landing_id = getattr(landing, "id", landing)
                battle_entered = landing_id in {85, 464}
            except TimeoutError:
                snapshot = read_godsoul_boss_challenge_snapshot()
                return {
                    "entered": True,
                    "responded": True,
                    "completed": bool(snapshot.get("settled")),
                    "message": "确认按钮已有响应；未识别到战斗页，拒绝重复挑战",
                    "runtime_snapshot": snapshot,
                }

        if battle_entered:
            try:
                landing = yield from runtime.wait_view(
                    465,
                    401,
                    timeout=max(
                        60.0,
                        float(payload.get("battle_timeout_seconds") or 1200.0),
                    ),
                    label="魔狱_挑战：等待战斗结束或返回活动页",
                )
                landing_id = getattr(landing, "id", landing)
            except TimeoutError:
                snapshot = read_godsoul_boss_challenge_snapshot()
                return {
                    "entered": True,
                    "responded": True,
                    "completed": bool(snapshot.get("settled")),
                    "message": "已进入战斗；等待结算超时，拒绝在本轮重复挑战",
                    "runtime_snapshot": snapshot,
                }

        if landing_id == 465:
            yield from runtime.wait_click_then_view(
                465,
                "继续",
                401,
                timeout=60.0,
                label="魔狱_挑战：结算后返回 #401",
            )
        snapshot = read_godsoul_boss_challenge_snapshot()
        return {
            "entered": True,
            "responded": True,
            "completed": bool(
                snapshot.get("settled")
                or landing_id == 465
                or (battle_entered and landing_id == 401)
            ),
            "message": (
                "挑战已有响应并完成结算"
                if (
                    snapshot.get("settled")
                    or landing_id == 465
                    or (battle_entered and landing_id == 401)
                )
                else "挑战已有响应，拒绝在本轮重复挑战"
            ),
            "runtime_snapshot": snapshot,
        }

    def _moyu_return_world(self, runtime: Any):
        current, _score, _frame = runtime.current_scene([401, 400, 34], update=True)
        if current == 401:
            runtime.click_shape_center(401, "返回")
            landing = yield from runtime.wait_view(
                400,
                34,
                timeout=30.0,
                label="魔狱_挑战：#401 返回后等待 #400/#34",
            )
            current = getattr(landing, "id", landing)
        if current == 400:
            runtime.click_shape_center(400, "返回")
            yield from runtime.wait_view(
                34,
                timeout=30.0,
                label="魔狱_挑战：#400 返回世界 #34",
            )
            return
        if current != 34:
            # 魔狱结算可能进入与 #314 全帧高度相似、但没有任何控件的
            # 魔道回城动画。复用 goto_view 已有的窄 transition guard：
            # 只在 #314 相似度达到 94% 时等待自然落到 #34，绝不把动画
            # 当 #314，也不执行通用 unknown 左下返回。
            ctx = getattr(runtime, "ctx", None)
            sentinel = object()
            previous_guard = (
                ctx.get("_go_scene_unknown_transition_guard", sentinel)
                if isinstance(ctx, dict)
                else sentinel
            )
            if isinstance(ctx, dict):
                ctx["_go_scene_unknown_transition_guard"] = {
                    "reference_scene_id": 314,
                    "similarity_threshold": 94.0,
                    "wait_seconds": 120.0,
                    "phase": "moyu_wait_mozu_world_transition",
                    "label": "魔狱结算回城动画",
                }
            try:
                yield from runtime.goto_view(34)
            finally:
                if isinstance(ctx, dict):
                    if previous_guard is sentinel:
                        ctx.pop("_go_scene_unknown_transition_guard", None)
                    else:
                        ctx["_go_scene_unknown_transition_guard"] = previous_guard

    def _moyu_claim_reward(self, runtime: Any, payload: dict[str, Any]):
        now = datetime.now()
        if now >= _at(now, REWARD_DEADLINE):
            return {
                "claimed": False,
                "already_claimed": False,
                "message": "已到 22:00，今日奖励窗口结束",
            }

        current, _score, _frame = runtime.current_scene([466, 401], update=True)
        if current not in {466, 401}:
            yield from self._moyu_open_activity(runtime, payload)
            current = 401
        if current == 401:
            yield from runtime.wait_click(401, "奖励")
            yield from runtime.wait_view(
                466,
                timeout=max(10.0, float(payload.get("reward_view_timeout_seconds") or 30.0)),
                label="魔狱_挑战：等待奖励页 #466 场景身份",
            )
        frame = runtime.cur_frame(update=True)
        reward_text = runtime.ocr_text(frame)
        if not self._moyu_reward_text(reward_text):
            raise RuntimeError(
                f"魔狱_挑战：#466 已识别但奖励标题 OCR 不一致：{reward_text}"
            )

        claim_action_text = runtime.ocr_text_in_shapes(
            466,
            ("领取",),
            padding=12,
            frame_data_url=frame,
        )
        claim_action_state = self._moyu_reward_claim_action_state(claim_action_text)
        if claim_action_state == "claimed":
            return {
                "claimed": False,
                "already_claimed": True,
                "message": (
                    f"#466[领取] OCR={claim_action_text!r}，状态已为“已领取”，未重复点击"
                ),
                "claim_action_text": claim_action_text,
                "rewards": [],
            }
        if claim_action_state != "claimable":
            raise RuntimeError(
                "魔狱_挑战：#466[领取] OCR 未能确认“领取/已领取”状态，拒绝点击："
                f"{claim_action_text!r}"
            )

        snapshot = read_godsoul_boss_reward_snapshot()
        if snapshot.get("complete") is not True:
            # The challenge transaction may already have consumed today's
            # non-idempotent opportunity.  Missing read-only ranking evidence
            # is therefore a deferred reward check, not a reason to replay the
            # whole activity every technical-retry minute.
            return {
                "claimed": False,
                "already_claimed": False,
                "deferred": True,
                "message": (
                    "两轮排名 Runtime 证据暂不可用，已保留奖励且拒绝猜测领取："
                    f"{snapshot.get('reason') or snapshot}"
                ),
                "runtime_snapshot": snapshot,
            }
        rewards = list(snapshot.get("rewards") or [])
        if snapshot.get("already_claimed") is True:
            return {
                "claimed": False,
                "already_claimed": True,
                "message": "今日排名奖励已领取",
                "rewards": rewards,
            }
        selected = select_moyu_reward(rewards)
        if selected is None:
            return {
                "claimed": False,
                "already_claimed": False,
                "message": "两轮均无可领取排名奖励",
                "rewards": rewards,
            }
        if datetime.now() >= _at(datetime.now(), REWARD_DEADLINE):
            return {
                "claimed": False,
                "already_claimed": False,
                "message": "排名读取完成时已到 22:00，未点击领取",
                "rewards": rewards,
            }

        selected_round = int(selected["round"])
        runtime.click_shape_center(466, f"第{selected_round}轮")
        yield from runtime.wait_action_settle(1.0)
        runtime.click_shape_center(466, "领取")
        with runtime.expect_views(539):
            try:
                yield from runtime.wait_view(
                    539,
                    timeout=8.0,
                    label="魔狱_挑战：等待排名奖励领取确认 #539",
                )
            except TimeoutError:
                final_snapshot = read_godsoul_boss_reward_snapshot()
                if not (
                    final_snapshot.get("complete") is True
                    and final_snapshot.get("already_claimed") is True
                ):
                    raise
                confirmed = False
            else:
                runtime.click_shape_center(539, "确认")
                confirmed = True
        if confirmed:
            yield from runtime.wait_action_settle(4.0)
        claimed_text = runtime.ocr_text(update=True)
        if not self._moyu_reward_claimed_text(claimed_text):
            final_snapshot = read_godsoul_boss_reward_snapshot()
            if final_snapshot.get("already_claimed") is not True:
                raise RuntimeError("魔狱_挑战：点击领取后未确认今日奖励变为已领取")
        return {
            "claimed": True,
            "already_claimed": False,
            "message": (
                f"选择第{selected_round}轮第{selected['rank']}名奖励并领取成功"
            ),
            "selected": selected,
            "rewards": rewards,
        }

    def _moyu_close_reward_and_return_world(self, runtime: Any):
        text = runtime.ocr_text(update=True)
        if self._moyu_reward_text(text):
            runtime.click_shape_center(466, "返回")
            landing = yield from runtime.wait_view(
                400,
                401,
                timeout=30.0,
                label="魔狱_挑战：奖励页返回 #400/#401",
            )
            if getattr(landing, "id", landing) == 401:
                runtime.click_shape_center(401, "返回")
                yield from runtime.wait_view(
                    400,
                    34,
                    timeout=30.0,
                    label="魔狱_挑战：#401 返回后等待 #400/#34",
                )
        yield from self._moyu_return_world(runtime)

    def moyu_challenge_flow(self, runtime: Any):
        payload = dict(getattr(runtime, "attrs", {}).get("payload") or {})
        now = datetime.now()
        morning_end = _at(now, MORNING_CHALLENGE_DEADLINE)
        evening_start = _at(now, EVENING_TRIGGER)
        evening_challenge_end = _at(now, EVENING_CHALLENGE_DEADLINE)
        reward_end = _at(now, REWARD_DEADLINE)
        is_morning = now < evening_start
        should_challenge = now < morning_end if is_morning else now < evening_challenge_end

        challenge = {
            "entered": False,
            "completed": False,
            "message": "已超过本轮 20 分钟挑战窗口，跳过挑战",
        }
        if should_challenge:
            yield from self._moyu_open_activity(runtime, payload)
            challenge = yield from self._moyu_try_challenge(runtime, payload)

        if (
            should_challenge
            and challenge.get("entered") is True
            and challenge.get("responded") is True
            and challenge.get("completed") is not True
        ):
            # A confirmed non-idempotent attempt may still be fighting or may
            # have an unrecognised settlement overlay.  Preserve that UI and
            # resume only after this challenge window has closed, so the next
            # run cannot click Challenge again or reopen the activity for a
            # reward from the wrong scene.
            if is_morning:
                next_time = evening_start
            else:
                next_time = max(
                    evening_challenge_end,
                    datetime.now() + timedelta(minutes=REWARD_EVIDENCE_RETRY_MINUTES),
                )
                next_time = min(next_time, reward_end - timedelta(minutes=1))
            runtime.set_next_time(next_time.strftime("%Y-%m-%d %H:%M:%S"))
            return {
                "result": "success",
                "current_scene": None,
                "message": (
                    f"魔狱_挑战：{challenge['message']}；"
                    "挑战终态尚未证明，已保留现场并延后领奖，未重新打开活动"
                ),
                "challenge": challenge,
                "reward_deferred": True,
            }

        if is_morning:
            returned_world = True
            try:
                yield from self._moyu_return_world(runtime)
            except (RuntimeError, TimeoutError):
                if not challenge.get("responded"):
                    raise
                returned_world = False
            next_time = _at(now, EVENING_TRIGGER)
            runtime.set_next_time(next_time.strftime("%Y-%m-%d %H:%M:%S"))
            return {
                "result": "success",
                "current_scene": 34 if returned_world else None,
                "message": (
                    f"魔狱_挑战：上午轮次{challenge['message']}，"
                    + ("已回到世界" if returned_world else "返回世界失败，保留现场且不重复挑战")
                ),
                "challenge": challenge,
            }

        reward = yield from self._moyu_claim_reward(runtime, payload)
        yield from self._moyu_close_reward_and_return_world(runtime)
        if reward.get("deferred") is True and datetime.now() < reward_end:
            next_time = min(
                datetime.now() + timedelta(minutes=REWARD_EVIDENCE_RETRY_MINUTES),
                reward_end - timedelta(minutes=1),
            )
        else:
            next_time = _at(now + timedelta(days=1), MORNING_TRIGGER)
        runtime.set_next_time(next_time.strftime("%Y-%m-%d %H:%M:%S"))
        return {
            "result": "success",
            "current_scene": 34,
            "message": (
                f"魔狱_挑战：晚间轮次{challenge['message']}；"
                f"{reward['message']}；已回到世界"
            ),
            "challenge": challenge,
            "reward": reward,
            "reward_window_open": datetime.now() < reward_end,
        }


__all__ = [
    "MoyuChallengeTaskMixin",
    "next_moyu_challenge_time",
    "select_moyu_reward",
]
