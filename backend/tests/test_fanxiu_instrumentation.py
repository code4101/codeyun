from __future__ import annotations

from types import SimpleNamespace
import pytest

from backend.core.fanxiu.instrumentation.service import (
    FanxiuInstrumentationService,
)
from backend.core.fanxiu.instrumentation.policy import (
    FanxiuInstrumentationPolicyError,
    instrumentation_policy_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    MumuProcessMemory,
)
from backend.core.fanxiu.instrumentation.yunmeng_trial import (
    read_yunmeng_trial_status_snapshot,
)
from backend.core.fanxiu.instrumentation.lua_main_state_snapshot import (
    run_lua_main_state_snapshot,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _deploy_bridge,
    ensure_redbag_runtime_manager,
)
from backend.core.fanxiu.instrumentation.spirit_artifact_runtime_loader import (
    _deploy as deploy_spirit_artifact_runtime,
    refresh_spirit_artifact_runtime,
)


def _fake_adb(service: FanxiuInstrumentationService, *, server_running: bool):
    def run(args, *, timeout=8.0, check=True):
        del timeout, check
        tail = tuple(args[2:]) if args[:2] == ["-s", "device-1"] else tuple(args)
        responses = {
            ("devices",): "List of devices attached\ndevice-1\tdevice\n",
            ("shell", "pidof", "com.frxxcrjpwssc3.ggws"): "1234\n",
            ("shell", "sh -c 'ls -1 /data/local/tmp/frida-server* 2>/dev/null'"): "/data/local/tmp/frida-server-17.11.0\n",
            ("shell", "/data/local/tmp/frida-server-17.11.0", "--version"): "17.11.0\n",
            ("shell", "pidof", "frida-server-17.11.0"): "4321\n" if server_running else "",
            ("shell", "getprop", "ro.product.cpu.abilist"): "x86_64,arm64-v8a\n",
            ("shell", "uname", "-m"): "aarch64\n",
            ("shell", "id"): "uid=0(root) gid=0(root)\n",
            ("shell", "getenforce"): "Permissive\n",
            ("shell", "cat", "/proc/1234/maps"): (
                "1000-2000 r-xp 0 00:00 0 /data/app/lib/arm64/libil2cpp.so\n"
                "3000-4000 r-xp 0 00:00 0 /data/app/lib/arm64/libunity.so\n"
            ),
        }
        if tail in responses:
            return responses[tail]
        if tail[:1] == ("shell",) and "nohup" in tail[1]:
            return ""
        raise AssertionError(f"unexpected adb args: {args!r}")

    service._run_adb = run


def test_inspect_reports_translated_il2cpp_process():
    fake_frida = SimpleNamespace(__version__="17.11.0")
    service = FanxiuInstrumentationService(frida_loader=lambda: fake_frida)
    _fake_adb(service, server_running=True)

    result = service.inspect()

    assert result["ok"] is True
    assert result["device"]["abi_list"] == "x86_64,arm64-v8a"
    assert result["device"]["kernel_machine"] == "aarch64"
    assert result["target"]["pid"] == 1234
    assert [item["name"] for item in result["target"]["modules"]] == [
        "libil2cpp.so",
        "libunity.so",
    ]
    assert result["frida"]["server_running"] is True
    capabilities = {
        item["name"]: item for item in result["capabilities"]
    }
    assert capabilities["chat.red_packet.pending"]["kind"] == "query"
    assert capabilities["chat.red_packet.pending"]["implemented"] is True
    assert capabilities["chat.red_packet.pending"]["side_effect"] == "none"
    assert (
        capabilities["chat.red_packet.claim"]["side_effect"]
        == "network-request-and-reward"
    )
    assert capabilities["lingquan.question.snapshot"]["implemented"] is True
    assert (
        capabilities["lingquan.question.snapshot"]["validation_status"]
        == "pending-live-window"
    )
    assert capabilities["lingmai.snapshot"]["implemented"] is True
    assert capabilities["lingmai.snapshot"]["validation_status"] == "live-validated"
    assert capabilities["lingmai.snapshot"]["side_effect"] == "none"
    assert capabilities["dongtian.snapshot"]["validation_status"] == "live-validated"
    assert capabilities["xianfu.skill_draw.snapshot"]["validation_status"] == "live-validated"
    assert capabilities["xianfu.skill_draw.snapshot"]["side_effect"] == "none"
    assert capabilities["lundao.snapshot"]["validation_status"] == "live-validated"
    assert capabilities["lundao.snapshot"]["side_effect"] == "none"
    assert capabilities["mail.snapshot"]["kind"] == "query"
    assert capabilities["mail.snapshot"]["implemented"] is True
    assert capabilities["mail.snapshot"]["validation_status"] == "live-validated"
    assert capabilities["mail.snapshot"]["side_effect"] == "none"
    assert capabilities["mail.unclaimed"]["kind"] == "query"
    assert capabilities["mail.unclaimed"]["implemented"] is True
    assert capabilities["mail.unclaimed"]["side_effect"] == "none"
    assert (
        capabilities["mail.claim"]["side_effect"]
        == "network-request-and-reward"
    )


def test_probe_is_blocked_before_device_or_process_access():
    service = FanxiuInstrumentationService(
        frida_loader=lambda: (_ for _ in ()).throw(
            AssertionError("strict read-only mode must not load Frida")
        )
    )
    service.choose_device = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("strict read-only mode must fail before device access")
    )

    with pytest.raises(FanxiuInstrumentationPolicyError, match="严格只读"):
        service.probe(ensure_server=False)


def test_choose_device_rejects_ambiguous_running_targets():
    service = FanxiuInstrumentationService(
        frida_loader=lambda: SimpleNamespace(__version__="17.11.0")
    )
    service.adb_devices = lambda: ["device-1", "device-2"]
    service.process_id = lambda _device, _package: 1

    with pytest.raises(Exception, match="多个设备"):
        service.choose_device()


def test_yunmeng_refresh_fails_fast_without_process_cache(monkeypatch):
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(
            lambda cls, **_kwargs: (_ for _ in ()).throw(
                FanxiuRuntimeMemoryError("凡修进程缓存尚未预热或身份已变化")
            )
        ),
    )
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover",
        classmethod(
            lambda cls: (_ for _ in ()).throw(
                AssertionError("用户刷新入口不得执行进程发现")
            )
        ),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="缓存尚未预热"):
        read_yunmeng_trial_status_snapshot(
            rank_activity_id=210801,
            currency_type=19,
            event_date="2026-08-02",
        )


def test_ensure_server_is_blocked_before_adb_access():
    service = FanxiuInstrumentationService(
        frida_loader=lambda: SimpleNamespace(__version__="17.11.0")
    )
    service.choose_device = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("strict read-only mode must fail before ADB access")
    )

    with pytest.raises(FanxiuInstrumentationPolicyError, match="Frida Server"):
        service.ensure_server()


def test_instrumentation_policy_is_fail_closed():
    policy = instrumentation_policy_snapshot()

    assert policy["mode"] == "strict-read-only"
    assert policy["locked"] is True
    assert any("Lua" in item for item in policy["blocked"])


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            lambda: run_lua_main_state_snapshot(
                name="read-test",
                script_source="return true",
            ),
            "Lua",
        ),
        (ensure_redbag_runtime_manager, "RedbagMgr"),
        (refresh_spirit_artifact_runtime, "灵器"),
    ],
)
def test_active_runtime_loaders_are_blocked_before_discovery(operation, expected):
    with pytest.raises(FanxiuInstrumentationPolicyError, match=expected):
        operation()


@pytest.mark.parametrize(
    "operation",
    [
        lambda: _deploy_bridge(SimpleNamespace(), "device-1"),
        lambda: deploy_spirit_artifact_runtime(SimpleNamespace(), "device-1"),
    ],
)
def test_device_deploy_helpers_are_blocked_before_file_or_adb_access(operation):
    with pytest.raises(FanxiuInstrumentationPolicyError, match="严格只读"):
        operation()


def test_red_packet_pending_delegates_to_runtime_memory(monkeypatch):
    captured: dict[str, object] = {}
    expected = {"ok": True, "source": "runtime_memory", "pending": False}
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.read_red_packet_pending",
        lambda **kwargs: captured.update(kwargs) or expected,
    )

    service = FanxiuInstrumentationService()

    assert service.red_packet_pending() is expected
    assert captured["allow_runtime_initialization"] is False


def test_lingquan_question_snapshot_delegates_to_non_blocking_cache(monkeypatch):
    expected = {"ok": True, "source": "runtime_memory", "question_id": 12}
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.lingquan."
        "get_lingquan_question_snapshot",
        lambda **_kwargs: expected,
    )

    service = FanxiuInstrumentationService()

    assert service.lingquan_question_snapshot() is expected


def test_final_camp_answer_snapshot_delegates_to_non_blocking_cache(monkeypatch):
    expected = {"ok": True, "quest_id": 3107}
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.final_camp_answer."
        "get_final_camp_answer_snapshot",
        lambda **kwargs: {**expected, "max_age_seconds": kwargs["max_age_seconds"]},
    )

    service = FanxiuInstrumentationService()
    result = service.final_camp_answer_snapshot(max_age_seconds=0.8)

    assert result == {**expected, "max_age_seconds": 0.8}


def test_camp_answer_snapshot_delegates_to_non_blocking_cache(monkeypatch):
    expected = {"ok": True, "question_count": 15}
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.camp_answer.get_camp_answer_snapshot",
        lambda **kwargs: {**expected, "max_age_seconds": kwargs["max_age_seconds"]},
    )

    service = FanxiuInstrumentationService()
    result = service.camp_answer_snapshot(max_age_seconds=1.5)

    assert result == {**expected, "max_age_seconds": 1.5}
