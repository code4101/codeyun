from __future__ import annotations

import datetime as dt
import json
import subprocess

import pytest

from backend.api import codex_setup as codex_setup_api
from backend.core import opencode_usage
from backend.core.codex import app_processes, official_setup, weekly_quota
from backend.core.codex import switch as codex_switch
from backend.core.codex.app_server import CodexAppServerError


_SAMPLE_RATE_LIMITS = {
    "ordinaryUsageAllowed": True,
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 86, "windowDurationMins": 10080, "resetsAt": 1789805381},
        "secondary": None,
    },
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": None,
            "primary": {"usedPercent": 86, "windowDurationMins": 10080, "resetsAt": 1789805381},
            "secondary": None,
        },
        "codex_bengalfox": {
            "limitId": "codex_bengalfox",
            "limitName": "GPT-5.3-Codex-Spark",
            "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1789458329},
            "secondary": {"usedPercent": 52, "windowDurationMins": 10080, "resetsAt": 1789833973},
        },
    },
}


def _write_config(home, body: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(body, encoding="utf-8")


def _write_global_state(home, recent: list[dict[str, object]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".codex-global-state.json").write_text(
        json.dumps(
            {"electron-persisted-atom-state": {"composer-recent-model-configurations-v1": recent}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_read_status_defaults_to_gpt_without_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\nmodel_reasoning_effort = "low"\n')

    status = official_setup.read_codex_status()

    assert status["mode"] == "gpt"
    assert status["model"] == "gpt-6-astra"
    assert status["model_provider"] == ""
    assert status["deepseek_configured"] is False


def test_read_status_detects_deepseek_flash_and_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(
        tmp_path,
        "\n".join(
            [
                'model = "deepseek-flash"',
                'model_provider = "deepseek"',
                "[model_providers.deepseek]",
                'name = "deepseek"',
                'base_url = "https://api.deepseek.com/"',
                'wire_api = "responses"',
                'experimental_bearer_token = "sk-test-token"',
            ]
        )
        + "\n",
    )

    status = official_setup.read_codex_status()

    assert status["mode"] == official_setup.FLASH_MODE
    assert status["deepseek_configured"] is True
    assert status["deepseek_api_key_present"] is True
    assert official_setup.read_existing_deepseek_token() == "sk-test-token"


def test_read_status_detects_deepseek_pro(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(
        tmp_path,
        'model = "deepseek-v4-pro"\nmodel_provider = "deepseek"\n[model_providers.deepseek]\n',
    )

    status = official_setup.read_codex_status()

    assert status["mode"] == official_setup.PRO_MODE


def test_build_answers_passes_menu_choice_and_restore_confirmation():
    assert official_setup._build_answers(official_setup.FLASH_MODE) == "1\n"
    assert official_setup._build_answers(official_setup.PRO_MODE) == "2\n"
    assert official_setup._build_answers(official_setup.GPT_MODE) == "9\ny\n"


def test_switch_codex_mode_runs_script_with_env_and_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    script_path = tmp_path / "codex-deepseek-setup.ps1"
    script_path.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(official_setup, "_download_setup_script", lambda platform: script_path)

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        (tmp_path / "config.toml").write_text(
            'model = "deepseek-flash"\nmodel_provider = "deepseek"\n[model_providers.deepseek]\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(official_setup.subprocess, "run", fake_run)

    result = official_setup.switch_codex_mode(official_setup.FLASH_MODE, api_key="sk-abc")

    assert result["mode"] == official_setup.FLASH_MODE
    assert result["status"]["mode"] == official_setup.FLASH_MODE
    assert captured["kwargs"]["input"] == "1\n"
    assert captured["kwargs"]["env"]["DEEPSEEK_API_KEY"] == "sk-abc"
    assert captured["kwargs"]["env"]["CODEX_HOME"] == str(tmp_path.resolve())


def test_switch_codex_mode_raises_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    script_path = tmp_path / "codex-deepseek-setup.ps1"
    script_path.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(official_setup, "_download_setup_script", lambda platform: script_path)
    monkeypatch.setattr(
        official_setup.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="boom"),
    )

    with pytest.raises(official_setup.CodexSetupError):
        official_setup.switch_codex_mode(official_setup.FLASH_MODE, api_key="sk-abc")


def test_api_status_reports_key_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "sk-system")

    response = codex_setup_api.get_codex_setup_status(current_user=None, session=None)

    assert response.model == "gpt-6-astra"
    assert response.deepseek_key_available is True
    assert response.provider == "openai"
    assert [item.id for item in response.providers] == [
        item["id"] for item in codex_setup_api.CODEX_SETUP_PROVIDERS
    ]


def test_api_switch_openai_cleans_config_and_syncs_app_model(tmp_path, monkeypatch):
    import tomllib

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(
        tmp_path,
        'model = "gpt-6-astra"\n\n'
        "[model_providers.opencode_go]\n"
        'base_url = "http://127.0.0.1:8787/v1"\n',
    )
    _write_global_state(
        tmp_path,
        [
            {"model": "deepseek-flash", "reasoningEffort": "high", "serviceTier": None},
            {"model": "gpt-5.6-luna", "reasoningEffort": "medium", "serviceTier": None},
        ],
    )
    (tmp_path / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "")
    monkeypatch.setattr(
        codex_setup_api,
        "stop_codex_processes",
        lambda: {"was_app_running": True, "app_exe": "C:/ChatGPT.exe", "stopped": []},
    )
    monkeypatch.setattr(codex_setup_api, "start_codex_app", lambda snapshot: True)

    response = codex_setup_api.switch_codex_setup(
        codex_setup_api.CodexSetupSwitchRequest(provider="openai"),
        current_user=None,
        session=None,
    )

    assert response.ok is True
    assert response.changed is False
    assert response.notice == codex_setup_api.OPENAI_RESET_NOTICE
    # The provider block and the stale DeepSeek catalog are gone, so nothing can
    # route new threads to the local proxy...
    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert "model_providers" not in data
    assert not (tmp_path / "models.json").exists()
    # ...and new threads default to the restored OpenAI model.
    state = json.loads((tmp_path / ".codex-global-state.json").read_text(encoding="utf-8"))
    recent = state["electron-persisted-atom-state"]["composer-recent-model-configurations-v1"]
    assert recent[-1]["model"] == "gpt-6-astra"


def test_ensure_baseline_snapshot_is_write_once(tmp_path):
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')

    assert codex_switch.ensure_baseline_snapshot(tmp_path, tmp_path / "config.toml") is True
    baseline = tmp_path / "backup-codeyun" / codex_switch.CODEX_BASELINE_FILENAME
    assert baseline.read_text(encoding="utf-8") == 'model = "gpt-6-astra"\n'

    _write_config(tmp_path, 'model = "deepseek-flash"\nmodel_provider = "deepseek"\n')
    assert codex_switch.ensure_baseline_snapshot(tmp_path, tmp_path / "config.toml") is False
    assert baseline.read_text(encoding="utf-8") == 'model = "gpt-6-astra"\n'


def test_restore_codeyun_backup_restores_baseline_and_drops_catalog(tmp_path):
    import tomllib

    baseline = 'model = "gpt-6-astra"\n'
    baseline_dir = tmp_path / "backup-codeyun"
    baseline_dir.mkdir()
    (baseline_dir / codex_switch.CODEX_BASELINE_FILENAME).write_text(baseline, encoding="utf-8")
    _write_config(tmp_path, 'model = "deepseek-flash"\nmodel_provider = "opencode_go"\n')
    (tmp_path / "opencode_models.json").write_text("{}", encoding="utf-8")
    (tmp_path / "models.json").write_text("{}", encoding="utf-8")

    assert codex_switch.restore_codeyun_backup(tmp_path) is True

    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["model"] == "gpt-6-astra"
    assert "model_provider" not in data
    assert "model_providers" not in data
    # Both generated catalogs are gone: the baseline referenced neither.
    assert not (tmp_path / "opencode_models.json").exists()
    assert not (tmp_path / "models.json").exists()
    # The baseline is kept so OpenAI can be restored again later.
    assert (baseline_dir / codex_switch.CODEX_BASELINE_FILENAME).is_file()


def test_restore_codeyun_backup_keeps_catalog_declared_by_baseline(tmp_path):
    import tomllib

    baseline_dir = tmp_path / "backup-codeyun"
    baseline_dir.mkdir()
    (baseline_dir / codex_switch.CODEX_BASELINE_FILENAME).write_text(
        'model = "gpt-6-astra"\nmodel_catalog_json = "C:/x/models.json"\n', encoding="utf-8"
    )
    _write_config(tmp_path, 'model = "deepseek-flash"\nmodel_provider = "deepseek"\n')
    (tmp_path / "models.json").write_text("{}", encoding="utf-8")

    assert codex_switch.restore_codeyun_backup(tmp_path) is True

    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["model_catalog_json"] == "C:/x/models.json"
    assert (tmp_path / "models.json").is_file()


def test_set_current_model_promotes_target_to_newest(tmp_path):
    from backend.core.codex import app_state

    _write_global_state(
        tmp_path,
        [
            {"model": "gpt-6-astra", "reasoningEffort": "medium", "serviceTier": None},
            {"model": "gpt-5.6-luna", "reasoningEffort": "medium", "serviceTier": None},
        ],
    )

    assert app_state.set_current_model(tmp_path, "gpt-6-astra") is True

    state = json.loads((tmp_path / ".codex-global-state.json").read_text(encoding="utf-8"))
    recent = state["electron-persisted-atom-state"]["composer-recent-model-configurations-v1"]
    assert [item["model"] for item in recent] == ["gpt-5.6-luna", "gpt-6-astra"]
    assert recent[-1]["reasoningEffort"] == "medium"


def test_set_current_model_is_noop_when_already_newest(tmp_path):
    from backend.core.codex import app_state

    _write_global_state(tmp_path, [{"model": "gpt-6-astra", "reasoningEffort": "low", "serviceTier": None}])

    assert app_state.set_current_model(tmp_path, "gpt-6-astra") is False


def test_set_current_model_ignores_missing_or_malformed_state(tmp_path):
    from backend.core.codex import app_state

    assert app_state.set_current_model(tmp_path, "gpt-6-astra") is False

    (tmp_path / ".codex-global-state.json").write_text("not json", encoding="utf-8")
    assert app_state.set_current_model(tmp_path, "gpt-6-astra") is False


def test_restore_codeyun_backup_drops_managed_provider_blocks(tmp_path):
    import tomllib

    baseline_dir = tmp_path / "backup-codeyun"
    baseline_dir.mkdir()
    (baseline_dir / codex_switch.CODEX_BASELINE_FILENAME).write_text('model = "gpt-6-astra"\n', encoding="utf-8")
    _write_config(
        tmp_path,
        'model = "deepseek-flash"\n'
        'model_provider = "deepseek"\n\n'
        "[model_providers.deepseek]\n"
        'base_url = "https://api.deepseek.com/"\n\n'
        "[model_providers.opencode_go]\n"
        'base_url = "http://127.0.0.1:8787/v1"\n',
    )

    assert codex_switch.restore_codeyun_backup(tmp_path) is True

    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["model"] == "gpt-6-astra"
    assert "model_provider" not in data
    # Neither managed provider survives, so no new thread can be created against
    # the local proxy.
    assert "model_providers" not in data


def test_api_switch_openai_prefers_baseline_over_deepseek_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    baseline = 'model = "gpt-6-astra"\n'
    (tmp_path / "backup-deepseek").mkdir()
    (tmp_path / "backup-codeyun").mkdir()
    (tmp_path / "backup-codeyun" / codex_switch.CODEX_BASELINE_FILENAME).write_text(baseline, encoding="utf-8")
    _write_config(
        tmp_path,
        'model = "deepseek-flash"\n'
        'model_provider = "opencode_go"\n'
        'model_catalog_json = "C:/Users/x/.codex/opencode_models.json"\n\n'
        "[model_providers.opencode_go]\n"
        'name = "OpenCode Go (proxy)"\n'
        'base_url = "http://127.0.0.1:8787/v1"\n',
    )
    (tmp_path / "opencode_models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        codex_setup_api,
        "stop_codex_processes",
        lambda: {"was_app_running": False, "app_exe": "", "stopped": []},
    )
    monkeypatch.setattr(codex_setup_api, "start_codex_app", lambda snapshot: False)
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "")

    response = codex_setup_api.switch_codex_setup(
        codex_setup_api.CodexSetupSwitchRequest(provider="openai"),
        current_user=None,
        session=None,
    )

    import tomllib

    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    # Top level is the pristine baseline...
    assert data["model"] == "gpt-6-astra"
    assert "model_provider" not in data
    assert "model_catalog_json" not in data
    # ...and the managed provider block is gone, so the desktop app cannot route
    # new threads through the local proxy.
    assert "model_providers" not in data
    assert not (tmp_path / "opencode_models.json").exists()
    assert response.status.model == "gpt-6-astra"
    assert response.provider == "openai"
    assert response.changed is True


def test_api_switch_deepseek_requires_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "")

    with pytest.raises(Exception) as excinfo:
        codex_setup_api.switch_codex_setup(
            codex_setup_api.CodexSetupSwitchRequest(provider="deepseek", model="deepseek-flash"),
            current_user=None,
            session=None,
        )

    assert "DeepSeek API Key" in str(excinfo.value)


def test_api_switch_rejects_unknown_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')

    with pytest.raises(Exception) as excinfo:
        codex_setup_api.switch_codex_setup(
            codex_setup_api.CodexSetupSwitchRequest(provider="opencode", model="definitely-not-a-model"),
            current_user=None,
            session=None,
        )

    assert "不支持该模型" in str(excinfo.value)


def test_switch_codex_defaults_deepseek_to_flash(tmp_path, monkeypatch):
    """The page picks a provider only, so DeepSeek must land on flash by default."""

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    applied: dict[str, object] = {}

    def fake_switch_mode(mode, *, api_key=None):
        applied["mode"] = mode
        applied["api_key"] = api_key
        return {"mode": mode, "output": "", "status": official_setup.read_codex_status()}

    monkeypatch.setattr(codex_switch, "switch_codex_mode", fake_switch_mode)

    result = codex_switch.switch_codex("deepseek", api_key="sk-system")

    assert applied["mode"] == official_setup.FLASH_MODE
    assert applied["api_key"] == "sk-system"
    assert result["mode"] == official_setup.FLASH_MODE


def test_api_status_exposes_provider_default_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "")

    response = codex_setup_api.get_codex_setup_status(current_user=None, session=None)

    by_id = {item.id: item for item in response.providers}
    assert by_id["deepseek"].default_model == official_setup.FLASH_MODE
    assert by_id["opencode"].default_model == official_setup.FLASH_MODE
    assert by_id["openai"].default_model == ""


def test_api_switch_closes_and_reopens_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "sk-system")

    calls: dict[str, object] = {}

    def fake_stop():
        calls["stop"] = True
        return {"was_app_running": True, "app_exe": "C:/ChatGPT.exe", "stopped": [{"pid": 1, "name": "ChatGPT"}]}

    def fake_start(snapshot):
        calls["start"] = snapshot
        return True

    def fake_switch(provider, model=None, *, api_key=None):
        calls["switch"] = (provider, model, api_key)
        _write_config(tmp_path, 'model = "deepseek-flash"\nmodel_provider = "deepseek"\n')
        return {"mode": model, "output": "ok", "status": official_setup.read_codex_status()}

    monkeypatch.setattr(codex_setup_api, "stop_codex_processes", fake_stop)
    monkeypatch.setattr(codex_setup_api, "start_codex_app", fake_start)
    monkeypatch.setattr(codex_setup_api, "switch_codex", fake_switch)

    response = codex_setup_api.switch_codex_setup(
        codex_setup_api.CodexSetupSwitchRequest(provider="deepseek", model="deepseek-flash"),
        current_user=None,
        session=None,
    )

    assert calls["stop"] is True
    assert calls["switch"] == ("deepseek", "deepseek-flash", "sk-system")
    assert calls["start"]["was_app_running"] is True
    assert response.restarted_app is True
    assert response.closed_process_count == 1
    assert "ChatGPT" in response.message
    # The switch only rewrites machine-wide config, so the caller has to be told
    # that already-open threads keep their own model selection.
    assert response.changed is True
    assert response.notice == codex_setup_api.SESSION_SCOPE_NOTICE


def test_api_switch_restarts_codex_even_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "resolve_deepseek_key", lambda session: "sk-system")
    restarted: dict[str, object] = {}
    monkeypatch.setattr(
        codex_setup_api,
        "stop_codex_processes",
        lambda: {"was_app_running": True, "app_exe": "C:/ChatGPT.exe", "stopped": []},
    )
    monkeypatch.setattr(codex_setup_api, "start_codex_app", lambda snapshot: restarted.setdefault("snapshot", snapshot) or True)

    def failing_switch(provider, model=None, *, api_key=None):
        raise official_setup.CodexSetupError("boom")

    monkeypatch.setattr(codex_setup_api, "switch_codex", failing_switch)

    with pytest.raises(Exception):
        codex_setup_api.switch_codex_setup(
            codex_setup_api.CodexSetupSwitchRequest(provider="deepseek", model="deepseek-flash"),
            current_user=None,
            session=None,
        )

    assert restarted["snapshot"]["was_app_running"] is True


def test_matches_codex_process_accepts_windows_exe_suffix():
    store_app = r"C:\Program Files\WindowsApps\OpenAI.Codex_x64\app\ChatGPT.exe"

    assert app_processes._matches_codex_process("ChatGPT.exe", store_app) is True
    assert app_processes._matches_codex_process("ChatGPT", store_app) is True
    assert app_processes._matches_codex_process("codex.exe", r"C:\Users\x\OpenAI\Codex\bin\codex.exe") is True
    assert app_processes._matches_codex_process("node.exe", r"C:\Program Files\nodejs\node.exe") is False


def test_stop_codex_processes_marks_store_app_as_running(monkeypatch):
    class FakeProcess:
        def __init__(self, pid: int, name: str, exe: str):
            self.pid = pid
            self.info = {"pid": pid, "name": name, "exe": exe}

    store_app = r"C:\Program Files\WindowsApps\OpenAI.Codex_x64\app\ChatGPT.exe"
    fake = FakeProcess(4321, "ChatGPT.exe", store_app)
    monkeypatch.setattr(app_processes.psutil, "process_iter", lambda attrs=None: [fake])
    monkeypatch.setattr(app_processes, "_terminate", lambda process: True)

    snapshot = app_processes.stop_codex_processes()

    assert snapshot["was_app_running"] is True
    assert snapshot["app_exe"] == store_app
    assert snapshot["stopped"] == [{"pid": 4321, "name": "ChatGPT.exe"}]


def test_parse_rate_limit_groups_splits_general_and_spark():
    groups = weekly_quota.parse_codex_rate_limit_groups(_SAMPLE_RATE_LIMITS)

    assert [item["id"] for item in groups] == ["codex", "codex_bengalfox"]
    assert groups[0]["name"] == "通用使用限额"
    assert [(w["label"], w["remaining_percent"]) for w in groups[0]["windows"]] == [("每周", 14)]
    assert groups[1]["name"] == "GPT-5.3-Codex-Spark"
    assert [(w["label"], w["remaining_percent"]) for w in groups[1]["windows"]] == [
        ("5 小时", 100),
        ("每周", 48),
    ]


def test_read_codex_quota_groups_forces_chatgpt_auth(monkeypatch):
    captured: dict[str, object] = {}

    def fake_read_codex_rate_limits(**kwargs):
        captured.update(kwargs)
        return _SAMPLE_RATE_LIMITS

    monkeypatch.setattr(weekly_quota, "read_codex_rate_limits", fake_read_codex_rate_limits)

    groups = weekly_quota.read_codex_quota_groups()

    assert captured["config_overrides"] == weekly_quota.CODEX_CHATGPT_AUTH_OVERRIDE
    assert groups[0]["windows"][0]["remaining_percent"] == 14


def test_chatgpt_auth_override_beats_forced_api_login():
    """The DeepSeek setup script forces API-key login, which hides the ChatGPT account.

    Both keys have to be overridden for the child app-server, otherwise
    ``account/rateLimits/read`` answers "authentication required" even when a valid
    ChatGPT ``auth.json`` exists.
    """

    overrides = dict(weekly_quota.CODEX_CHATGPT_AUTH_OVERRIDE)

    assert overrides["preferred_auth_method"] == '"chatgpt"'
    assert overrides["forced_login_method"] == '"chatgpt"'


def test_quota_error_message_explains_missing_chatgpt_login():
    error = CodexAppServerError(
        "Codex app-server account/rateLimits/read 失败：codex account authentication required to read rate limits"
    )

    message = weekly_quota.describe_codex_quota_error(error)

    assert "codex login" in message
    assert "未登录 ChatGPT" in message


def test_quota_error_message_keeps_unrelated_failures_verbatim():
    error = CodexAppServerError("Codex app-server initialize 超时")

    assert weekly_quota.describe_codex_quota_error(error) == "Codex app-server initialize 超时"


def test_build_general_quota_window_covers_two_periods():
    groups = weekly_quota.parse_codex_rate_limit_groups(_SAMPLE_RATE_LIMITS)
    snapshots = [
        {"date": "2026-09-10", "observed_at": "2026-09-10T00:00:00", "remaining_percent": 90},
        {"date": "2026-09-13", "observed_at": "2026-09-13T00:00:00", "remaining_percent": 60},
    ]

    window = weekly_quota.build_codex_general_quota_window(groups, snapshots)

    assert window["name"] == "通用使用限额"
    assert window["reset_at"] == "2026-09-19T08:09:41+00:00"
    start = dt.datetime.fromisoformat(window["window_start"])
    end = dt.datetime.fromisoformat(window["window_end"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second) == (0, 0, 0)
    assert start <= dt.datetime(2026, 9, 5, 8, 9, 41, tzinfo=dt.timezone.utc)
    assert end >= dt.datetime(2026, 9, 19, 8, 9, 41, tzinfo=dt.timezone.utc)
    assert window["period_minutes"] == 7 * 24 * 60
    assert window["remaining_percent"] == 14
    assert [item["remaining_percent"] for item in window["points"]] == [90, 60]


def test_api_quota_returns_empty_prompt_before_collection(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api, "load_codex_quota_snapshot", lambda: {"observed_at": "", "groups": []}
    )
    monkeypatch.setattr(codex_setup_api, "list_codex_weekly_quota_snapshots", lambda: [])
    monkeypatch.setattr(codex_setup_api, "collect_codex_quota_snapshot", lambda: pytest.fail("GET must only read snapshots"))

    response = codex_setup_api.get_codex_quota(current_user=None)

    assert response.groups == []
    assert "尚未采集" in response.error


def test_api_quota_serves_stored_snapshot(monkeypatch):
    groups = weekly_quota.parse_codex_rate_limit_groups(_SAMPLE_RATE_LIMITS)
    monkeypatch.setattr(
        codex_setup_api,
        "load_codex_quota_snapshot",
        lambda: {"observed_at": "2026-09-15T00:00:00", "groups": groups},
    )
    monkeypatch.setattr(codex_setup_api, "list_codex_weekly_quota_snapshots", lambda: [])

    response = codex_setup_api.get_codex_quota(current_user=None)

    assert [item.id for item in response.groups] == ["codex", "codex_bengalfox"]
    assert response.observed_at == "2026-09-15T00:00:00"
    assert response.general_window is not None
    assert response.general_window.remaining_percent == 14
    assert response.general_window.period_minutes == 7 * 24 * 60


def test_build_general_quota_window_without_live_snapshot():
    snapshots = [
        {
            "date": "2026-09-14",
            "observed_at": "2026-09-14T00:00:00",
            "remaining_percent": 30,
            "reset_at": "2026-09-19T08:09:41+00:00",
        },
        {
            "date": "2026-09-15",
            "observed_at": "2026-09-15T00:00:00",
            "remaining_percent": 14,
            "reset_at": "2026-09-19T08:09:41+00:00",
        },
    ]

    window = weekly_quota.build_codex_general_quota_window([], snapshots)

    assert window["name"] == "通用使用限额"
    assert window["reset_at"] == "2026-09-19T08:09:41+00:00"
    assert window["remaining_percent"] == 14
    assert [item["remaining_percent"] for item in window["points"]] == [30, 14]


def test_build_general_quota_window_breaks_line_at_reset_time():
    snapshots = [
        {
            "date": "2026-09-11",
            "observed_at": "2026-09-11T00:00:00+00:00",
            "remaining_percent": 5,
            "reset_at": "2026-09-12T00:00:00+00:00",
        },
        {
            "date": "2026-09-13",
            "observed_at": "2026-09-13T00:00:00+00:00",
            "remaining_percent": 100,
            "reset_at": "2026-09-19T00:00:00+00:00",
        },
    ]

    window = weekly_quota.build_codex_general_quota_window([], snapshots)

    assert [(item["at"], item["remaining_percent"]) for item in window["points"]] == [
        ("2026-09-11T00:00:00+00:00", 5),
        ("2026-09-12T00:00:00+00:00", 5),
        ("2026-09-12T00:00:00+00:00", None),
        ("2026-09-12T00:00:00+00:00", 100),
        ("2026-09-13T00:00:00+00:00", 100),
    ]


def test_build_general_quota_window_separates_reset_end_from_next_start():
    """A cycle's end is not the next cycle's start: the next starts on first use."""

    snapshots = [
        {
            "date": "2026-09-11",
            "observed_at": "2026-09-11T00:00:00+00:00",
            "remaining_percent": 5,
            "reset_at": "2026-09-12T00:00:00+00:00",
        },
        {
            "date": "2026-09-13",
            "observed_at": "2026-09-13T00:00:00+00:00",
            "remaining_percent": 100,
            "reset_at": "2026-09-19T18:00:00+00:00",
        },
    ]

    window = weekly_quota.build_codex_general_quota_window([], snapshots)

    # The old cycle closes at 09-12 00:00, but the new one only opens one period
    # before its own reset (09-12 18:00); the idle gap stays out of the line.
    assert [(item["at"], item["remaining_percent"]) for item in window["points"]] == [
        ("2026-09-11T00:00:00+00:00", 5),
        ("2026-09-12T00:00:00+00:00", 5),
        ("2026-09-12T00:00:00+00:00", None),
        ("2026-09-12T18:00:00+00:00", 100),
        ("2026-09-13T00:00:00+00:00", 100),
    ]
    # Each cycle carries its own start/reset pair for its reference line.
    assert window["periods"] == [
        {"start_at": "2026-09-05T00:00:00+00:00", "reset_at": "2026-09-12T00:00:00+00:00"},
        {"start_at": "2026-09-12T18:00:00+00:00", "reset_at": "2026-09-19T18:00:00+00:00"},
    ]


@pytest.mark.parametrize("remaining", [100, 30])
def test_build_general_quota_window_breaks_at_early_reset(remaining):
    """A new cycle can replace the old one before its scheduled deadline."""
    snapshots = [
        {"date": "2026-09-21", "observed_at": "2026-09-21T00:00:00+00:00",
         "remaining_percent": 54, "reset_at": "2026-09-27T00:00:00+00:00"},
        {"date": "2026-09-22", "observed_at": "2026-09-22T01:00:00+00:00",
         "remaining_percent": remaining, "reset_at": "2026-09-29T00:00:00+00:00"},
    ]
    window = weekly_quota.build_codex_general_quota_window([], snapshots)
    assert [(p["at"], p["remaining_percent"]) for p in window["points"]] == [
        ("2026-09-21T00:00:00+00:00", 54),
        ("2026-09-22T00:00:00+00:00", 54),
        ("2026-09-22T00:00:00+00:00", None),
        ("2026-09-22T00:00:00+00:00", 100),
        ("2026-09-22T01:00:00+00:00", remaining),
    ]
    # Preserve the original deadline: the renderer clips, never re-slopes it.
    assert window["periods"] == [
        {"start_at": "2026-09-20T00:00:00+00:00", "reset_at": "2026-09-27T00:00:00+00:00"},
        {"start_at": "2026-09-22T00:00:00+00:00", "reset_at": "2026-09-29T00:00:00+00:00"},
    ]


def test_build_general_quota_window_breaks_reset_with_clock_skew():
    """The reset observed at 17:00:09 has an inferred start four seconds later."""
    snapshots = [
        {"observed_at": "2026-09-26T16:00:20+00:00", "remaining_percent": 9,
         "reset_at": "2026-09-30T02:07:31+00:00"},
        {"observed_at": "2026-09-26T17:00:09+00:00", "remaining_percent": 100,
         "reset_at": "2026-10-03T17:00:13+00:00"},
        {"observed_at": "2026-09-26T18:00:07+00:00", "remaining_percent": 99,
         "reset_at": "2026-10-03T17:00:46+00:00"},
    ]
    window = weekly_quota.build_codex_general_quota_window([], snapshots)
    assert [(p["at"], p["remaining_percent"]) for p in window["points"]] == [
        ("2026-09-26T16:00:20+00:00", 9),
        ("2026-09-26T17:00:09+00:00", 9),
        ("2026-09-26T17:00:09+00:00", None),
        ("2026-09-26T17:00:09+00:00", 100),
        ("2026-09-26T17:00:09+00:00", 100),
        ("2026-09-26T18:00:07+00:00", 99),
    ]


def test_build_general_quota_window_deduplicates_reset_timestamp_drift():
    snapshots = [
        {"observed_at": "2026-09-16T00:00:00+00:00", "remaining_percent": 0,
         "reset_at": "2026-09-19T16:09:42+00:00"},
        {"observed_at": "2026-09-20T00:00:00+00:00", "remaining_percent": 84,
         "reset_at": "2026-09-26T16:10:35+00:00"},
        {"observed_at": "2026-09-21T00:00:00+00:00", "remaining_percent": 46,
         "reset_at": "2026-09-26T16:10:36+00:00"},
    ]

    window = weekly_quota.build_codex_general_quota_window([], snapshots)

    assert [item["reset_at"] for item in window["periods"]] == [
        "2026-09-19T16:09:42+00:00",
        "2026-09-26T16:10:36+00:00",
    ]

    snapshots.append({
        "observed_at": "2026-09-23T11:00:00+00:00", "remaining_percent": 100,
        "reset_at": "2026-09-30T10:07:30+00:00",
    })
    window = weekly_quota.build_codex_general_quota_window([], snapshots)
    assert [item["reset_at"] for item in window["periods"]] == [
        "2026-09-19T16:09:42+00:00",
        "2026-09-26T16:10:36+00:00",
        "2026-09-30T10:07:30+00:00",
    ]


def test_api_quota_serves_history_without_snapshot(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api, "load_codex_quota_snapshot", lambda: {"observed_at": "", "groups": []}
    )
    monkeypatch.setattr(codex_setup_api, "collect_codex_quota_snapshot", lambda: pytest.fail("GET must only read snapshots"))
    monkeypatch.setattr(
        codex_setup_api,
        "list_codex_weekly_quota_snapshots",
        lambda: [
            {
                "date": "2026-09-15",
                "observed_at": "2026-09-15T00:00:00",
                "remaining_percent": 14,
                "reset_at": "2026-09-19T08:09:41+00:00",
            }
        ],
    )

    response = codex_setup_api.get_codex_quota(current_user=None)

    assert response.groups == []
    assert response.general_window is not None
    assert len(response.general_window.points) == 1
    assert response.observed_at == response.general_window.points[0].at


def test_detect_provider_maps_model_provider_ids():
    assert codex_switch.detect_provider("") == "openai"
    assert codex_switch.detect_provider("deepseek") == "deepseek"
    assert codex_switch.detect_provider("opencode_go") == "opencode"


def test_switch_to_opencode_writes_provider_block_and_preserves_config(tmp_path, monkeypatch):
    import tomllib

    config = tmp_path / "config.toml"
    config.write_text(
        'model = "deepseek-flash"\n'
        'model_provider = "deepseek"\n'
        'model_catalog_json = "C:/x/models.json"\n'
        'model_reasoning_effort = "high"\n\n'
        "[mcp_servers]\n\n"
        "[model_providers.deepseek]\n"
        'base_url = "https://api.deepseek.com/"\n',
        encoding="utf-8",
    )
    (tmp_path / "backup-deepseek").mkdir()
    monkeypatch.setattr(codex_switch, "read_opencode_go_key", lambda: "sk-oc")
    monkeypatch.setattr(
        codex_switch,
        "discover_opencode_models",
        lambda **kwargs: [{"id": "deepseek-v4-pro", "label": "deepseek-v4-pro"}],
    )
    monkeypatch.setattr(codex_switch, "get_opencode_proxy_base_url", lambda: "http://127.0.0.1:8787")

    codex_switch.switch_to_opencode(config, "deepseek-v4-pro")

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model"] == "deepseek-v4-pro"
    assert data["model_provider"] == "opencode_go"
    assert "model_catalog_json" not in data
    assert "model_reasoning_effort" not in data
    assert data["model_providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/"
    provider = data["model_providers"]["opencode_go"]
    assert provider["base_url"] == "http://127.0.0.1:8787/v1"
    assert provider["wire_api"] == "responses"
    assert provider["experimental_bearer_token"] == "opencode-proxy"


def test_ensure_enabled_reasoning_efforts_merges_existing_list():
    lines = [
        'model = "x"',
        "",
        "[desktop]",
        'enabled-reasoning-efforts = ["low", "high"]',
        "",
        "[other]",
    ]

    codex_switch.ensure_enabled_reasoning_efforts(lines, ["max", "low"])

    assert lines[3] == 'enabled-reasoning-efforts = ["low", "high", "max"]'


def test_ensure_enabled_reasoning_efforts_creates_desktop_section():
    lines = ['model = "x"']

    codex_switch.ensure_enabled_reasoning_efforts(lines, ["max"])

    assert lines[-2:] == ["[desktop]", 'enabled-reasoning-efforts = ["max"]']


def test_switch_to_opencode_adds_catalog_efforts_to_desktop_list(tmp_path, monkeypatch):
    import tomllib

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(codex_switch, "_official_deepseek_catalog", lambda: {})
    (tmp_path / "models_cache.json").write_text(
        json.dumps({
            "models": [{
                "slug": "gpt-5.3-codex",
                "context_window": 272000,
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "l"},
                    {"effort": "medium", "description": "m"},
                    {"effort": "high", "description": "h"},
                    {"effort": "xhigh", "description": "xh"},
                    {"effort": "max", "description": "mx"},
                ],
            }]
        }),
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "deepseek-flash"\n'
        'model_provider = "deepseek"\n\n'
        "[desktop]\n"
        'enabled-reasoning-efforts = ["low", "medium", "high", "xhigh"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_switch, "read_opencode_go_key", lambda: "sk-oc")
    monkeypatch.setattr(
        codex_switch,
        "discover_opencode_models",
        lambda **kwargs: [{"id": "kimi-k3", "label": "kimi-k3"}],
    )
    monkeypatch.setattr(codex_switch, "get_opencode_proxy_base_url", lambda: "http://127.0.0.1:8787")

    codex_switch.switch_to_opencode(config, "kimi-k3")

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["desktop"]["enabled-reasoning-efforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_build_opencode_catalog_clones_codex_template(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(codex_switch, "_official_deepseek_catalog", lambda: {})
    (tmp_path / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-5.3-codex", "display_name": "GPT 5.3 Codex", "context_window": 272000}]}),
        encoding="utf-8",
    )

    path = codex_switch.build_opencode_catalog(
        tmp_path, [{"id": "grok-4.6", "label": "grok-4.6"}, {"id": "kimi-k3", "label": "kimi-k3"}]
    )

    assert path is not None and path.name == "opencode_models.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [item["slug"] for item in data["models"]] == ["grok-4.6", "kimi-k3"]
    assert data["models"][0]["display_name"] == "grok-4.6"
    assert data["models"][0]["context_window"] == 272000
    assert data["models"][0]["effective_context_window_percent"] == codex_switch.OPENCODE_CONTEXT_WINDOW_PERCENT


def test_build_opencode_catalog_uses_model_reasoning_metadata(tmp_path, monkeypatch):
    (tmp_path / "models_cache.json").write_text(
        json.dumps({
            "models": [
                {
                    "slug": "gpt-5.3-codex",
                    "context_window": 272000,
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "medium", "description": "x"}],
                }
            ]
        }),
        encoding="utf-8",
    )
    cache_home = tmp_path / "cache"
    (cache_home / "opencode").mkdir(parents=True)
    (cache_home / "opencode" / "models.json").write_text(
        json.dumps({
            "opencode-go": {
                "models": {
                    "kimi-k3": {
                        "reasoning_options": [{"type": "effort", "values": ["max"]}],
                        "limit": {"context": 1048576},
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr(codex_switch, "_official_deepseek_catalog", lambda: {})

    path = codex_switch.build_opencode_catalog(tmp_path, [{"id": "kimi-k3", "label": "kimi-k3"}])

    entry = json.loads(path.read_text(encoding="utf-8"))["models"][0]
    assert [level["effort"] for level in entry["supported_reasoning_levels"]] == ["max"]
    assert entry["default_reasoning_level"] == "max"
    assert entry["context_window"] == 1048576


def test_build_opencode_catalog_prefers_official_deepseek_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    (tmp_path / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-5.3-codex", "context_window": 1, "base_instructions": "GPT"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        codex_switch,
        "_official_deepseek_catalog",
        lambda: {"deepseek-flash": {"slug": "deepseek-flash", "context_window": 999999, "base_instructions": "DS"}},
    )

    path = codex_switch.build_opencode_catalog(
        tmp_path, [{"id": "deepseek-flash", "label": "DeepSeek Flash"}, {"id": "kimi-k3", "label": "kimi-k3"}]
    )

    by_slug = {item["slug"]: item for item in json.loads(path.read_text(encoding="utf-8"))["models"]}
    assert by_slug["deepseek-flash"]["base_instructions"] == "DS"
    assert by_slug["deepseek-flash"]["context_window"] == 999999
    # The opencode entry keeps the official DeepSeek tuning but is listed by its
    # own label, without a transport prefix.
    assert by_slug["deepseek-flash"]["display_name"] == "DeepSeek Flash"
    assert by_slug["kimi-k3"]["base_instructions"] == "GPT"


def test_switch_to_opencode_replaces_existing_block(tmp_path, monkeypatch):
    import tomllib

    config = tmp_path / "config.toml"
    config.write_text(
        'model = "grok-4.6"\n'
        'model_provider = "opencode_go"\n'
        "mcp_servers = 1\n\n"
        "[model_providers.opencode_go]\n"
        'base_url = "old"\n',
        encoding="utf-8",
    )
    (tmp_path / "backup-deepseek").mkdir()
    monkeypatch.setattr(codex_switch, "read_opencode_go_key", lambda: "sk-oc")
    monkeypatch.setattr(
        codex_switch,
        "discover_opencode_models",
        lambda **kwargs: [{"id": "grok-4.6", "label": "grok-4.6"}],
    )

    codex_switch.switch_to_opencode(config, "grok-4.6")

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model_providers"]["opencode_go"]["base_url"].endswith("/v1")
    assert data["mcp_servers"] == 1


def test_parse_opencode_go_usage_converts_used_to_remaining():
    windows = opencode_usage.parse_opencode_go_usage(
        {
            "usage": {
                "rolling": {"status": "ok", "percent": 17, "resetsAt": "2026-09-15T06:45:45.104Z"},
                "weekly": {"status": "ok", "percent": 6, "resetsAt": "2026-09-21T00:00:00.104Z"},
                "monthly": {"status": "ok", "percent": 3, "resetsAt": "2026-10-14T14:57:19.104Z"},
            }
        }
    )

    assert [(item["label"], item["remaining_percent"]) for item in windows] == [
        ("5 小时", 83),
        ("每周", 94),
        ("每月", 97),
    ]


def test_read_opencode_go_key_from_auth_file(tmp_path):
    assert opencode_usage.read_opencode_go_key(path=tmp_path / "auth.json") == ""

    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode-go": {"type": "api", "key": "sk-test"}}', encoding="utf-8")

    assert opencode_usage.read_opencode_go_key(path=auth_path) == "sk-test"


def test_record_codex_weekly_quota_accumulates_intraday_by_hour(tmp_path):
    path = tmp_path / "weekly_quota_history.json"
    weekly_quota.record_codex_weekly_quota_snapshot(
        remaining_percent=40, observed_at=dt.datetime(2026, 9, 15, 10, 0, 0), path=path
    )
    weekly_quota.record_codex_weekly_quota_snapshot(
        remaining_percent=30, observed_at=dt.datetime(2026, 9, 15, 11, 30, 0), path=path
    )

    snapshots = weekly_quota.list_codex_weekly_quota_snapshots(path)

    assert [item["remaining_percent"] for item in snapshots] == [40, 30]


def test_build_deepseek_balance_window_uses_last_30_days():
    from backend.core import deepseek_balance

    now = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    snapshots = [
        {"observed_at": "2026-08-01T00:00:00", "total_balance": 10},
        {"observed_at": "2026-09-01T00:00:00", "total_balance": 20},
        {"observed_at": "2026-09-10T00:00:00", "total_balance": 30},
    ]

    window = deepseek_balance.build_deepseek_balance_window(snapshots, now=now)

    assert [point["value"] for point in window["points"]] == [20, 30]


def test_build_deepseek_balance_window_includes_latest_without_history():
    from backend.core import deepseek_balance

    now = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)

    window = deepseek_balance.build_deepseek_balance_window(
        [], now=now, latest_value=76.22, latest_at="2026-09-15T11:00:00+00:00"
    )

    assert len(window["points"]) == 1
    assert window["points"][0]["value"] == 76.22


def test_build_opencode_monthly_window_filters_history():
    from backend.core import opencode_usage

    payload = {
        "available": True,
        "windows": [
            {"label": "5 小时", "remaining_percent": 80, "reset_at": ""},
            {"label": "每周", "remaining_percent": 90, "reset_at": ""},
            {"label": "每月", "remaining_percent": 50, "reset_at": "2026-10-14T14:57:19+00:00"},
        ],
        "error": "",
    }
    snapshots = [
        {"date": "2026-08-01", "observed_at": "2026-08-01T00:00:00", "monthly": 99},
        {"date": "2026-09-15", "observed_at": "2026-09-15T00:00:00", "monthly": 70},
        {"date": "2026-09-25", "observed_at": "2026-09-25T00:00:00", "monthly": 50},
    ]

    window = opencode_usage.build_opencode_monthly_window(payload, snapshots)

    assert window["reset_at"] == "2026-10-14T14:57:19+00:00"
    assert window["period_minutes"] == 30 * 24 * 60
    assert window["remaining_percent"] == 50
    assert [item["remaining_percent"] for item in window["points"]] == [70, 50]


def test_opencode_usage_snapshot_roundtrip(tmp_path):
    from backend.core import opencode_usage

    path = tmp_path / "opencode_usage.json"
    opencode_usage.save_opencode_usage_snapshot(
        {"available": True, "windows": [], "error": ""},
        dt.datetime(2026, 9, 15, 12, 0),
        path=path,
    )

    snapshot = opencode_usage.load_opencode_usage_snapshot(path=path)

    assert snapshot["observed_at"].startswith("2026-09-15T12:00")
    assert snapshot["payload"]["available"] is True


def test_api_opencode_usage_serves_stored_snapshot(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api,
        "load_opencode_usage_snapshot",
        lambda: {
            "observed_at": "2026-09-15T12:00:00",
            "payload": {
                "available": True,
                "windows": [{"label": "5 小时", "remaining_percent": 80, "reset_at": "", "status": "ok"}],
                "error": "",
            },
        },
    )

    response = codex_setup_api.get_opencode_usage(current_user=None)

    assert response.available is True
    assert response.observed_at == "2026-09-15T12:00:00"
    assert response.windows[0].remaining_percent == 80


def test_api_opencode_usage_refresh_collects(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api,
        "collect_opencode_usage_snapshot",
        lambda: {
            "observed_at": "2026-09-15T12:00:00",
            "payload": {
                "available": True,
                "windows": [{"label": "5 小时", "remaining_percent": 83, "reset_at": "", "status": "ok"}],
                "error": "",
            },
        },
    )

    response = codex_setup_api.refresh_opencode_usage(current_user=None)

    assert response.available is True
    assert response.observed_at == "2026-09-15T12:00:00"
    assert response.windows[0].remaining_percent == 83


def test_api_quota_refresh_reports_error_without_failing(monkeypatch):
    def raiser():
        raise CodexAppServerError("认证不可用")

    monkeypatch.setattr(codex_setup_api, "collect_codex_quota_snapshot", raiser)
    monkeypatch.setattr(codex_setup_api, "load_codex_quota_snapshot", lambda: {"observed_at": "", "groups": []})

    response = codex_setup_api.refresh_codex_quota(current_user=None)

    assert response.groups == []
    assert "认证不可用" in response.error


def test_api_quota_refresh_falls_back_to_last_snapshot(monkeypatch):
    def raiser():
        raise CodexAppServerError("认证不可用")

    monkeypatch.setattr(codex_setup_api, "collect_codex_quota_snapshot", raiser)
    monkeypatch.setattr(codex_setup_api, "list_codex_weekly_quota_snapshots", lambda: [])
    monkeypatch.setattr(
        codex_setup_api,
        "load_codex_quota_snapshot",
        lambda: {
            "observed_at": "2026-09-15T12:00:00",
            "groups": [
                {
                    "id": "codex",
                    "name": "Codex",
                    "windows": [{"label": "每周", "remaining_percent": 42, "reset_at": ""}],
                }
            ],
        },
    )

    response = codex_setup_api.refresh_codex_quota(current_user=None)

    assert response.groups[0].windows[0].remaining_percent == 42
    assert response.observed_at == "2026-09-15T12:00:00"
    assert "认证不可用" in response.error
