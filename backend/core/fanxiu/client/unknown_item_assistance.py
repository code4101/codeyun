from __future__ import annotations

"""White-listed Codex escalation for genuinely unresolved mail reward items."""

import hashlib
import json
import time
from typing import Any

from sqlmodel import Session

from backend.core.codex import CodexEscalationRequest
from backend.core.fanxiu.data_annotation.ai_assistance import request_ai_assistance
from backend.models import AppSetting


FANXIU_UNKNOWN_ITEM_ASSISTANCE_SETTING_KEY = "fanxiu.unknown_item_assistance.latest"
FANXIU_UNKNOWN_ITEM_ASSISTANCE_COOLDOWN_SECONDS = 6 * 3600


def _signature(evidence: list[dict[str, Any]]) -> str:
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _save_state(state: dict[str, Any], db_bind: Any | None = None) -> None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine
    with Session(db_bind) as session:
        row = session.get(AppSetting, FANXIU_UNKNOWN_ITEM_ASSISTANCE_SETTING_KEY)
        if row is None:
            row = AppSetting(key=FANXIU_UNKNOWN_ITEM_ASSISTANCE_SETTING_KEY)
        row.value = {"latest_run": state}
        row.updated_at = time.time()
        session.add(row)
        session.commit()


def _load_state(db_bind: Any | None = None) -> dict[str, Any] | None:
    if db_bind is None:
        from backend.db import engine

        db_bind = engine
    with Session(db_bind) as session:
        row = session.get(AppSetting, FANXIU_UNKNOWN_ITEM_ASSISTANCE_SETTING_KEY)
        value = row.value if row and isinstance(row.value, dict) else {}
        latest = value.get("latest_run") if isinstance(value, dict) else None
        return dict(latest) if isinstance(latest, dict) else None


def _prompt(evidence: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "请作为 CodeYun 凡修后台工程代理，解决邮件奖励出现真正未知 Item 的解析缺口。",
            "",
            "任务边界：",
            "1. 先读仓库 AGENTS.md、fanxiu skill 和凡修领域文档，再定位现有邮件 Runtime/静态图鉴解析链。",
            "2. 只读调查游戏 Runtime、逆向导出和现有数据；禁止点击游戏、领取或删除邮件、修改调度状态。",
            "3. 不得把未知道具直接视为可领，不得靠标题猜测；只有权威配置或可验证结构能进入业务策略。",
            "4. 若能确定解析机制，直接改进 CodeYun 正式实现并补聚焦测试；不要写一次性生产旁路，不要 git commit。",
            "5. 若证据仍不足，保持失败关闭，只输出缺少的最小证据和下一步工程探针。",
            "6. 完成后简洁说明根因、改动、测试及是否已能让原邮件幂等重试通过。",
            "",
            "本次最小未知证据：",
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        ]
    )


def run_fanxiu_unknown_item_assistance(
    evidence: list[dict[str, Any]],
    *,
    signature: str | None = None,
    db_bind: Any | None = None,
) -> dict[str, Any]:
    signature = signature or _signature(evidence)
    state = {
        "signature": signature,
        "status": "running",
        "evidence": evidence,
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    _save_state(state, db_bind)
    try:
        dispatch = request_ai_assistance(CodexEscalationRequest(
            title=f"凡修邮件未知奖励：{signature}",
            problem=_prompt(evidence),
            objective="修复未知奖励解析，保持邮件领取安全门禁，通过正式解析入口验证。",
            constraints=("禁止点击游戏、领取或删除邮件、修改调度状态。",),
            completion_criteria=("权威配置或可验证结构能解释道具身份；证据不足则保留失败状态。",),
        ))
    except Exception as exc:
        state.update(
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
            finished_at=time.time(),
            updated_at=time.time(),
        )
        _save_state(state, db_bind)
        raise
    state.update(
        status="dispatched" if dispatch else "suppressed",
        dispatch=dispatch.model_dump() if dispatch else None,
        updated_at=time.time(),
    )
    _save_state(state, db_bind)
    return state


def enqueue_fanxiu_unknown_item_assistance(
    evidence: list[dict[str, Any]],
    *,
    db_bind: Any | None = None,
) -> dict[str, Any]:
    """Deduplicate one unknown signature and enqueue its Codex investigation."""

    normalized = [dict(item) for item in evidence if isinstance(item, dict)]
    signature = _signature(normalized)
    latest = _load_state(db_bind)
    now = time.time()
    if (
        latest
        and latest.get("status") != "suppressed"
        and latest.get("signature") == signature
        and now - float(latest.get("updated_at") or 0) < FANXIU_UNKNOWN_ITEM_ASSISTANCE_COOLDOWN_SECONDS
    ):
        return {
            "queued": False,
            "deduplicated": True,
            "signature": signature,
            "task_id": latest.get("task_id"),
            "status": latest.get("status"),
        }

    from backend.core.jobs.executor import background_task_queue

    task_name = f"fanxiu_unknown_item:{signature}"
    # Persist the deduplication marker before starting the queue worker.  The
    # worker may begin immediately and write ``running``; a post-enqueue write
    # here would race and could incorrectly move it back to ``queued``.
    requested_state = {
        "signature": signature,
        "status": "requested",
        "evidence": normalized,
        "requested_at": now,
        "updated_at": now,
    }
    _save_state(requested_state, db_bind)
    task_id, queued = background_task_queue.enqueue_once(
        task_name,
        run_fanxiu_unknown_item_assistance,
        normalized,
        signature=signature,
        db_bind=db_bind,
        metadata={"signature": signature, "unknown_count": len(normalized)},
        resource_lock="resource:codex-cli",
    )
    return {
        "queued": queued,
        "deduplicated": not queued,
        "signature": signature,
        "task_id": task_id,
        "status": "queued" if queued else "running",
    }


__all__ = [
    "enqueue_fanxiu_unknown_item_assistance",
    "run_fanxiu_unknown_item_assistance",
]
