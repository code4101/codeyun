from __future__ import annotations

from typing import Any, Callable


EngineGetter = Callable[[], Any]


def _sqlmodel_mail_record() -> tuple[Any, Any, Any]:
    from sqlmodel import Session, select

    from backend.models import FanxiuMailRecord

    return Session, select, FanxiuMailRecord


def trace_runtime_mail_gap(
    engine_getter: EngineGetter,
    *,
    title: str,
    time_text: str,
    window_minutes: int = 8,
    max_sources: int = 24,
) -> dict[str, Any]:
    """Retained diagnostic shape; runtime-gap tracing has been retired."""

    del engine_getter, window_minutes, max_sources
    return {
        "ok": False,
        "available": False,
        "source": "runtime_memory",
        "reason": "runtime_gap_tracing_retired",
        "title": title,
        "time_text": time_text,
        "sources": [],
    }


def pending_runtime_mail_records(engine_getter: EngineGetter) -> list[Any]:
    """Compatibility name: current candidates now come only from MailMgr."""
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.locked == False,  # noqa: E712
                )
            ).all()
        )


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
    fingerprints = {
        str(row.runtime_sequence_fingerprint or "") for row in rows
    }
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


def pending_runtime_mail_action_candidates(engine_getter: EngineGetter, policies: set[str]) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.runtime_status == "unclaimed",
                    FanxiuMailRecord.action_policy.in_(tuple(sorted(policies))),
                    FanxiuMailRecord.locked == False,  # noqa: E712
                )
            ).all()
        )


def mark_runtime_mail_record_missing_from_list(
    engine_getter: EngineGetter,
    record: Any,
    *,
    reason: str,
    marked_at: str,
) -> None:
    # GUI absence is not authoritative. A complete MailMgr snapshot alone may
    # mark a record absent, so this legacy visual inference intentionally does nothing.
    del engine_getter, record, reason, marked_at


def runtime_mail_records_same_title(engine_getter: EngineGetter, normalized_title: str, *, limit: int = 5) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.normalized_title == normalized_title,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def runtime_mail_records_same_time(engine_getter: EngineGetter, normalized_time: str, *, limit: int | None = None) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    query = (
        select(FanxiuMailRecord)
        .where(
            FanxiuMailRecord.source == "runtime_memory",
            FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
            FanxiuMailRecord.create_time_text == normalized_time,
        )
        .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    with Session(engine_getter()) as session:
        return list(session.exec(query).all())


def runtime_mail_records_for_visible_row_exact(
    engine_getter: EngineGetter,
    *,
    normalized_title: str,
    normalized_time: str,
) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.normalized_title == normalized_title,
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
        )


def runtime_mail_records_for_visible_row_same_time(engine_getter: EngineGetter, normalized_time: str) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
        )


def runtime_mail_records_by_normalized_title(engine_getter: EngineGetter, normalized_title: str, *, limit: int = 20) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                    FanxiuMailRecord.normalized_title == normalized_title,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def recent_runtime_mail_records(engine_getter: EngineGetter, *, limit: int = 200) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "runtime_memory",
                    FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def find_runtime_mail_record_exact(engine_getter: EngineGetter, *, normalized_title: str, normalized_time: str) -> Any | None:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return session.exec(
            select(FanxiuMailRecord).where(
                FanxiuMailRecord.source == "runtime_memory",
                FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                FanxiuMailRecord.normalized_title == normalized_title,
                FanxiuMailRecord.create_time_text == normalized_time,
            )
        ).first()


def find_runtime_mail_record_by_raw_title(engine_getter: EngineGetter, *, title: str, normalized_time: str) -> Any | None:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return session.exec(
            select(FanxiuMailRecord).where(
                FanxiuMailRecord.source == "runtime_memory",
                FanxiuMailRecord.present_in_runtime == True,  # noqa: E712
                FanxiuMailRecord.title == title,
                FanxiuMailRecord.create_time_text == normalized_time,
            )
        ).first()


def update_runtime_mail_action(
    engine_getter: EngineGetter,
    *,
    mail_key: str,
    status: str,
    evidence: dict[str, Any],
) -> bool:
    from backend.core.fanxiu.mail.store import mark_fanxiu_mail_action

    Session, _select, _mail_record = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        updated = mark_fanxiu_mail_action(session, mail_key, status=status, evidence=evidence)
        session.commit()
        return updated


def align_runtime_mail_records_claimable_between_visible_neighbors(
    engine_getter: EngineGetter,
    *,
    newer_time_text: str,
    older_time_text: str,
    source: str = "visible_mail_adjacency",
    dry_run: bool = False,
) -> dict[str, Any]:
    from backend.core.fanxiu.mail.store import align_fanxiu_mail_records_claimable_between_times

    Session, _select, _mail_record = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        result = align_fanxiu_mail_records_claimable_between_times(
            session,
            newer_time_text=newer_time_text,
            older_time_text=older_time_text,
            source=source,
            dry_run=dry_run,
        )
        if not dry_run:
            session.commit()
        return result
