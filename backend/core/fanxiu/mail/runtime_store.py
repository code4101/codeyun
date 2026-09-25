from __future__ import annotations

from typing import Any, Callable


EngineGetter = Callable[[], Any]


def mail_database_engine() -> Any:
    """Resolve the mail persistence binding at use time, without eager ORM loading."""
    from backend.db import engine

    return engine


def _sqlmodel_mail_record() -> tuple[Any, Any, Any]:
    from sqlmodel import Session, select

    from backend.models import FanxiuMailRecord

    return Session, select, FanxiuMailRecord


def current_runtime_mail_sequence(engine_getter: EngineGetter) -> list[Any]:
    """Return the latest complete MailMgr sequence in its original UI order."""

    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.runtime_index.is_not(None),
                )
                .order_by(FanxiuMailRecord.runtime_index.asc())
            ).all()
        )


def current_runtime_mail_sequence_snapshot(engine_getter: EngineGetter) -> dict[str, Any]:
    """Build a validated alignment snapshot from the durable Runtime projection."""

    rows = current_runtime_mail_sequence(engine_getter)
    indices = [row.runtime_index for row in rows]
    fingerprints = {str(row.runtime_sequence_fingerprint or "") for row in rows}
    fingerprints.discard("")
    complete = indices == list(range(len(rows))) and len(fingerprints) <= 1
    return {
        "ok": complete,
        "complete": complete,
        "decoded_count": len(rows),
        "total": len(rows),
        "sequence_fingerprint": next(iter(fingerprints), ""),
        "items": [row.model_dump() for row in rows],
        "reason": "" if complete else "数据库中的动态邮件序号不连续或快照指纹不一致",
    }


__all__ = [
    "mail_database_engine",
    "current_runtime_mail_sequence",
    "current_runtime_mail_sequence_snapshot",
]
