from types import SimpleNamespace

from backend.core import fanxiu_slimming
from backend.models import AppSetting


def test_fanxiu_slimming_worker_writes_latest_run_and_report(session, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    source_dir = tmp_path / "fx"
    report_root = tmp_path / "codeyun-data"
    data_dir.mkdir()
    source_dir.mkdir()
    (data_dir / "old.log").write_text("log", encoding="utf-8")

    captured = {}

    def fake_chat_func(**kwargs):
        captured["provider_id"] = kwargs["provider_id"]
        captured["message"] = kwargs["messages"][0]["content"]
        return {"content": "完成\n- 已检查旧日志", "model": "fake-model"}

    monkeypatch.setattr(fanxiu_slimming, "get_settings", lambda: SimpleNamespace(data_dir=report_root))
    monkeypatch.setattr(fanxiu_slimming, "is_fanxiu_slimming_allowed_host", lambda: True)

    fanxiu_slimming.run_fanxiu_slimming_worker(
        chat_func=fake_chat_func,
        db_bind=session.get_bind(),
        data_dir=data_dir,
        source_dir=source_dir,
    )

    session.expire_all()
    row = session.get(AppSetting, fanxiu_slimming.FANXIU_SLIMMING_SETTING_KEY)
    latest_run = row.value["latest_run"]
    assert latest_run["status"] == "completed"
    assert latest_run["stage_label"] == "巡检完成"
    assert latest_run["model"] == "fake-model"
    assert "源码功能" in captured["message"]
    assert "不要执行 git commit" in captured["message"]
    assert (report_root / "fanxiu-slimming").exists()
    assert latest_run["report_path"]


def test_fanxiu_slimming_worker_skips_non_mf_host(session, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    source_dir = tmp_path / "fx"
    data_dir.mkdir()
    source_dir.mkdir()

    def fail_chat_func(**kwargs):
        raise AssertionError("Codex CLI should not run on a disallowed host")

    monkeypatch.setattr(fanxiu_slimming, "is_fanxiu_slimming_allowed_host", lambda: False)

    fanxiu_slimming.run_fanxiu_slimming_worker(
        chat_func=fail_chat_func,
        db_bind=session.get_bind(),
        data_dir=data_dir,
        source_dir=source_dir,
    )

    session.expire_all()
    row = session.get(AppSetting, fanxiu_slimming.FANXIU_SLIMMING_SETTING_KEY)
    latest_run = row.value["latest_run"]
    assert latest_run["status"] == "skipped"
    assert latest_run["stage"] == "wrong_host"
