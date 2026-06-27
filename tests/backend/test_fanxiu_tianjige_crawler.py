import os

from backend.core import fanxiu_tianjige_crawler
from backend.models import AppSetting


class _CompletedProcess:
    returncode = 0
    stdout = "抢答爬虫完成"
    stderr = ""


def test_fanxiu_tianjige_worker_runs_xlproject_crawler(session, tmp_path, monkeypatch):
    xlproject_root = tmp_path / "xlproject"
    python_executable = xlproject_root / ".venv" / "Scripts" / "python.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return _CompletedProcess()

    monkeypatch.setattr(fanxiu_tianjige_crawler.subprocess, "run", fake_run)
    monkeypatch.setattr(fanxiu_tianjige_crawler, "is_fanxiu_tianjige_quiz_allowed_host", lambda: True)

    fanxiu_tianjige_crawler.run_fanxiu_tianjige_quiz_worker(
        db_bind=session.get_bind(),
        xlproject_root=xlproject_root,
        python_executable=python_executable,
    )

    assert calls
    assert calls[0]["args"][0] == os.fspath(python_executable.resolve(strict=False))
    assert calls[0]["args"][1] == "-c"
    assert "天机阁爬虫" in calls[0]["args"][2]
    assert "自动抢答有奖竞答" in calls[0]["args"][2]
    assert calls[0]["cwd"] == xlproject_root.resolve(strict=False)
    assert os.fspath(xlproject_root / "src") in calls[0]["env"]["PYTHONPATH"]

    session.expire_all()
    row = session.get(AppSetting, fanxiu_tianjige_crawler.FANXIU_TIANJIGE_QUIZ_SETTING_KEY)
    latest_run = row.value["latest_run"]
    assert latest_run["status"] == "completed"
    assert latest_run["stage_label"] == "爬虫完成"
    assert latest_run["returncode"] == 0
    assert "抢答爬虫完成" in latest_run["result_text"]


def test_fanxiu_tianjige_worker_skips_disallowed_host(session, tmp_path, monkeypatch):
    xlproject_root = tmp_path / "xlproject"
    python_executable = xlproject_root / ".venv" / "Scripts" / "python.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("crawler should not run on a disallowed host")

    monkeypatch.setattr(fanxiu_tianjige_crawler.subprocess, "run", fail_run)
    monkeypatch.setattr(fanxiu_tianjige_crawler, "is_fanxiu_tianjige_quiz_allowed_host", lambda: False)

    fanxiu_tianjige_crawler.run_fanxiu_tianjige_quiz_worker(
        db_bind=session.get_bind(),
        xlproject_root=xlproject_root,
        python_executable=python_executable,
    )

    session.expire_all()
    row = session.get(AppSetting, fanxiu_tianjige_crawler.FANXIU_TIANJIGE_QUIZ_SETTING_KEY)
    latest_run = row.value["latest_run"]
    assert latest_run["status"] == "skipped"
    assert latest_run["stage"] == "wrong_host"
