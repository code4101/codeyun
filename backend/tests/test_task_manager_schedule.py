import datetime as dt

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api import task_manager as task_manager_module
from backend.models import Task


def _parse_time(value: str) -> dt.datetime:
    text = value[:-1] if value.endswith("Z") else value
    return dt.datetime.fromisoformat(text)


def test_manual_interval_trigger_resets_next_run_at(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(task_manager_module, "engine", engine)

    now = dt.datetime.now().replace(microsecond=0)
    stale_next_run_at = (now + dt.timedelta(minutes=3)).isoformat()
    task = Task(
        id="publish-job",
        name="公网前端发布",
        command="echo publish",
        device_id="local",
        runtime_kind="job",
        schedule_policy={
            "enabled": True,
            "trigger": {"type": "interval", "minutes": 30},
        },
        schedule_state={"next_trigger_at": stale_next_run_at},
        next_run_at=stale_next_run_at,
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()

    manager = task_manager_module.TaskManager()
    try:
        manager._reset_interval_schedule_after_manual_trigger("publish-job")
    finally:
        manager.scheduler.shutdown(wait=False)

    with Session(engine) as session:
        updated = session.get(Task, "publish-job")
        assert updated is not None
        assert updated.next_run_at is not None
        delta = (_parse_time(updated.next_run_at) - now).total_seconds()
        assert 25 * 60 <= delta <= 35 * 60
        assert updated.schedule_state["next_trigger_at"] == updated.next_run_at
