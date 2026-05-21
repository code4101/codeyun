import time
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, text

from backend.api import task_manager as task_manager_module
from backend.migrations.manager import (
    run_startup_schema_repairs,
    v55_migrate_documentasset_primary_key_to_numeric,
)
from backend.models import Task


def test_task_manager_constructor_defers_database_work(monkeypatch):
    calls = []

    def fake_scan(self, restore_timeouts=False):
        calls.append(("scan", restore_timeouts))

    def fake_load(self):
        calls.append(("load",))

    monkeypatch.setattr(task_manager_module.TaskManager, "scan_running_tasks", fake_scan)
    monkeypatch.setattr(task_manager_module.TaskManager, "load_schedules", fake_load)

    manager = task_manager_module.TaskManager()
    try:
        assert calls == []

        manager.initialize_runtime_state(restore_timeouts=True)
        assert calls == [("scan", True), ("load",)]

        manager.initialize_runtime_state(restore_timeouts=True)
        assert calls == [("scan", True), ("load",)]
    finally:
        manager.scheduler.shutdown(wait=False)


def test_startup_schema_repairs_add_runtime_columns_to_legacy_task_table():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE task (
                id VARCHAR NOT NULL PRIMARY KEY,
                name VARCHAR NOT NULL,
                command VARCHAR NOT NULL,
                cwd VARCHAR,
                description VARCHAR,
                device_id VARCHAR NOT NULL,
                schedule VARCHAR,
                timeout INTEGER,
                "order" INTEGER,
                created_at FLOAT NOT NULL
            )
            """
        )
        conn.exec_driver_sql("CREATE TABLE system_version (version INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO system_version (version) VALUES (61)")

    run_startup_schema_repairs(engine)

    with Session(engine) as session:
        columns = {row[1] for row in session.exec(text("PRAGMA table_info(task)")).all()}
        indexes = {row[1] for row in session.exec(text("PRAGMA index_list(task)")).all()}

    assert {"runtime_kind", "schedule_policy", "schedule_state"} <= columns
    assert "ix_task_runtime_kind" in indexes


def test_task_manager_deep_scans_missing_services_only(engine, session, monkeypatch):
    service = Task(
        id="service-codeyun",
        name="codeyun",
        command="python dev.py",
        device_id="local-device",
        runtime_kind="service",
        created_at=time.time(),
    )
    job = Task(
        id="job-weekly",
        name="weekly",
        command="python weekly.py",
        device_id="local-device",
        runtime_kind="job",
        created_at=time.time(),
    )
    session.add(service)
    session.add(job)
    session.commit()

    class FakeDevice:
        def __init__(self):
            self.calls = []
            self.running = set()

        def scan_running_tasks(self, tasks, *, deep_scan=False):
            ids = [task.id for task in tasks]
            self.calls.append((ids, deep_scan))
            if deep_scan:
                self.running.update(ids)

        def get_task_status(self, task_id):
            return SimpleNamespace(running=task_id in self.running)

    fake_device = FakeDevice()
    manager = task_manager_module.TaskManager()
    try:
        monkeypatch.setattr(task_manager_module, "engine", engine)
        monkeypatch.setattr(manager, "_get_local_device_id", lambda: "local-device")
        monkeypatch.setattr(task_manager_module.device_manager, "get_device", lambda device_id: fake_device)

        manager.scan_running_tasks()

        assert fake_device.calls == [
            (["service-codeyun", "job-weekly"], False),
            (["service-codeyun"], True),
        ]
    finally:
        manager.scheduler.shutdown(wait=False)


def test_documentasset_numeric_migration_drops_stale_readable_views():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE documentasset (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title VARCHAR NOT NULL,
                original_filename VARCHAR NOT NULL,
                media_type VARCHAR NOT NULL,
                file_ext VARCHAR NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 VARCHAR NOT NULL,
                source_char_count INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                latest_run_id VARCHAR,
                latest_summary VARCHAR NOT NULL,
                latest_query_at FLOAT,
                run_count INTEGER NOT NULL,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO documentasset (
                id, user_id, title, original_filename, media_type, file_ext,
                size_bytes, sha256, source_char_count, status, latest_summary,
                run_count, created_at, updated_at
            )
            VALUES (
                'legacy-doc', 1, 'Doc', 'doc.txt', 'text/plain', '.txt',
                12, 'sha', 12, 'uploaded', '', 0, 1000, 1000
            )
            """
        )
        conn.exec_driver_sql("CREATE VIEW attendancerun_readable AS SELECT * FROM attendancerun")

    with Session(engine) as session:
        v55_migrate_documentasset_primary_key_to_numeric(session)

    with Session(engine) as session:
        view = session.exec(
            text("SELECT name FROM sqlite_master WHERE type = 'view' AND name = 'attendancerun_readable'")
        ).first()
        id_column = next(row for row in session.exec(text("PRAGMA table_info(documentasset)")).all() if row[1] == "id")
        row = session.exec(text("SELECT id, legacy_id FROM documentasset")).one()

    assert view is None
    assert str(id_column[2]).upper().startswith("INTEGER")
    assert row == (1, "legacy-doc")
