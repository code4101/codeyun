from __future__ import annotations

import datetime as dt
import subprocess

import pytest

from backend.api import codex_setup as codex_setup_api
from backend.core import opencode_usage
from backend.core.codex import official_setup, weekly_quota
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
    monkeypatch.setattr(codex_setup_api, "_resolve_deepseek_key", lambda session: "sk-system")

    response = codex_setup_api.get_codex_setup_status(current_user=None, session=None)

    assert response.model == "gpt-6-astra"
    assert response.deepseek_key_available is True
    assert response.provider == "openai"
    assert [item.id for item in response.providers] == [
        item["id"] for item in codex_setup_api.CODEX_SETUP_PROVIDERS
    ]


def test_api_switch_openai_is_noop_when_already_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "_resolve_deepseek_key", lambda session: "")

    response = codex_setup_api.switch_codex_setup(
        codex_setup_api.CodexSetupSwitchRequest(provider="openai"),
        current_user=None,
        session=None,
    )

    assert response.ok is True
    assert response.changed is False


def test_api_switch_deepseek_requires_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "_resolve_deepseek_key", lambda session: "")

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
            codex_setup_api.CodexSetupSwitchRequest(provider="opencode", model="kimi-k3"),
            current_user=None,
            session=None,
        )

    assert "不支持该模型" in str(excinfo.value)


def test_api_switch_closes_and_reopens_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "_resolve_deepseek_key", lambda session: "sk-system")

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


def test_api_switch_restarts_codex_even_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_config(tmp_path, 'model = "gpt-6-astra"\n')
    monkeypatch.setattr(codex_setup_api, "_resolve_deepseek_key", lambda session: "sk-system")
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


def test_build_general_quota_window_defaults_to_seven_days():
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
    assert start <= dt.datetime(2026, 9, 12, 8, 9, 41, tzinfo=dt.timezone.utc)
    assert end >= dt.datetime(2026, 9, 19, 8, 9, 41, tzinfo=dt.timezone.utc)
    assert window["remaining_percent"] == 14
    assert [item["remaining_percent"] for item in window["points"]] == [60]


def test_api_quota_returns_empty_prompt_before_collection(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api, "load_codex_quota_snapshot", lambda: {"observed_at": "", "groups": []}
    )
    monkeypatch.setattr(codex_setup_api, "list_codex_weekly_quota_snapshots", lambda: [])
    monkeypatch.setattr(codex_setup_api, "_bootstrap_quota_snapshot", lambda: None)

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


def test_api_quota_serves_history_without_snapshot(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api, "load_codex_quota_snapshot", lambda: {"observed_at": "", "groups": []}
    )
    monkeypatch.setattr(codex_setup_api, "_bootstrap_quota_snapshot", lambda: None)
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

    codex_switch.switch_to_opencode(config, "deepseek-v4-pro")

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model"] == "deepseek-v4-pro"
    assert data["model_provider"] == "opencode_go"
    assert "model_catalog_json" not in data
    assert data["model_reasoning_effort"] == "high"
    assert data["model_providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/"
    provider = data["model_providers"]["opencode_go"]
    assert provider["base_url"] == "https://opencode.ai/zen/go/v1"
    assert provider["wire_api"] == "responses"
    assert provider["experimental_bearer_token"] == "sk-oc"
    assert provider["http_headers"]["x-opencode-session"] == codex_switch.OPENCODE_GO_SESSION


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

    codex_switch.switch_to_opencode(config, "grok-4.6")

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model_providers"]["opencode_go"]["base_url"] == "https://opencode.ai/zen/go/v1"
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


def test_api_opencode_usage_returns_windows(monkeypatch):
    monkeypatch.setattr(
        codex_setup_api,
        "read_opencode_go_usage",
        lambda: {
            "available": True,
            "windows": [
                {"label": "5 小时", "remaining_percent": 83, "reset_at": "", "status": "ok"}
            ],
            "error": "",
        },
    )

    response = codex_setup_api.get_opencode_usage(current_user=None)

    assert response.available is True
    assert response.windows[0].remaining_percent == 83


def test_api_quota_refresh_reports_error_without_failing(monkeypatch):
    def raiser():
        raise CodexAppServerError("认证不可用")

    monkeypatch.setattr(codex_setup_api, "collect_codex_quota_snapshot", raiser)

    response = codex_setup_api.refresh_codex_quota(current_user=None)

    assert response.groups == []
    assert "认证不可用" in response.error
