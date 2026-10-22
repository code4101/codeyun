import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.core.baidu_netdisk import automation as am
from backend.core.baidu_netdisk import automation_cli as cli
from backend.core.baidu_netdisk import automation_config as ac

SECRET = "SECRET-provider-text-41"
AUTOMATION = "backend.core.baidu_netdisk.automation"


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "bootstrap_runtime", lambda: pytest.fail("explicit config must not bootstrap"))
    cfg = ac.AutomationConfig(profile="main", scene_ids=["home", "files"], client_download_dir=tmp_path / "dl",
                              local_root=tmp_path / "local", paths=ac.AutomationPaths(tmp_path / "auto"))
    return str(ac.write_automation_config(cfg))


class FakeService:
    """Records every public call; returns scripted results."""

    def __init__(self, results=None, raises=None):
        self.calls, self.results, self.raises = [], results or {}, raises

    def _answer(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.results.get(name, {"status": "verified"})

    def inspect(self, *a):
        return self._answer("inspect", *a)

    def reconcile(self, *a):
        return self._answer("reconcile", *a)

    def recover(self, *a):
        return self._answer("recover", *a)

    def verify(self, *a):
        return self._answer("verify", *a)

    def list_directory(self, *a):
        return self._answer("list_directory", *a)

    def resolve(self, *a):
        return self._answer("resolve", *a)

    def download(self, *a, **k):
        return self._answer("download", *a, **k)

    def download_dir(self, *a, **k):
        return self._answer("download_dir", *a, **k)

    def upload(self, *a, **k):
        return self._answer("upload", *a, **k)

    def inspect_upload(self, *a):
        return self._answer("inspect_upload", *a)

    def reconcile_upload(self, *a):
        return self._answer("reconcile_upload", *a)

    def ensure_directory(self, *a, **k):
        return self._answer("ensure_directory", *a, **k)

    def inspect_directory(self, *a):
        return self._answer("inspect_directory", *a)

    def reconcile_directory(self, *a):
        return self._answer("reconcile_directory", *a)

    def list_maintenance(self, *a):
        return self._answer("list_maintenance", *a)

    def inspect_maintenance(self, *a):
        return self._answer("inspect_maintenance", *a)

    def dispatch_maintenance(self, *a):
        return self._answer("dispatch_maintenance", *a)

    @contextmanager
    def native(self, owner, *, lease_token=None):
        self.calls.append(("native", (owner,), {"lease_token": lease_token}))
        service = self

        class Ctx:
            def observe(self, role):
                return service._answer("observe", role)

            def action(self, *a, **k):
                return service._answer("action", *a, **k)
        yield Ctx()
        self.calls.append(("native_closed", (), {}))


def run(monkeypatch, capsys, argv, service=None):
    configs = []
    if service is not None:
        monkeypatch.setattr(cli, "_open_service", lambda cfg: configs.append(cfg) or service)
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith("{") else out), configs


def test_help_never_loads_config_env_or_service(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, AUTOMATION)
    monkeypatch.setattr(ac, "bootstrap_runtime", lambda: pytest.fail("bootstrapped"))
    monkeypatch.setattr(cli, "load_automation_config", lambda *a: pytest.fail("config loaded"))
    monkeypatch.setattr(cli, "_open_service", lambda *a: pytest.fail("service opened"))
    assert cli.main(["--help"]) == 0
    assert "download" in capsys.readouterr().out
    for command in ("download", "verify", "observe", "action", "config-show", "maintenance-list",
                    "maintenance-inspect", "maintenance-dispatch", "ensure-directory", "inspect-directory",
                    "reconcile-directory"):
        assert cli.main([command, "--help"]) == 0
    assert AUTOMATION not in sys.modules


def test_usage_errors_exit_2_without_config(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_automation_config", lambda *a: pytest.fail("config loaded"))
    assert cli.main([]) == cli.EXIT_USAGE
    assert cli.main(["upload", "x"]) == cli.EXIT_USAGE
    assert cli.main(["download", "--operation-id", "op", "--remote-path", "/a", "--size", "3",
                     "--destination", "out"]) == cli.EXIT_USAGE  # no resource id
    assert cli.main(["download", "--operation-id", "op", "--remote-path", "/a", "--resource-id", "1",
                     "--size", "three", "--destination", "out"]) == cli.EXIT_USAGE


def test_default_config_is_loaded_lazily_only_when_a_command_runs(monkeypatch, capsys, tmp_path):
    seen = []
    cfg = ac.AutomationConfig(profile="p", scene_ids=["s"], client_download_dir=tmp_path / "dl",
                              local_root=tmp_path / "l", paths=ac.AutomationPaths(tmp_path / "a"))
    monkeypatch.setattr(cli, "load_automation_config", lambda path: seen.append(path) or cfg)
    code, out, _ = run(monkeypatch, capsys, ["config-show"])
    assert seen == [None] and code == 0 and out["ok"] is True
    assert out["result"]["profile"] == "p" and out["result"]["paths"]["failures_dir"] == str(tmp_path / "a" / "failures")


def test_json_on_cp936_redirected_stdout_is_valid_utf8_with_exact_unicode(monkeypatch, tmp_path):
    import io
    root = tmp_path / "百度网盘_下载" / "ğ😀"  # CJK plus characters cp936 cannot encode
    cfg = ac.AutomationConfig(profile="配置", scene_ids=["s"], client_download_dir=root / "dl",
                              local_root=root, paths=ac.AutomationPaths(tmp_path / "a"))
    monkeypatch.setattr(cli, "load_automation_config", lambda path: cfg)
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp936", newline="\n")
    monkeypatch.setattr(sys, "stdout", stdout)
    assert cli.main(["config-show"]) == 0
    stdout.flush()
    out = json.loads(raw.getvalue().decode("utf-8"))
    assert out["result"]["local_root"] == str(root) and out["result"]["profile"] == "配置"


def test_config_show_reads_generated_config_without_service(monkeypatch, capsys, config_file, tmp_path):
    monkeypatch.setattr(cli, "_open_service", lambda *a: pytest.fail("service opened"))
    code, out, _ = run(monkeypatch, capsys, ["--config", config_file, "config-show"])
    assert code == 0 and out["command"] == "config-show"
    assert out["result"]["scene_ids"] == ["home", "files"] and out["result"]["local_root"] == str(tmp_path / "local")
    assert not (tmp_path / "auto" / "operations").exists()


def test_config_errors_exit_3(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_open_service", lambda *a: pytest.fail("service opened"))
    code, out, _ = run(monkeypatch, capsys, ["--config", str(tmp_path / "missing.json"), "inspect", "op-1"])
    assert code == cli.EXIT_CONFIG and out["error"]["code"] == "config_invalid" and out["ok"] is False


@pytest.mark.parametrize("argv, call, result, code", [
    (["inspect", "op-1"], ("inspect", ("op-1",), {}), {"state": "intent"}, 0),
    (["inspect", "op-1"], ("inspect", ("op-1",), {}), None, 1),
    (["reconcile", "op-1"], ("reconcile", ("op-1",), {}), {"status": "verified"}, 0),
    (["reconcile", "op-1"], ("reconcile", ("op-1",), {}), {"status": "needs_review"}, 1),
    (["recover", "op-1"], ("recover", ("op-1",), {}), {"status": "needs_review", "recovered": False}, 1),
    (["verify", "out"], ("verify", ("out", None), {}), {"status": "local_hash_recorded"}, 0),
    (["verify", "out"], ("verify", ("out", None), {}), {"status": "not_found"}, 1),
    (["list", "/docs", "--expected-id", "7"], ("list_directory", ("/docs", "7"), {}), {"status": "listed"}, 0),
    (["list", "/docs"], ("list_directory", ("/docs", None), {}), {"status": "unstable"}, 1),
    (["resolve", "/docs/a", "--type", "file"], ("resolve", ("/docs/a", None, False), {}), {"status": "resolved"}, 0),
    (["resolve", "/docs"], ("resolve", ("/docs", None, None), {}), {"status": "not_found"}, 1),
    (["inspect-upload", "op-1"], ("inspect_upload", ("op-1",), {}), {"state": "intent"}, 0),
    (["inspect-upload", "op-1"], ("inspect_upload", ("op-1",), {}), None, 1),
    (["reconcile-upload", "op-1"], ("reconcile_upload", ("op-1",), {}), {"status": "remote_present"}, 0),
    (["reconcile-upload", "op-1"], ("reconcile_upload", ("op-1",), {}), {"status": "submitted"}, 1),
])
def test_readonly_commands_call_exactly_one_public_method(monkeypatch, capsys, config_file, argv, call, result, code):
    name = call[0]
    service = FakeService({name: result})
    rc, out, configs = run(monkeypatch, capsys, ["--config", config_file, *argv], service)
    assert rc == code and service.calls == [call]
    assert configs[0].profile == "main" and out["ok"] is (code == 0) and out["command"] == argv[0]
    if result is None:
        assert out["result"] == {"operation_id": "op-1", "found": False}
    else:
        assert out["result"] == result


def test_verify_reads_manifest_file_and_rejects_bad_one(monkeypatch, capsys, config_file, tmp_path):
    manifest = {"files": [{"rel": "a.txt", "size": 3}]}
    good, bad = tmp_path / "m.json", tmp_path / "bad.json"
    good.write_text(json.dumps(manifest), encoding="utf-8")
    bad.write_text("[1]", encoding="utf-8")
    service = FakeService({"verify": {"status": "structure_verified"}})
    rc, _, _ = run(monkeypatch, capsys, ["--config", config_file, "verify", "out", "--manifest", str(good)], service)
    assert rc == 0 and service.calls == [("verify", ("out", manifest), {})]
    for path in (bad, tmp_path / "missing.json"):
        rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "verify", "out", "--manifest", str(path)],
                         FakeService())
        assert rc == cli.EXIT_USAGE and out["error"]["code"] == "input_invalid"


def test_observe_and_action_go_through_native_context(monkeypatch, capsys, config_file):
    service = FakeService({"observe": {"observation_id": "o1"}, "action": {"status": "verified"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "observe", "--role", "settings",
                                           "--owner", "task-1", "--lease-token", "tok"], service)
    assert rc == 0 and out["result"] == {"observation_id": "o1"}
    assert service.calls == [("native", ("task-1",), {"lease_token": "tok"}), ("observe", ("settings",), {}),
                             ("native_closed", (), {})]
    service = FakeService({"action": {"status": "unverified"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "action", "home", "tab", "--expected", "files",
                                           "--expected", "home", "--discovery"], service)
    assert rc == 1 and out["result"] == {"status": "unverified"}
    assert service.calls == [("native", (cli.OWNER,), {"lease_token": None}),
                             ("action", ("home", "tab", ["files", "home"]), {"discovery": True}),
                             ("native_closed", (), {})]


def test_download_passes_explicit_request_once(monkeypatch, capsys, config_file):
    service = FakeService({"download": {"status": "verified", "content_verified": True}})
    argv = ["--config", config_file, "download", "--operation-id", "op-dl", "--remote-path", "/docs/a.txt",
            "--resource-id", "998877", "--size", "3", "--destination", "out", "--sha256", "A" * 64,
            "--version", "v2", "--owner", "task-9", "--lease-token", "tok"]
    rc, out, _ = run(monkeypatch, capsys, argv, service)
    assert rc == 0 and out["ok"] is True
    assert service.calls == [("download", ("op-dl",), {
        "remote_path": "/docs/a.txt", "resource_id": "998877", "expected_size": 3, "destination": "out",
        "expected_sha256": "A" * 64, "resource_version": "v2", "owner": "task-9", "lease_token": "tok"})]


def test_download_defaults_and_unverified_outcome_exit_1(monkeypatch, capsys, config_file):
    service = FakeService({"download": {"status": "state_unknown", "next": "collect_do_not_resubmit"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "download", "--operation-id", "op-dl",
                                           "--remote-path", "/a.txt", "--resource-id", "1", "--size", "0",
                                           "--destination", "out"], service)
    assert rc == cli.EXIT_NOT_OK and out["result"]["next"] == "collect_do_not_resubmit"
    (_, _, kwargs), = service.calls  # called exactly once, no retry
    assert kwargs["expected_sha256"] is None and kwargs["resource_version"] is None
    assert kwargs["owner"] == cli.OWNER and kwargs["lease_token"] is None


DIR_ARGV = ["download-dir", "--operation-id", "op-dir", "--remote-path", "/docs/pack", "--resource-id", "556677",
            "--version", "1700000000", "--destination", "out"]


def test_download_dir_passes_explicit_request_and_manifest_once(monkeypatch, capsys, config_file, tmp_path):
    manifest = {"files": [{"rel": "sub/a.txt", "size": 3, "sha256": "A" * 64}]}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = {"status": "verified", "verification_level": "content_verified", "manifest_sha256": "B" * 64}
    service = FakeService({"download_dir": result})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *DIR_ARGV, "--manifest", str(path),
                                           "--owner", "task-9", "--lease-token", "tok"], service)
    assert rc == 0 and out["ok"] is True and out["result"] == result and out["command"] == "download-dir"
    assert service.calls == [("download_dir", ("op-dir",), {
        "remote_path": "/docs/pack", "resource_id": "556677", "destination": "out", "expected_manifest": manifest,
        "resource_version": "1700000000", "owner": "task-9", "lease_token": "tok"})]
    service = FakeService({"download_dir": {"status": "state_unknown", "next": "resume_download_dir_do_not_resubmit"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *DIR_ARGV, "--manifest", str(path)], service)
    assert rc == cli.EXIT_NOT_OK and out["result"]["next"] == "resume_download_dir_do_not_resubmit"
    (_, _, kwargs), = service.calls  # called exactly once, no retry
    assert kwargs["owner"] == cli.OWNER and kwargs["lease_token"] is None


def test_download_dir_version_is_optional_and_never_fabricated(monkeypatch, capsys, config_file, tmp_path):
    manifest = {"files": [{"rel": "a.txt", "size": 1}]}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    i = DIR_ARGV.index("--version")
    argv = [*DIR_ARGV[:i], *DIR_ARGV[i + 2:], "--manifest", str(path)]
    assert cli.build_parser().parse_args(argv).version is None
    service = FakeService({"download_dir": {"status": "verified"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *argv], service)
    assert rc == 0 and out["ok"] is True
    assert service.calls == [("download_dir", ("op-dir",), {
        "remote_path": "/docs/pack", "resource_id": "556677", "destination": "out", "expected_manifest": manifest,
        "resource_version": None, "owner": cli.OWNER, "lease_token": None})]


def test_download_dir_requires_manifest_and_explicit_identity(monkeypatch, capsys, config_file, tmp_path):
    monkeypatch.setattr(cli, "_open_service", lambda *a: pytest.fail("service opened"))
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"files": [{"rel": "a.txt", "size": 1}]}), encoding="utf-8")
    assert cli.main(["download-dir", "--help"]) == 0
    assert cli.main(DIR_ARGV) == cli.EXIT_USAGE  # no --manifest
    for flag in ("--operation-id", "--remote-path", "--resource-id", "--destination"):
        i = DIR_ARGV.index(flag)
        argv = [*DIR_ARGV[:i], *DIR_ARGV[i + 2:], "--manifest", str(manifest)]
        assert cli.main(["--config", config_file, *argv]) == cli.EXIT_USAGE
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    for path in (bad, tmp_path / "missing.json"):
        rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *DIR_ARGV, "--manifest", str(path)])
        assert rc == cli.EXIT_USAGE and out["error"]["code"] == "input_invalid"


def test_real_service_download_dir_rejects_bad_manifest_before_native(monkeypatch, capsys, config_file, tmp_path):
    def boom(*a, **k):
        raise AssertionError("native/browser/listing must not be touched")
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"files": [{"rel": "a.txt", "size": 3, "sha256": "c" * 32}]}), encoding="utf-8")
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *DIR_ARGV, "--manifest", str(manifest)])
    assert rc == cli.EXIT_FAILURE and out["error"]["code"] == "manifest_invalid"
    assert out["error"]["receipt"]["operation"] == "download_dir_intent"
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "inspect", "op-dir"])
    assert rc == 1 and out["result"]["found"] is False


def test_upload_passes_explicit_request_once(monkeypatch, capsys, config_file, tmp_path):
    source = str(tmp_path / "u.txt")
    service = FakeService({"upload": {"status": "remote_present", "content_verified": False}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "upload", "--operation-id", "op-up",
                                           "--source", source, "--remote-dir", "/up", "--owner", "task-9",
                                           "--lease-token", "tok"], service)
    assert rc == 0 and out["ok"] is True
    assert service.calls == [("upload", ("op-up", source, "/up"), {"owner": "task-9", "lease_token": "tok"})]
    service = FakeService({"upload": {"status": "state_unknown", "next": "observe_remote_do_not_resubmit"}})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "upload", "--operation-id", "op-up",
                                           "--source", source, "--remote-dir", "/up"], service)
    assert rc == cli.EXIT_NOT_OK and out["result"]["next"] == "observe_remote_do_not_resubmit"
    (_, _, kwargs), = service.calls  # called exactly once, no retry
    assert kwargs == {"owner": cli.OWNER, "lease_token": None}
    assert cli.main(["upload", "--operation-id", "op", "--source", source]) == cli.EXIT_USAGE  # no remote dir


def test_automation_failure_prints_receipt_and_exits_4(monkeypatch, capsys, config_file):
    receipt = {"case_id": "c" * 32, "operation": "download_submit", "code": "action_expired",
               "error_type": "StepFailed", "at": 1.0, "evidence": {}}
    service = FakeService(raises=am.AutomationFailure(receipt))
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "download", "--operation-id", "op",
                                           "--remote-path", "/a", "--resource-id", "1", "--size", "1",
                                           "--destination", "out"], service)
    assert rc == cli.EXIT_FAILURE and out["error"] == {"code": "action_expired", "receipt": receipt}
    assert len(service.calls) == 1


CASE = "d" * 32


@pytest.mark.parametrize("argv, call, result, code", [
    (["maintenance-list"], ("list_maintenance", (), {}), [{"case_id": CASE, "count": 2}], 0),
    (["maintenance-inspect", CASE], ("inspect_maintenance", (CASE,), {}), {"case_id": CASE}, 0),
    (["maintenance-inspect", CASE], ("inspect_maintenance", (CASE,), {}), None, 1),
    (["maintenance-dispatch", CASE, "--workspace", "W"], ("dispatch_maintenance", (CASE, "W"), {}),
     {"case_id": CASE, "dispatch": {"state": "dispatched", "dispatch_id": "e" * 32}}, 0),
    (["maintenance-dispatch", CASE, "--workspace", "W"], ("dispatch_maintenance", (CASE, "W"), {}),
     {"case_id": CASE, "dispatch": {"state": "below_threshold"}}, 1),
    (["maintenance-dispatch", CASE, "--workspace", "W"], ("dispatch_maintenance", (CASE, "W"), {}),
     {"case_id": CASE, "dispatch": {"state": "needs_review"}}, 1),
])
def test_maintenance_commands_delegate_exactly_once(monkeypatch, capsys, config_file, argv, call, result, code):
    service = FakeService({call[0]: result})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *argv], service)
    assert rc == code and service.calls == [call] and out["command"] == argv[0]
    assert out["result"] == (result if result is not None else {"case_id": CASE, "found": False})


def test_maintenance_dispatch_requires_explicit_workspace_and_types_refusals(monkeypatch, capsys, config_file):
    monkeypatch.setattr(cli, "_open_service", lambda *a: pytest.fail("service opened"))
    assert cli.main(["--config", config_file, "maintenance-dispatch", CASE]) == cli.EXIT_USAGE
    capsys.readouterr()
    for exc, code in ((ValueError("workspace_not_separate_git_worktree"), "workspace_not_separate_git_worktree"),
                      (KeyError("case_unknown"), "case_unknown"),
                      (FileNotFoundError(2, SECRET), "workspace_unavailable")):
        rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "maintenance-dispatch", CASE,
                                               "--workspace", "W"], FakeService(raises=exc))
        assert rc == cli.EXIT_USAGE and out["error"] == {"code": "input_invalid", "message": code}
        assert SECRET not in json.dumps(out)


def test_real_service_maintenance_reads_queue_fed_by_failures_without_dispatch(monkeypatch, capsys, config_file):
    import backend.core.baidu_netdisk.maintenance as mq
    monkeypatch.setattr(mq, "_default_dispatcher", lambda *a, **k: pytest.fail("model dispatched"))
    base = ["--config", config_file]
    for _ in range(2):
        rc, out, _ = run(monkeypatch, capsys, [*base, "inspect", "bad id!"])
        assert rc == cli.EXIT_FAILURE
    rc, out, _ = run(monkeypatch, capsys, [*base, "maintenance-list"])
    assert rc == 0 and len(out["result"]) == 1 and out["result"][0]["count"] == 2
    case_id = out["result"][0]["case_id"]
    rc, out, _ = run(monkeypatch, capsys, [*base, "maintenance-inspect", case_id])
    assert rc == 0 and out["result"]["ready"] is True and out["result"]["dispatch"] is None
    rc, out, _ = run(monkeypatch, capsys, [*base, "maintenance-inspect", "not-a-case"])
    assert rc == cli.EXIT_USAGE and out["error"]["message"] == "case_id_invalid"


@pytest.mark.parametrize("argv, call, result, code", [
    (["ensure-directory", "--operation-id", "op-d", "--path", "/A/b", "--anchor-path", "/A",
      "--expected-anchor-id", "a1"], ("ensure_directory", ("op-d", "/A/b", "/A", "a1"), {}),
     {"status": "completed", "id": "n1"}, 0),
    (["ensure-directory", "--operation-id", "op-r", "--path", "/b", "--anchor-path", "/"],
     ("ensure_directory", ("op-r", "/b", "/", None), {}), {"status": "completed"}, 0),
    (["ensure-directory", "--operation-id", "op-d", "--path", "/A/b", "--anchor-path", "/A"],
     ("ensure_directory", ("op-d", "/A/b", "/A", None), {}),
     {"status": "invalid", "reason": "expected_anchor_id_required"}, 1),
    (["ensure-directory", "--operation-id", "op-d", "--path", "/A/b", "--anchor-path", "/A",
      "--expected-anchor-id", "a1"], ("ensure_directory", ("op-d", "/A/b", "/A", "a1"), {}),
     {"status": "needs_review", "do_not_replay": True}, 1),
    (["ensure-directory", "--operation-id", "op-d", "--path", "/A/b", "--anchor-path", "/A",
      "--expected-anchor-id", "a1"], ("ensure_directory", ("op-d", "/A/b", "/A", "a1"), {}),
     {"status": "conflict", "reason": "operation_id_reused"}, 1),
    (["inspect-directory", "op-d"], ("inspect_directory", ("op-d",), {}), {"status": "found"}, 0),
    (["inspect-directory", "op-d"], ("inspect_directory", ("op-d",), {}), {"status": "not_found"}, 1),
    (["reconcile-directory", "op-d"], ("reconcile_directory", ("op-d",), {}), {"status": "completed"}, 0),
    (["reconcile-directory", "op-d"], ("reconcile_directory", ("op-d",), {}), {"status": "needs_review"}, 1),
    (["reconcile-directory", "op-d"], ("reconcile_directory", ("op-d",), {}), {"status": "refused"}, 1),
])
def test_directory_commands_delegate_once_with_honest_exit(monkeypatch, capsys, config_file, argv, call, result,
                                                           code):
    service = FakeService({call[0]: result})
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, *argv], service)
    assert rc == code and service.calls == [call]
    assert out["ok"] is (code == 0) and out["result"] == result and out["command"] == argv[0]


def test_ensure_directory_requires_explicit_intent(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_automation_config", lambda *a: pytest.fail("config loaded"))
    assert cli.main(["ensure-directory", "--operation-id", "op", "--path", "/A/b"]) == cli.EXIT_USAGE
    assert cli.main(["ensure-directory", "--path", "/A/b", "--anchor-path", "/A"]) == cli.EXIT_USAGE
    assert cli.main(["inspect-directory"]) == cli.EXIT_USAGE


def test_real_service_directory_commands_never_touch_native_or_dispatch(monkeypatch, capsys, config_file):
    from backend.core.baidu_netdisk import directory_operations as dop

    def boom(*a, **k):
        raise AssertionError("native/mutator/dispatch must not be touched")
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.MaintenanceQueue, "dispatch", boom)
    monkeypatch.setattr(dop.web_directories, "ensure_directory", boom)
    monkeypatch.setattr(dop.listing, "resolve_remote", lambda *a, **k: {"status": "missing", "at": "/A"})
    base = ["--config", config_file]
    rc, out, _ = run(monkeypatch, capsys, [*base, "inspect-directory", "op-d"])
    assert rc == 1 and out["result"]["status"] == "not_found"
    rc, out, _ = run(monkeypatch, capsys, [*base, "reconcile-directory", "op-d"])
    assert rc == 1 and out["result"]["status"] == "not_found"
    rc, out, _ = run(monkeypatch, capsys, [*base, "ensure-directory", "--operation-id", "op-d", "--path", "/A/b",
                                           "--anchor-path", "/A", "--expected-anchor-id", "a1"])
    assert rc == 1 and out["result"]["status"] == "refused"  # anchor unproven: nothing submitted
    rc, out, _ = run(monkeypatch, capsys, [*base, "ensure-directory", "--operation-id", "op-d", "--path", "/A/b",
                                           "--anchor-path", "/A", "--expected-anchor-id", "a1"])
    assert rc == 1 and out["result"]["status"] == "refused"  # duplicate only reads back the stored refusal
    rc, out, _ = run(monkeypatch, capsys, [*base, "inspect-directory", "op-d"])
    assert rc == 0 and out["result"]["status"] == "found" and out["result"]["phase"] == "anchor_refused"


def test_unexpected_error_reports_type_only(monkeypatch, capsys, config_file):
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "reconcile", "op-1"],
                     FakeService(raises=RuntimeError(SECRET)))
    assert rc == cli.EXIT_ERROR and out["error"] == {"code": "unexpected_error", "error_type": "RuntimeError"}
    rc, out, _ = run(monkeypatch, capsys, ["--config", config_file, "reconcile", "op-1"],
                     FakeService(raises=ac.DestinationError(SECRET)))
    assert rc == cli.EXIT_CONFIG  # typed config errors stay config errors


def test_real_service_offline_commands_never_touch_native(monkeypatch, capsys, config_file):
    def boom(*a, **k):
        raise AssertionError("native/browser/listing must not be touched")
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.listing, "list_directory", boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    base = ["--config", config_file]
    rc, out, _ = run(monkeypatch, capsys, [*base, "inspect", "op-1"])
    assert rc == 1 and out["result"] == {"operation_id": "op-1", "found": False}
    rc, out, _ = run(monkeypatch, capsys, [*base, "reconcile", "op-1"])
    assert rc == 1 and out["result"]["status"] == "unknown_operation"
    rc, out, _ = run(monkeypatch, capsys, [*base, "verify", "../escape"])
    assert rc == cli.EXIT_FAILURE and out["error"]["code"] == "destination_invalid"
    rc, out, _ = run(monkeypatch, capsys, [*base, "inspect", "bad id!"])
    assert rc == cli.EXIT_FAILURE and out["error"]["receipt"]["operation"] == "inspect"
    assert SECRET not in json.dumps(out)
    rc, out, _ = run(monkeypatch, capsys, [*base, "inspect-upload", "op-1"])
    assert rc == 1 and out["result"] == {"operation_id": "op-1", "found": False}
    rc, out, _ = run(monkeypatch, capsys, [*base, "reconcile-upload", "op-1"])
    assert rc == 1 and out["result"]["status"] == "unknown_operation" and out["result"]["next"] == am._REVIEW
    rc, out, _ = run(monkeypatch, capsys, [*base, "upload", "--operation-id", "op-1", "--source",
                                           str(Path(config_file).parent / "missing.txt"), "--remote-dir", "/up"])
    assert rc == cli.EXIT_FAILURE and out["error"]["code"] == "source_missing"
    assert out["error"]["receipt"]["operation"] == "upload_intent"
