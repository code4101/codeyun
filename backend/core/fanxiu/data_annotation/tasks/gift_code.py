from __future__ import annotations

import threading
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable

from backend.core.fanxiu.gift_code_crawler import crawl_weekly_gift_codes


WEEKLY_GIFT_CODE_TASK_ID = "gift-code-weekly"
WEEKLY_GIFT_CODE_WEEKDAY = 0  # Python weekday: Monday
WEEKLY_GIFT_CODE_TRIGGER_TIME = dt_time(23, 30)


def _now() -> datetime:
    return datetime.now()


def next_weekly_gift_code_trigger_at(now: datetime) -> datetime:
    """计算本次正常完成后的下周一 23:30。

    同一周周一无论几点完成，都推进到七天后的周一；其它日期推进到紧接着的
    下一个周一。

    :param datetime now: 本次 Job 正常完成时间。
    :return datetime: 下一周期的绝对触发时间。
    """

    days_until_monday = (WEEKLY_GIFT_CODE_WEEKDAY - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return datetime.combine(
        now.date() + timedelta(days=days_until_monday),
        WEEKLY_GIFT_CODE_TRIGGER_TIME,
    )


class GiftCodeTaskMixin:
    """执行“每周_礼包码”的调度外壳与游戏兑换动作。"""

    @staticmethod
    def _gift_page_text_ready(text: str) -> bool:
        """用兑换窗口自身文案确认页面，避免被通用提示场景抢先识别。"""

        normalized = "".join(str(text or "").split())
        return (
            ("点击输入兑换码" in normalized or "请输入正确的兑换码" in normalized)
            and "兑换" in normalized
        )

    @staticmethod
    def _gift_input_confirm_point(
        fragments: list[dict[str, Any]],
        *,
        frame_width: float,
        frame_height: float,
    ) -> tuple[float, float]:
        """定位文本输入覆盖层右下方唯一的“确定”按钮。"""

        candidates: list[tuple[float, float]] = []
        for item in fragments:
            text = "".join(str(item.get("text") or "").split())
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            w = float(item.get("w") or 0)
            h = float(item.get("h") or 0)
            center_x = x + w / 2
            center_y = y + h / 2
            if (
                text == "确定"
                and center_x >= frame_width * 0.7
                and center_y >= frame_height * 0.85
            ):
                candidates.append((center_x, center_y))
        if len(candidates) != 1:
            raise RuntimeError(f"礼包码输入：右下角‘确定’匹配到 {len(candidates)} 项，停止点击")
        return candidates[0]

    def _record_weekly_gift_code_done(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        next_time = next_weekly_gift_code_trigger_at(now or _now()).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or WEEKLY_GIFT_CODE_TASK_ID),
            next_time,
        )
        return next_time

    def _fetch_weekly_gift_codes(self, stop_event: threading.Event) -> list[str]:
        result = crawl_weekly_gift_codes(
            check_cancel=lambda: self._raise_if_stopped(stop_event),
        )
        self._log(
            "info",
            f"每周_礼包码：论坛正文 {result.text_length} 字，解析到 {len(result.codes)} 个兑换码",
        )
        return list(result.codes)

    def _execute_weekly_gift_code_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从论坛获取本周礼包码，依次兑换并推进到下周一 23:30。"""

        payload = dict(payload or {})
        raw_codes = payload.get("codes")
        codes: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_codes, list):
            for raw_code in raw_codes:
                code = str(raw_code or "").strip()
                if code and code not in seen:
                    codes.append(code)
                    seen.add(code)
        if not codes:
            codes = self._fetch_weekly_gift_codes(stop_event)
        if not codes:
            raise RuntimeError("每周_礼包码：论坛未返回任何兑换码，不能按成功完成")

        completion: dict[str, str] = {}

        def record_codes_processed() -> str:
            """Persist the business completion time once, before best-effort departure."""

            if "next_time" not in completion:
                completion["next_time"] = self._record_weekly_gift_code_done(payload)
            return completion["next_time"]

        redeem_flow = self._execute_gift_code_task(
            ctx,
            codes,
            stop_event,
            on_codes_processed=record_codes_processed,
        )
        redeem_result: dict[str, Any] = {}
        if isinstance(redeem_flow, GeneratorType):
            returned = yield from redeem_flow
            if isinstance(returned, dict):
                redeem_result = returned
        elif isinstance(redeem_flow, dict):
            redeem_result = redeem_flow

        # Compatibility for a custom executor that returns success without
        # invoking the completion callback. The production executor records it
        # immediately after the last code, before attempting to leave #49.
        next_time = record_codes_processed()
        departure_warning = str(redeem_result.get("departure_warning") or "").strip()
        message = f"每周_礼包码：已完成 {len(codes)} 个兑换码，下次 {next_time}"
        if departure_warning:
            message = f"{message}；{departure_warning}"
        self._log("success", message)
        return {
            "result": "success",
            "message": message,
            "current_scene": int(redeem_result.get("current_scene") or 34),
            "code_count": len(codes),
            **({"departure_warning": departure_warning} if departure_warning else {}),
        }

    def _execute_gift_code_task(
        self,
        ctx: dict[str, Any],
        codes: list[str],
        stop_event: threading.Event,
        *,
        on_codes_processed: Callable[[], str] | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少每周_礼包码资产树路径，无法打开设置页")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "对齐 #49 设置页", phase="align_settings")
        yield from self._open_settings_page(runtime)
        return (
            yield from self._redeem_gift_codes_from_settings(
                ctx,
                runtime,
                codes,
                stop_event,
                on_codes_processed=on_codes_processed,
            )
        )

    def _redeem_gift_codes_from_settings(
        self,
        ctx: dict[str, Any],
        runtime: Any,
        codes: list[str],
        stop_event: threading.Event,
        *,
        on_codes_processed: Callable[[], str] | None = None,
    ) -> dict[str, Any]:
        """从已确认的 #49 兑换一批礼包码，再尽力安全回到 #34。"""

        for index, code in enumerate(codes):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"处理第 {index + 1}/{len(codes)} 个：{code}",
                    current_index=index,
                    current_code=code,
                    phase="process_code",
                )
                self._log_locked("action", f"开始兑换：{code}")
            self._process_code(ctx, code, index == len(codes) - 1, stop_event)

        # From this point onward every code has already been submitted. Persist
        # the next business trigger before the unrelated departure side effect,
        # otherwise a missing #49 -> #34 route would make Scheduler repeat the
        # whole batch.
        if on_codes_processed is not None:
            on_codes_processed()
        with self._lock:
            self._set_status_locked("running", "礼包码处理完成，返回 #34", phase="finish_back")
        try:
            yield from self._leave_settings_page(runtime)
        except InterruptedError:
            # Cell cancellation is control flow, not a recoverable business
            # departure failure. GeneratorExit/KeyboardInterrupt likewise do
            # not derive from Exception and continue to propagate naturally.
            raise
        except Exception as exc:
            if on_codes_processed is None:
                raise
            warning = f"兑换已完成，但离场失败，最后确认场景 #49：{type(exc).__name__}: {exc}"
            with self._lock:
                self._log_locked("warning", warning)
            return {
                "result": "success",
                "message": f"已处理 {len(codes)} 个礼包码；{warning}",
                "current_scene": 49,
                "code_count": len(codes),
                "departure_warning": warning,
            }
        return {
            "result": "success",
            "message": f"已处理 {len(codes)} 个礼包码并返回世界",
            "current_scene": 34,
            "code_count": len(codes),
        }
