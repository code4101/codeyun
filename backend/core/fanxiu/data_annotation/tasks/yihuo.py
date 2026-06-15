from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.autogui import View, image_number as _runtime_image_number


class DailyYihuoTaskMixin:
    def _execute_daily_yihuo_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_异火资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        recovered_local_page = yield from self._recover_daily_yihuo_local_pages(runtime, stop_event)
        if recovered_local_page:
            yield from runtime.wait_view(
                34,
                timeout=self._daily_yihuo_timeout(payload, "world_return_timeout", 20.0),
                label="日常_异火：局部页收尾后等待回到 #34 世界",
            )
            self._finish_daily_yihuo(payload, "日常_异火已从局部页返回世界")
            return "success"

        yield from self._run_daily_yihuo_flow(runtime, payload)
        yield from self._wait_runtime_action_settle(
            ctx,
            stop_event,
            seconds=self._daily_yihuo_timeout(payload, "yihuo_settle_seconds", 1.5),
        )
        self._finish_daily_yihuo(payload, "日常_异火完成，已回到世界")
        return "success"

    def _run_daily_yihuo_flow(self, runtime: Any, payload: dict[str, Any]):
        timeout = self._daily_yihuo_timeout

        yield from runtime.goto_view(34)
        yield from runtime.wait_click(34, "下方菜单/星海", timeout=timeout(payload, "yihuo_click_timeout", 18.0))
        yield from runtime.wait_click(259, "异火", timeout=timeout(payload, "yihuo_page_timeout", 12.0))
        yield from runtime.wait_click(260, "净莲", timeout=timeout(payload, "jinglian_page_timeout", 12.0))

        if (yield from self._wait_daily_yihuo_box_state(runtime, payload)) == "claimable":
            yield from runtime.wait_click(261, "箱子", timeout=timeout(payload, "box_click_timeout", 12.0))

        yield from runtime.wait_click(261, "返回", timeout=timeout(payload, "box_return_timeout", 12.0))
        yield from runtime.wait_click(260, "返回", timeout=timeout(payload, "jinglian_return_timeout", 12.0))

        if (yield from self._wait_daily_yihuo_return_state(runtime, payload)) == "yihuo_back":
            yield from runtime.wait_click(259, "返回", timeout=timeout(payload, "world_return_timeout", 20.0))
        yield from runtime.wait_view(
            34,
            timeout=timeout(payload, "world_return_timeout", 20.0),
            label="日常_异火：等待回到 #34 世界",
        )

    def _wait_daily_yihuo_box_state(self, runtime: Any, payload: dict[str, Any]):
        return (
            yield from runtime.wait_any(
                {
                    "claimable": runtime.shape_visible(261, "箱子"),
                    "claimed": runtime.all_of(
                        runtime.shape_visible(261, "返回"),
                        runtime.ocr_contains(all_of=("已领取",), any_of=("次日5点刷新", "净莲妖火"), label="异火已领取文本"),
                        label="#261 已领取态",
                    ),
                },
                timeout=self._daily_yihuo_timeout(payload, "box_page_timeout", 12.0),
                label="日常_异火：等待 #261 箱子或已领取态",
            )
        )

    def _wait_daily_yihuo_return_state(self, runtime: Any, payload: dict[str, Any]):
        return (
            yield from runtime.wait_any(
                {
                    "world": runtime.view_visible(34),
                    "yihuo_back": runtime.shape_visible(259, "返回"),
                },
                timeout=self._daily_yihuo_timeout(payload, "yihuo_return_timeout", 12.0),
                label="日常_异火：等待 #34 世界或 #259 返回",
            )
        )

    def _daily_yihuo_timeout(self, payload: dict[str, Any], key: str, default: float) -> float:
        return float(payload.get(key) or default)

    def _finish_daily_yihuo(self, payload: dict[str, Any], message: str) -> None:
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-yihuo")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_yihuo",
            label="日常_异火",
            last_result="success",
        )
        with self._lock:
            self._set_status_locked(
                "success",
                f"{message}，下次 {next_time}",
                phase="daily_yihuo_done",
                current_scene=34,
            )
            self._log_locked("success", self._status["message"])

    def _daily_yihuo_view_image(self, runtime: Any, view_id: int) -> dict[str, Any]:
        view = runtime.get_view(view_id)
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError(f"日常_异火：缺少 #{view_id} 标注")
        return view.raw

    def _recover_daily_yihuo_local_pages(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        threshold: float = 80.0,
    ):
        ctx = runtime.ctx
        image259 = self._daily_yihuo_view_image(runtime, 259)
        image260 = self._daily_yihuo_view_image(runtime, 260)
        image261 = self._daily_yihuo_view_image(runtime, 261)
        pages: list[tuple[dict[str, Any], str]] = [
            (image261, "箱子"),
            (image260, "净莲"),
            (image259, "异火"),
        ]
        recovered = False
        for _ in range(3):
            if stop_event.is_set():
                raise InterruptedError()
            frame = self._screencap(ctx)
            matched: tuple[dict[str, Any], dict[str, Any], float] | None = None
            for image, identity_title in pages:
                identity_shape = self._find_shape(image, identity_title)
                if identity_shape is None:
                    continue
                score = float(self._shape_score(ctx, image, identity_shape, frame) or 0.0)
                if score >= threshold:
                    matched = (image, identity_shape, score)
                    break
            if matched is None:
                return recovered
            recovered = True
            image, _identity_shape, score = matched
            image_id = image.get("id") or _runtime_image_number(image) or "?"
            return_shape = self._find_shape(image, "返回")
            if return_shape is None:
                raise RuntimeError(f"日常_异火：检测到 #{image_id} 局部页 {score:.0f}%，但缺少「返回」标注")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_异火：从残留局部页 #{image_id} 点击「返回」",
                    phase="daily_yihuo_recover_local_page",
                    current_scene=image_id if isinstance(image_id, int) else None,
                )
                self._log_locked("action", self._status["message"])
            self._click_shape(ctx, image, return_shape, frame)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
        return recovered

