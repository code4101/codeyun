"""星图笔记每日整理：规则集中声明，作业统一调度并记录各规则修改数。"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from sqlalchemy import func, update
from sqlmodel import Session, select

from backend.models import NoteNode

NOTE_MAINTENANCE_TASK_KEY = "note_daily_maintenance"
NOTE_MAINTENANCE_JOB_TYPE = "notes.daily-maintenance"


@dataclass(frozen=True)
class MinimumPrivacyRule:
    key: str
    title_condition: Callable[[], Any]
    minimum: int = 1


# 新增标题隐私规则只需扩展此表；其他状态整理可在入口中增加独立步骤。
PRIVACY_RULES = (
    MinimumPrivacyRule("dash_title", lambda: NoteNode.title == "-"),
)


def maintain_notes(session: Session) -> dict[str, Any]:
    """整理所有用户的未删除笔记，只提升隐私并保留操作历史。

    一次事务提交；条件更新防止覆盖扫描后发生的人工修改。
    并发改过的笔记留待下一次整理，重复执行不会刷新未变更笔记的时间。
    """
    counts = {}
    from backend.plugins.extensions import plugin_values

    rules = (*PRIVACY_RULES, *(rule for group in plugin_values("note_privacy_rules") for rule in group))
    for rule in rules:
        condition = (
            NoteNode.deleted_at.is_(None),
            NoteNode.private_level < rule.minimum,
            rule.title_condition(),
        )
        rows = session.exec(select(
            NoteNode.id, NoteNode.updated_at, NoteNode.history,
        ).where(*condition)).all()
        changed = 0
        for note_id, updated_at, history in rows:
            now = time.time()
            result = session.execute(
                update(NoteNode).where(
                    *condition, NoteNode.id == note_id, NoteNode.updated_at == updated_at,
                ).values(
                    private_level=rule.minimum,
                    updated_at=now,
                    history=[*(history or []), {"ts": int(now), "f": "p", "v": rule.minimum}],
                ).execution_options(synchronize_session=False)
            )
            changed += result.rowcount
        counts[rule.key] = changed
    session.commit()
    return {"updated_count": sum(counts.values()), "rules": counts}


def run_note_maintenance() -> dict[str, Any]:
    """每日作业和手动执行共用的公开入口。"""
    from backend.db import engine

    with Session(engine) as session:
        return maintain_notes(session)


def enqueue_note_maintenance() -> str:
    from backend.core.jobs.local_runtime import submit_local_job_once

    run, _ = submit_local_job_once(job_type=NOTE_MAINTENANCE_JOB_TYPE, payload={})
    return run.id
