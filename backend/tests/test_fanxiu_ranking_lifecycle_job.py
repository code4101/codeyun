from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, create_engine, select

import backend.core.fanxiu.activity.runtime_schedule as runtime_schedule
import backend.core.fanxiu.data_annotation.tasks.magic_invasion_initialization as magic_initialization
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


def _magic_occurrence() -> RankingOccurrence:
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


def _occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="server-tiandi",
        activity_id=8090004,
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


def _xutian_occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="xutian-palace",
        family="gameplay_rank",
        runtime_id="4080001400004",
        activity_id=4080001,
        start_at=datetime(2026, 8, 31, 10, tzinfo=TZ),
        end_at=datetime(2026, 9, 1, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 31, 0, tzinfo=TZ),
        close_at=datetime(2026, 9, 2, 23, 59, 59, tzinfo=TZ),
        cross_count=8,
    )


def _beast_occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="beast-abyss",
        family="gameplay_rank",
        runtime_id="8150001400004",
        activity_id=8150001,
        start_at=datetime(2026, 9, 3, 10, tzinfo=TZ),
        end_at=datetime(2026, 9, 3, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 9, 2, 0, tzinfo=TZ),
        close_at=datetime(2026, 9, 4, 23, 59, 59, tzinfo=TZ),
        cross_count=8,
    )


def test_beast_initialization_rnd_cell_runs_only_the_current_occurrence(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import beast_abyss_active

    monkeypatch.setattr(
        lifecycle_job,
        "job_now",
        lambda: datetime(2026, 9, 3, 10, 0, tzinfo=TZ),
    )
    monkeypatch.setattr(
        runtime_schedule,
        "read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True},
    )
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_beast_occurrence(),),
    )
    seen = []

    def execute(*_args, occurrence, **_kwargs):
        seen.append(occurrence.instance_key)
        if False:
            yield None
        return {"status": "completed", "phase": "initialization"}

    monkeypatch.setattr(
        beast_abyss_active,
        "execute_beast_abyss_initialization_checkpoint",
        execute,
    )

    result = _drain(lifecycle_job.execute_beast_abyss_initialization_rnd_cell(
        object(), {}, {}, Event()
    ))

    assert result["phase"] == "initialization"
    assert seen == [_beast_occurrence().instance_key]


def test_beast_initialization_rnd_cell_refuses_closed_window(monkeypatch):
    monkeypatch.setattr(
        lifecycle_job,
        "job_now",
        lambda: datetime(2026, 9, 3, 9, 59, tzinfo=TZ),
    )

    with pytest.raises(RuntimeError, match="10:00-21:30"):
        _drain(lifecycle_job.execute_beast_abyss_initialization_rnd_cell(
            object(), {}, {}, Event()
        ))


def test_beast_exchange_tail_rnd_cell_runs_only_the_unique_settlement_occurrence(monkeypatch):
    monkeypatch.setattr(
        lifecycle_job,
        "job_now",
        lambda: datetime(2026, 9, 4, 17, 0, tzinfo=TZ),
    )
    monkeypatch.setattr(
        runtime_schedule,
        "read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True},
    )
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_beast_occurrence(),),
    )
    seen = []

    def execute(*_args, occurrence, **_kwargs):
        seen.append(occurrence.instance_key)
        if False:
            yield None
        return {"status": "completed", "phase": "exchange_tail"}

    monkeypatch.setattr(lifecycle_job, "_execute_exchange_tail_checkpoint", execute)

    result = _drain(lifecycle_job.execute_beast_abyss_exchange_tail_rnd_cell(
        object(), {}, {}, Event()
    ))

    assert result["phase"] == "exchange_tail"
    assert seen == [_beast_occurrence().instance_key]


def test_exchange_tail_business_fact_is_independent_from_executor_acceptance() -> None:
    assert lifecycle_job.exchange_tail_executor_is_production("magic-invasion") is True
    assert lifecycle_job.exchange_tail_executor_is_production("beast-abyss") is True
    assert lifecycle_job.exchange_tail_executor_is_production("xutian-palace") is False


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


def test_job_records_daily_checkpoint_then_persists_wake(
    monkeypatch,
) -> None:
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
        ("ranking-lifecycle", datetime(2026, 8, 21, 5, 0, tzinfo=TZ))
    ]


@pytest.mark.parametrize("returned_error", [False, True])
def test_job_preserves_repeated_error_and_never_advances_wake(monkeypatch, returned_error):
    def fail(*_args, **_kwargs):
        if returned_error:
            return {"status": "error", "message": "static config unavailable"}
        raise RuntimeError("static config unavailable")

    engine = _arrange(monkeypatch, reconcile=fail)
    runner = _Runner()
    for _ in range(4):
        with pytest.raises(RuntimeError, match="static config unavailable"):
            _drain(lifecycle_job.execute_ranking_lifecycle_job(runner, {}, {}, Event()))
    with Session(engine) as session:
        row = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
        assert lifecycle_job.completed_ranking_checkpoint_keys(session) == set()
    assert row.status == "error"
    assert row.attempt_count == 4
    assert row.completed_at == row.retry_at == ""
    assert runner.next_times == []


def test_job_schedules_default_retry_for_first_blocked_business_checkpoint(
    monkeypatch,
) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "blocked",
            "message": "兑换宝阁本次未刷新",
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
        row = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
    assert row.status == "blocked"
    assert row.completed_at == ""
    assert row.retry_at == "2026-08-21T10:00:00+08:00"
    assert runner.next_times[-1] == (
        "ranking-lifecycle",
        datetime(2026, 8, 21, 5, tzinfo=TZ),
    )
    assert "待重试 1" in result["message"]


def test_job_defers_future_occurrence_to_its_start_instead_of_spinning(
    monkeypatch,
) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "blocked",
            "message": "活动尚未开始",
        },
    )
    future = RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="future-beast",
        activity_id=8090004,
        start_at=datetime(2026, 8, 21, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 22, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 21, 0, tzinfo=TZ),
        close_at=datetime(2026, 8, 23, 23, 59, 59, tzinfo=TZ),
        cross_count=8,
    )
    monkeypatch.setattr(
        lifecycle_job, "discover_ranking_occurrences", lambda _schedule: (future,)
    )
    runner = _Runner()

    _drain(lifecycle_job.execute_ranking_lifecycle_job(runner, {}, {}, Event()))

    with Session(engine) as session:
        row = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
    assert row.status == "blocked"
    assert row.retry_at == "2026-08-21T10:00:00+08:00"
    assert runner.next_times[-1][1] == datetime(2026, 8, 21, 5, tzinfo=TZ)


def test_business_waiting_does_not_imply_unavailability() -> None:
    occurrence = _occurrence()
    checkpoint = next(iter(lifecycle_job.due_ranking_checkpoints(
        (_occurrence(),),
        now=datetime(2026, 8, 21, 0, 30, tzinfo=TZ),
        completed_keys=set(),
    )))
    status, retry_at = lifecycle_job._default_retry_policy(
        status="blocked",
        checkpoint=checkpoint,
        occurrence=occurrence,
        now=datetime(2026, 8, 21, 10, tzinfo=TZ),
    )
    assert status == "blocked"
    assert retry_at == datetime(2026, 8, 21, 10, 10, tzinfo=TZ)


def test_job_reports_terminal_unavailable_separately_from_success(monkeypatch) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "unavailable", "message": "业务已关闭",
        },
    )
    runner = _Runner()

    result = _drain(lifecycle_job.execute_ranking_lifecycle_job(
        runner,
        {"scheduler_task_id": "ranking-lifecycle"},
        {},
        Event(),
    ))

    with Session(engine) as session:
        row = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
    assert row.status == "unavailable"
    assert result["result"] == "partial"
    assert result["successful_checkpoint_count"] == 0
    assert result["unavailable_checkpoint_count"] == 1
    assert "成功 0，待重试 0，不可用 1" in result["message"]


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
    occurrence = _magic_occurrence()

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


def test_gameplay_job_skips_unpromoted_magic_mail_checkpoint(monkeypatch) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "completed",
            "message": "静态档次已对齐",
        },
    )
    monkeypatch.setattr(
        lifecycle_job,
        "job_now",
        lambda: datetime(2026, 8, 21, 12, 0, tzinfo=TZ),
    )
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_magic_occurrence(),),
    )
    initialized = []

    def initialize(*_args, occurrence, **_kwargs):
        initialized.append(occurrence.instance_key)
        if False:
            yield None
        return {"status": "completed", "message": "魔道实例化完成"}

    monkeypatch.setattr(
        magic_initialization,
        "execute_magic_invasion_initialization_checkpoint",
        initialize,
    )
    seen = []

    def execute(*_args, checkpoint, **_kwargs):
        seen.append(checkpoint.key)
        if False:
            yield None
        return {"status": "completed", "message": "今日魔道邮件已处理"}

    monkeypatch.setattr(lifecycle_job, "_execute_magic_mail_checkpoint", execute)

    result = _drain(
        lifecycle_job.execute_ranking_lifecycle_job(
            _Runner(), {"scheduler_task_id": "ranking-lifecycle"}, {}, Event()
        )
    )

    with Session(engine) as session:
        rows = list(session.exec(select(FanxiuRankingLifecycleCheckpoint)).all())
    assert seen == []
    assert initialized == [_magic_occurrence().instance_key]
    assert len(rows) == 1
    assert rows[0].checkpoint_kind == "magic_initialization_0030"
    assert rows[0].status == "completed"
    assert result["result"] == "success"


def test_xutian_active_dispatches_the_unified_checkpoint(monkeypatch) -> None:
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
        return {"status": "pending", "retry_at": "2026-08-31T20:45:00+08:00"}

    monkeypatch.setattr(lifecycle_job, "_execute_xutian_active_checkpoint", execute)
    runner = _Runner()
    ctx = {"scheduler_task_id": "ranking-lifecycle"}
    payload = {"expected": "xutian"}
    stop_event = Event()
    occurrence = _xutian_occurrence()

    result = _drain(
        lifecycle_job._execute_xutian_active_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence
        )
    )

    assert result == {
        "status": "pending",
        "retry_at": "2026-08-31T20:45:00+08:00",
    }
    assert seen == {
        "runner": runner,
        "ctx": ctx,
        "payload": payload,
        "stop_event": stop_event,
        "occurrence": occurrence,
    }


def test_gameplay_job_does_not_execute_resource_sibling_when_gameplay_retries(
    monkeypatch,
) -> None:
    def reconcile(_session, occurrence, **_kwargs):
        if occurrence.runtime_id == "server-tiandi":
            raise RuntimeError("gameplay adapter unavailable")
        return {"status": "completed", "message": "资源榜静态事实已对齐"}

    engine = _arrange(monkeypatch, reconcile=reconcile)
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_occurrence(), _resource_occurrence()),
    )
    runner = _Runner()

    with pytest.raises(RuntimeError, match="gameplay adapter unavailable"):
        _drain(lifecycle_job.execute_ranking_lifecycle_job(runner, {}, {}, Event()))

    with Session(engine) as session:
        rows = list(
            session.exec(
                select(FanxiuRankingLifecycleCheckpoint).order_by(
                    FanxiuRankingLifecycleCheckpoint.runtime_id
                )
            ).all()
        )
    assert [(row.runtime_id, row.status) for row in rows] == [
        ("server-tiandi", "error"),
    ]
    assert rows[0].completed_at == rows[0].retry_at == ""
    assert runner.next_times == []


def test_resource_parent_executes_only_resource_family_and_owns_its_next_time(
    monkeypatch,
) -> None:
    engine = _arrange(
        monkeypatch,
        reconcile=lambda *_args, **_kwargs: {
            "status": "completed",
            "message": "资源榜静态事实已对齐",
        },
    )
    monkeypatch.setattr(
        lifecycle_job,
        "discover_ranking_occurrences",
        lambda _schedule: (_occurrence(), _resource_occurrence()),
    )
    runner = _Runner()

    result = _drain(
        lifecycle_job.execute_resource_ranking_job(
            runner,
            {"scheduler_task_id": "resource-ranking"},
            {},
            Event(),
        )
    )

    with Session(engine) as session:
        rows = list(session.exec(select(FanxiuRankingLifecycleCheckpoint)).all())
    assert [(row.family, row.runtime_id, row.status) for row in rows] == [
        ("resource_rank", "resource-lianti", "completed"),
    ]
    assert result["family"] == "resource_rank"
    assert runner.next_times == [
        ("resource-ranking", datetime(2026, 8, 21, 5, 0, tzinfo=TZ))
    ]
