from __future__ import annotations

from typing import Any, Callable


EngineGetter = Callable[[], Any]


def _sqlmodel_mail_record() -> tuple[Any, Any, Any]:
    from sqlmodel import Session, select

    from backend.models import FanxiuMailRecord

    return Session, select, FanxiuMailRecord


def trace_packet_mail_gap(
    engine_getter: EngineGetter,
    *,
    title: str,
    time_text: str,
    window_minutes: int = 8,
    max_sources: int = 24,
) -> dict[str, Any]:
    from backend.core.fanxiu.mail.packet_sync import trace_fanxiu_mail_packet_gap

    Session, _select, _mail_record = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return trace_fanxiu_mail_packet_gap(
            session,
            title=title,
            time_text=time_text,
            window_minutes=window_minutes,
            max_sources=max_sources,
        )


def pending_packet_mail_records(engine_getter: EngineGetter) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.locked == False,  # noqa: E712
                )
            ).all()
        )


def pending_packet_mail_action_candidates(engine_getter: EngineGetter, policies: set[str]) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord).where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.action_policy.in_(tuple(sorted(policies))),
                    FanxiuMailRecord.locked == False,  # noqa: E712
                    FanxiuMailRecord.status.not_in(("claimed", "deleted", "missing_from_list")),
                )
            ).all()
        )


def mark_packet_mail_record_missing_from_list(
    engine_getter: EngineGetter,
    record: Any,
    *,
    reason: str,
    marked_at: str,
) -> None:
    import time

    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    mail_key = str(getattr(record, "mail_key", "") or "").strip()
    if not mail_key:
        return
    with Session(engine_getter()) as session:
        current = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_key == mail_key)).first()
        if current is None:
            return
        evidence = dict(current.evidence or {})
        evidence.update(
            {
                "runtime_action": "missing_from_list",
                "missing_from_list_at": marked_at,
                "missing_from_list_reason": reason,
            }
        )
        current.status = "missing_from_list"
        current.action_policy = ""
        current.evidence = evidence
        current.updated_at = time.time()
        session.add(current)
        session.commit()


def packet_mail_records_same_title(engine_getter: EngineGetter, normalized_title: str, *, limit: int = 5) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source == "packet",
                    FanxiuMailRecord.normalized_title == normalized_title,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def packet_mail_records_same_time(engine_getter: EngineGetter, normalized_time: str, *, limit: int | None = None) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    query = (
        select(FanxiuMailRecord)
        .where(
            FanxiuMailRecord.source == "packet",
            FanxiuMailRecord.create_time_text == normalized_time,
        )
        .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    with Session(engine_getter()) as session:
        return list(session.exec(query).all())


def packet_mail_records_for_visible_row_exact(
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
                    FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
                    FanxiuMailRecord.normalized_title == normalized_title,
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
        )


def packet_mail_records_for_visible_row_same_time(engine_getter: EngineGetter, normalized_time: str) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
                    FanxiuMailRecord.create_time_text == normalized_time,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
            ).all()
        )


def packet_mail_records_by_normalized_title(engine_getter: EngineGetter, normalized_title: str, *, limit: int = 20) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(
                    FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
                    FanxiuMailRecord.normalized_title == normalized_title,
                )
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def recent_packet_mail_records(engine_getter: EngineGetter, *, limit: int = 200) -> list[Any]:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return list(
            session.exec(
                select(FanxiuMailRecord)
                .where(FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")))
                .order_by(FanxiuMailRecord.create_time_ms.desc(), FanxiuMailRecord.id.desc())
                .limit(limit)
            ).all()
        )


def find_packet_mail_record_exact(engine_getter: EngineGetter, *, normalized_title: str, normalized_time: str) -> Any | None:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return session.exec(
            select(FanxiuMailRecord).where(
                FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
                FanxiuMailRecord.normalized_title == normalized_title,
                FanxiuMailRecord.create_time_text == normalized_time,
            )
        ).first()


def find_packet_mail_record_by_raw_title(engine_getter: EngineGetter, *, title: str, normalized_time: str) -> Any | None:
    Session, select, FanxiuMailRecord = _sqlmodel_mail_record()
    with Session(engine_getter()) as session:
        return session.exec(
            select(FanxiuMailRecord).where(
                FanxiuMailRecord.source.in_(("packet", "packet_orphan_action")),
                FanxiuMailRecord.title == title,
                FanxiuMailRecord.create_time_text == normalized_time,
            )
        ).first()


def update_packet_mail_action(
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
