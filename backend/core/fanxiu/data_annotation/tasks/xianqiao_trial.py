from __future__ import annotations

import threading
from typing import Any


class XianqiaoTrialTaskMixin:
    """仙窍_试炼正式日常任务入口。"""

    def _execute_xianqiao_trial_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="xianqiao_trial",
            label="仙窍_试炼",
            flow=self.xianqiao_trial_flow,
        )

    def xianqiao_trial_flow(self, runtime: Any):
        """从 #34 进入玩法，消耗当天免费次数，再稳定回到 #34。"""

        payload = runtime.payload
        purchase_target = payload.get("target_daily_purchases")
        entry = yield from runtime.enter_xianqiao_trial(
            max_daily_scrolls=int(payload.get("max_daily_scrolls") or 30),
            settle_seconds=float(payload.get("settle_seconds") or 0.8),
        )
        daily = yield from runtime.run_xianqiao_trial_daily(
            target_daily_purchases=(
                int(purchase_target) if purchase_target is not None else 0
            ),
            max_challenges=int(payload.get("max_challenges") or 10),
            settle_seconds=float(payload.get("settle_seconds") or 0.8),
            battle_timeout=float(payload.get("battle_timeout") or 360.0),
        )
        runtime.set_next_time(self._next_daily_boss_reset_time_text())
        return {**daily, "entry": entry}
