import json
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine, select

from backend import db as db_module
from backend.core import device as device_module
from backend.models import Task, UserDevice


def _patch_identity_paths(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    logs_dir = data_dir / "logs"
    machine_dir = tmp_path / "machine"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(device_module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(device_module, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(device_module, "SYSTEM_ID_FILE", str(data_dir / "system_id.json"))
    monkeypatch.setattr(device_module, "LEGACY_CONFIG_FILE", str(data_dir / "config.json"))
    monkeypatch.setattr(device_module, "LEGACY_DEVICE_STATE_FILE", str(data_dir / "device_state.json"))
    monkeypatch.setattr(device_module, "LEGACY_NODE_STATE_FILE", str(data_dir / "node_state.json"))
    monkeypatch.setattr(device_module, "PIDS_FILE", str(data_dir / "pids.json"))
    monkeypatch.setattr(device_module, "MACHINE_STATE_DIR", str(machine_dir))
    monkeypatch.setattr(device_module, "MACHINE_IDENTITY_FILE", str(machine_dir / "device_identity.json"))

    return data_dir


def test_device_id_reuses_machine_identity_file(monkeypatch, tmp_path):
    _patch_identity_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        device_module,
        "_machine_identity_seed",
        lambda: ("windows_machine_guid", "machine-guid-123"),
    )

    first_id = device_module.get_device_id()
    expected_id = device_module._stable_device_id_from_seed(
        "windows_machine_guid",
        "machine-guid-123",
    )

    assert first_id == expected_id
    assert Path(device_module.MACHINE_IDENTITY_FILE).exists()
    second_id = device_module.get_device_id()

    assert second_id == first_id


def test_device_id_migrates_local_records_and_clears_legacy_dir(monkeypatch, tmp_path):
    data_dir = _patch_identity_paths(monkeypatch, tmp_path)
    old_id = "legacy-device-id"
    new_id = device_module._stable_device_id_from_seed(
        "windows_machine_guid",
        "machine-guid-xyz",
    )

    Path(device_module.LEGACY_DEVICE_STATE_FILE).write_text(
        json.dumps(
            {
                "device_id": old_id,
                "name": "Legacy Node",
                "created_at": 123.0,
            }
        ),
        encoding="utf-8",
    )

    old_log_dir = data_dir / old_id / "logs"
    old_log_dir.mkdir(parents=True, exist_ok=True)
    (old_log_dir / "task-1.log").write_text("old log", encoding="utf-8")

    db_path = tmp_path / "codeyun.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)

    with Session(engine) as session:
        session.add(
            Task(
                id="task-1",
                name="Legacy Task",
                command="python -V",
                device_id=old_id,
            )
        )
        session.add(
            UserDevice(
                user_id=1,
                device_id=old_id,
                mode="local",
                name="Legacy Entry",
                token="legacy-token",
            )
        )
        session.commit()

    monkeypatch.setattr(
        device_module,
        "_machine_identity_seed",
        lambda: ("windows_machine_guid", "machine-guid-xyz"),
    )

    resolved_id = device_module.get_device_id()

    assert resolved_id == new_id

    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.device_id == new_id)).all()
        entries = session.exec(select(UserDevice).where(UserDevice.device_id == new_id)).all()
        legacy_tasks = session.exec(select(Task).where(Task.device_id == old_id)).all()

    assert len(tasks) == 1
    assert len(entries) == 1
    assert legacy_tasks == []
    assert not (data_dir / old_id).exists()
    assert list((data_dir / "backups").glob("device-id-migration-*"))
