from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

from sqlmodel import Session, create_engine, select

import backend.core.fanxiu.activity.runtime_schedule as runtime_schedule
import backend.core.fanxiu.data_annotation.tasks.ranking_lifecycle as lifecycle_job
import backend.db as backend_db
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.models import FanxiuRankingLifecycleCheckpoint


TZ = ZoneInfo("Asia/Shanghai")


class _Runner:
    def __init__(self) -> None:
        self.next_times: list[tuple[str, datetime]] = []
        self.logs: list[tuple[str, str]] = []

    def _persist_scheduler_task_next_time(self, task_id: str, next_time: datetime) -> None:
        self.next_times.append((task_id, next_time))

    def _log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="magic-invasion",
        family="gameplay_rank",
        runtime_id="server-magic",
        activity_id=700014,
        start_at=datetime(2026, 8, 21, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 21, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 21, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 22, 23, 59, 59, tzinfo=TZ),
        cross_count=1,
    )


def _resource_occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="lianti-faxiang",
        family="resource_rank",
        runtime_id="resource-lianti",
        activity_id=1043011,
        start_at=datetime(2026, 8, 21, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 22, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 21, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 23, 23, 59, 59, tzinfo=TZ),
        cross_count=1,
    )


def _arrange(monkeypatch, *, reconcile):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(backend_db, "engine", engine)
    monkeypatch.setattr(
        runtime_schedule,
        "read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True, "items": []},
    )
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_occurrence(),),
    )
    monkeypatch.setattr(
        lifecycle_job,
        "job_now",
        lambda: datetime(2026, 8, 21, 0, 30, tzinfo=TZ),
    )
    monkeypatch.setattr(lifecycle_job, "reconcile_ranking_occurrence", reconcile)
    return engine


def test_job_records_daily_checkpoint_and_owns_one_next_time(monkeypatch) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "completed",
            "message": "静态档次已对齐",
        },
    )
    runner = _Runner()

    result = _drain(
        lifecycle_job.execute_ranking_lifecycle_job(
            runner,
            {"scheduler_task_id": "ranking-lifecycle"},
            {},
            Event(),
        )
    )

    with Session(engine) as session:
        rows = list(session.exec(select(FanxiuRankingLifecycleCheckpoint)).all())
    assert [(row.checkpoint_kind, row.status) for row in rows] == [
        ("daily_reconcile", "completed")
    ]
    assert result["result"] == "success"
    assert runner.next_times == [
        ("ranking-lifecycle", datetime(2026, 8, 21, 19, tzinfo=TZ))
    ]


def test_job_isolates_checkpoint_error_and_schedules_retry(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("static config unavailable")

    engine = _arrange(monkeypatch, reconcile=fail)
    runner = _Runner()

    result = _drain(
        lifecycle_job.execute_ranking_lifecycle_job(
            runner,
            {"scheduler_task_id": "ranking-lifecycle"},
            {},
            Event(),
        )
    )

    with Session(engine) as session:
        row = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
    assert row.status == "error"
    assert row.retry_at == "2026-08-21T00:40:00+08:00"
    assert runner.next_times[0][1] == datetime(2026, 8, 21, 0, 40, tzinfo=TZ)
    assert result["result"] == "success"
    assert "待重试 1" in result["message"]


def test_magic_active_dispatches_the_compound_checkpoint(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion_compound

    seen = {}

    def execute(runner, ctx, payload, stop_event, *, occurrence):
        seen.update(
            runner=runner,
            ctx=ctx,
            payload=payload,
            stop_event=stop_event,
            occurrence=occurrence,
        )
        if False:
            yield None
        return {"status": "completed", "message": "3×500 complete"}

    monkeypatch.setattr(
        magic_invasion_compound,
        "execute_magic_invasion_compound_checkpoint",
        execute,
    )
    runner = _Runner()
    ctx = {"scheduler_task_id": "ranking-lifecycle"}
    payload = {"expected": "cross"}
    stop_event = Event()
    occurrence = _occurrence()

    result = _drain(
        lifecycle_job._execute_magic_active_checkpoint(
            runner,
            ctx,
            payload,
            stop_event,
            occurrence=occurrence,
        )
    )

    assert result == {"status": "completed", "message": "3×500 complete"}
    assert seen == {
        "runner": runner,
        "ctx": ctx,
        "payload": payload,
        "stop_event": stop_event,
        "occurrence": occurrence,
    }


def test_job_commits_successful_sibling_when_one_occurrence_needs_retry(
    monkeypatch,
) -> None:
    def reconcile(_session, occurrence, **_kwargs):
        if occurrence.runtime_id == "server-magic":
            raise RuntimeError("gameplay adapter unavailable")
        return {"status": "completed", "message": "资源榜静态事实已对齐"}

    engine = _arrange(monkeypatch, reconcile=reconcile)
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_occurrence(), _resource_occurrence()),
    )
    runner = _Runner()

    result = _drain(
        lifecycle_job.execute_ranking_lifecycle_job(
            runner,
            {"scheduler_task_id": "ranking-lifecycle"},
            {},
            Event(),
        )
    )

    with Session(engine) as session:
        rows = list(
            session.exec(
                select(FanxiuRankingLifecycleCheckpoint).order_by(
                    FanxiuRankingLifecycleCheckpoint.runtime_id
                )
            ).all()
        )
    assert [(row.runtime_id, row.status) for row in rows] == [
        ("resource-lianti", "completed"),
        ("server-magic", "error"),
    ]
    assert rows[0].completed_at
    assert rows[1].retry_at == "2026-08-21T00:40:00+08:00"
    assert result["result"] == "success"
    assert "成功 1，待重试 1" in result["message"]
    assert runner.next_times == [
        ("ranking-lifecycle", datetime(2026, 8, 21, 0, 40, tzinfo=TZ))
    ]
