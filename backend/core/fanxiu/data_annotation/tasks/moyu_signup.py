from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


class MoyuSignupTaskMixin:
    """执行“魔狱_报名”的幂等闭环。"""

    @staticmethod
    def _next_moyu_signup_time(now: datetime | None = None) -> datetime:
        current = now or datetime.now()
        morning = current.replace(hour=5, minute=0, second=0, microsecond=0)
        afternoon = current.replace(hour=14, minute=0, second=0, microsecond=0)
        if current < morning:
            return morning
        if current < afternoon:
            return afternoon
        return morning + timedelta(days=1)

    def moyu_signup_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = datetime.now()
        window_start = now.replace(hour=5, minute=0, second=0, microsecond=0)
        window_end = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if window_start <= now < window_end:
            return None
        next_time = self._next_moyu_signup_time(now)
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "魔狱_报名：当前不在 05:00-18:00 报名窗口，未执行游戏操作",
            "next_time": next_time.strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    @staticmethod
    def _moyu_signup_text_is_signed(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        return "已报名" in compact or (
            "魔狱封阵" in compact and "挑战" in compact
        )

    def moyu_signup_flow(self, runtime: Any):
        yield from runtime.wait_click_then_view(34, "日常", 69)
        entry_result = yield from runtime.open_daily_entry(
            label="魔狱_报名",
            title_pattern=r"魔狱|封阵",
            progress_can_mark_done=False,
            max_scrolls=30,
            initial_checks=2,
        )
        if entry_result != "open":
            raise RuntimeError("魔狱_报名：#69 日常列表未找到“魔狱/封阵”入口")

        payload = getattr(runtime, "attrs", {}).get("payload", {})
        yield from runtime.wait_view_or_ocr(
            400,
            lambda text: (
                "大道外域" in re.sub(r"\s+", "", str(text or ""))
                and "魔狱封阵" in re.sub(r"\s+", "", str(text or ""))
            ),
            timeout=float(payload.get("moyu_entry_travel_timeout") or 180.0),
            label="魔狱_报名：等待自动寻路抵达大道外域 #400",
        )
        yield from runtime.wait_click_then_view(400, "封阵", 401)

        text = runtime.ocr_text(update=True)
        already_signed = self._moyu_signup_text_is_signed(text)
        if not already_signed:
            yield from runtime.wait_click(401, "报名")
            yield from runtime.wait_any(
                {
                    "已报名": runtime.ocr_matches(
                        self._moyu_signup_text_is_signed,
                        label="魔狱_报名：报名结果 OCR",
                    )
                },
                label="魔狱_报名：确认报名成功",
            )

        yield from runtime.wait_click_then_view(401, "返回", 400)
        yield from runtime.wait_click_then_view(400, "返回", 34)
        next_time = self._next_moyu_signup_time()
        runtime.set_next_time(next_time.strftime("%Y-%m-%d %H:%M:%S"))
        return {
            "result": "success",
            "current_scene": 34,
            "message": "魔狱_报名：已报名并回到世界" if not already_signed else "魔狱_报名：本轮已报名，已回到世界",
            "already_signed": already_signed,
        }
