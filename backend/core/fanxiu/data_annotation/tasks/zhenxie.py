from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any


_ZHENXIE_PARTICIPATION_SECONDS = 30.0


class ZhenxieTaskMixin:
    def daily_zhenxie_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = datetime.now()
        window_start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        window_end = now.replace(hour=21, minute=5, second=0, microsecond=0)
        if window_start <= now <= window_end:
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

    def _enter_daily_zhenxie(self, runtime: Any):
        """Enter the event from any valid timed-event landing scene."""

        scene_id, _score, _frame = runtime.current_scene(
            [63, 271, 272, 85, 34, 66],
            update=True,
        )
        current = scene_id
        if current not in {63, 271, 272, 85}:
            yield from runtime.goto_view(66)
            yield from runtime.wait_action_settle(3.0)
            current = self._zhenxie_scene_id(
                (
                    yield from runtime.wait_click_then_view(
                        66,
                        "前往",
                        63,
                        271,
                        272,
                        85,
                        timeout=20.0,
                    )
                )
            )

        if current == 63:
            yield from runtime.wait_click(63, "前往")
            yield from runtime.wait_action_settle(1.0)
            current = self._zhenxie_scene_id(
                (
                    yield from runtime.wait_scene(
                        271,
                        timeout=180.0,
                        layer0_wait_seconds=180.0,
                        label="日常_镇邪：#63[前往] 后等待 #271",
                    )
                )
            )
        if current == 271:
            current = self._zhenxie_scene_id(
                (
                    yield from runtime.wait_click_then_view(
                        271,
                        "参加",
                        272,
                        85,
                        timeout=20.0,
                    )
                )
            )
        if current == 272:
            yield from runtime.wait_click(272, "前往")
            return
        if current == 85:
            return
        raise RuntimeError(
            f"日常_镇邪：未能到达 #272/#85，当前 #{current if current is not None else 'unknown'}"
        )

    def _leave_daily_zhenxie(self, runtime: Any):
        """Consume nested activity/confirmation layers until #34 is real."""

        deadline = time.monotonic() + 135.0
        landing = yield from runtime.wait_view(
            34,
            85,
            186,
            86,
            272,
            271,
            timeout=90.0,
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
                raise RuntimeError("日常_镇邪：仍停在 #271 报名页，未参加且该页没有安全离场动作")
            if current in {85, 186}:
                runtime.click_shape(current, "离开")
                yield from runtime.wait_action_settle(2.0)
                landed = yield from runtime.wait_view(
                    34,
                    85,
                    186,
                    86,
                    timeout=max(1.0, deadline - time.monotonic()),
                    label="日常_镇邪：点击离开后重新识别多层落点",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            if current == 86:
                landed = yield from runtime.wait_click_then_view(
                    86,
                    "确认",
                    [34, 85, 186, 86],
                    timeout=max(1.0, deadline - time.monotonic()),
                    label="日常_镇邪：确认离场后重新识别多层落点",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            if current == 272:
                runtime.runner._click_generic_back(runtime.ctx)
                runtime.clear_frame()
                yield from runtime.wait_action_settle(2.0)
                landed = yield from runtime.goto_view(34)
                current = self._zhenxie_scene_id(landed)
                continue
            if current is None:
                landed = yield from runtime.wait_view(
                    34,
                    85,
                    186,
                    86,
                    timeout=max(1.0, deadline - time.monotonic()),
                    label="日常_镇邪：重新识别多层离场上下文",
                )
                current = self._zhenxie_scene_id(landed)
                continue
            raise RuntimeError(f"日常_镇邪：多层离场落点异常：#{current}")

        raise RuntimeError(f"日常_镇邪：多层离场动作次数耗尽，当前 #{current or 'unknown'}")

    def daily_zhenxie_flow(self, runtime: Any):
        now = datetime.now()
        window_start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        next_run_text = (window_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        yield from self._enter_daily_zhenxie(runtime)
        yield from runtime.wait_action_settle(_ZHENXIE_PARTICIPATION_SECONDS)
        current = yield from self._leave_daily_zhenxie(runtime)
        runtime.set_next_time(next_run_text)
        return {
            "result": "success",
            "message": "日常_镇邪：已参战并运行至少 30 秒，离开后返回主界面",
            "current_scene": current,
        }
