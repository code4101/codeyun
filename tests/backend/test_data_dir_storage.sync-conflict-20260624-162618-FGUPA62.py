import json
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine, select

from backend import db as db_module
from backend.api import admin as admin_module
from backend.core import device as device_module
from backend.models import AppSetting, TaskRuntime


def _patch_data_dir_paths(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    logs_dir = data_dir / "logs"
    machine_dir = tmp_path / "machine"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    machine_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(device_module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(device_module, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(device_module, "SYSTEM_ID_FILE", str(data_dir / "system_id.json"))
    monkeypatch.setattr(device_module, "LEGACY_CONFIG_FILE", str(data_dir / "config.json"))
    monkeypatch.setattr(device_module, "LEGACY_DEVICE_STATE_FILE", str(data_dir / "device_state.json"))
    monkeypatch.setattr(device_module, "LEGACY_NODE_STATE_FILE", str(data_dir / "node_state.json"))
    monkeypatch.setattr(device_module, "PIDS_FILE", str(data_dir / "pids.json"))
    monkeypatch.setattr(device_module, "MACHINE_STATE_DIR", str(machine_dir))
    monkeypatch.setattr(device_module, "MACHINE_IDENTITY_FILE", str(machine_dir / "device_identity.json"))
    monkeypatch.setattr(
        admin_module,
        "LEGACY_STORAGE_CONFIG_FILE",
        str(data_dir / "storage_config.json"),
    )

    return data_dir


def test_local_device_imports_legacy_pids_into_database(monkeypatch, tmp_path):
    data_dir = _patch_data_dir_paths(monkeypatch, tmp_path)
    Path(device_module.PIDS_FILE).write_text(
        json.dumps({"task-1": 12345}),
        encoding="utf-8",
    )

    db_path = tmp_path / "codeyun.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(device_module, "engine", engine)

    device = device_module.LocalDevice(device_id="device-a", name="Device A")

    assert device.saved_pids == {"task-1": 12345}

    with Session(engine) as session:
        rows = session.exec(select(TaskRuntime).where(TaskRuntime.device_id == "device-a")).all()

    assert len(rows) == 1
    assert rows[0].task_id == "task-1"
    assert rows[0].pid == 12345
    assert data_dir.joinpath("pids.json").exists()


def test_admin_schedule_imports_legacy_storage_config_into_database(monkeypatch, tmp_path):
    _patch_data_dir_paths(monkeypatch, tmp_path)
    Path(admin_module.LEGACY_STORAGE_CONFIG_FILE).write_text(
        json.dumps({"schedule_enabled": True, "cron_expression": "15 2 * * *"}),
        encoding="utf-8",
    )

    db_path = tmp_path / "codeyun.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(admin_module, "engine", engine)

    config = admin_module.load_config()

    assert config == {
        "schedule_enabled": True,
        "cron_expression": "15 2 * * *",
    }

    with Session(engine) as session:
        row = session.get(AppSetting, admin_module.STORAGE_SCHEDULE_SETTING_KEY)

    assert row is not None
    assert row.value == config
