import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk import automation_config as ac


def make(tmp_path, **overrides):
    values = dict(profile="main", scene_ids=["home", "share.save"],
                  client_download_dir=tmp_path / "dl", local_root=tmp_path / "local",
                  paths=ac.AutomationPaths(tmp_path / "auto"))
    values.update(overrides)
    return ac.AutomationConfig(**values)


def test_paths_derive_from_single_root(tmp_path):
    p = ac.AutomationPaths(tmp_path)
    assert p.assets_dir == tmp_path / "assets"
    assert p.transfers_file == tmp_path / "transfers.json"
    assert p.operations_dir == tmp_path / "operations"
    assert p.failures_dir == tmp_path / "failures"
    assert p.config_file == tmp_path / "config.json"


def test_string_root_normalized_and_relative_rejected(tmp_path):
    p = ac.AutomationPaths(str(tmp_path))
    assert p.root == tmp_path and p.assets_dir == tmp_path / "assets"
    for bad in ["rel", "", None]:
        with pytest.raises(ac.AutomationConfigError):
            ac.AutomationPaths(bad)


@pytest.mark.parametrize("overrides", [
    {"profile": " "}, {"scene_ids": "home"}, {"scene_ids": ["a", "a"]}, {"scene_ids": []},
    {"scene_ids": ["bad id"]}, {"scene_ids": [1]},
    {"client_download_dir": "rel/dl"}, {"local_root": ""},
    {"paths": str(Path("x").absolute())},
])
def test_validation_rejects(tmp_path, overrides):
    with pytest.raises(ac.AutomationConfigError):
        make(tmp_path, **overrides)


def test_write_read_roundtrip_and_no_overwrite(tmp_path):
    cfg = make(tmp_path)
    file = ac.write_automation_config(cfg)
    assert file == cfg.paths.config_file
    assert not [f for f in file.parent.iterdir() if f.name.endswith(".tmp")]
    data = json.loads(file.read_text(encoding="utf-8"))
    assert data["version"] == ac.CONFIG_VERSION and data["root"] == str(tmp_path / "auto")
    assert ac.load_automation_config(file) == cfg
    with pytest.raises(FileExistsError):
        ac.write_automation_config(make(tmp_path, profile="other"))
    assert ac.load_automation_config(file).profile == "main"
    ac.write_automation_config(make(tmp_path, profile="other"), overwrite=True)
    assert ac.load_automation_config(file).profile == "other"


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    cfg = make(tmp_path)
    file = ac.write_automation_config(cfg)
    monkeypatch.setattr(ac.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        ac.write_automation_config(make(tmp_path, profile="x"), overwrite=True)
    assert ac.load_automation_config(file).profile == "main"
    assert sorted(f.name for f in file.parent.iterdir()) == ["config.json"]


def test_concurrent_default_init_exactly_one_wins(tmp_path, monkeypatch):
    real_link = ac.os.link
    barrier = threading.Barrier(2, timeout=10)

    def synced_link(src, dst):
        barrier.wait()  # both have passed the exists check and written their temp file
        real_link(src, dst)

    monkeypatch.setattr(ac.os, "link", synced_link)
    results = {}

    def init(profile):
        try:
            ac.write_automation_config(make(tmp_path, profile=profile))
            results[profile] = "ok"
        except FileExistsError:
            results[profile] = "exists"

    threads = [threading.Thread(target=init, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["exists", "ok"]
    winner = next(p for p, r in results.items() if r == "ok")
    file = tmp_path / "auto" / "config.json"
    assert ac.load_automation_config(file).profile == winner
    assert sorted(f.name for f in file.parent.iterdir()) == ["config.json"]


def test_default_write_never_clobbers_file_created_after_check(tmp_path, monkeypatch):
    file = tmp_path / "auto" / "config.json"
    real_link = ac.os.link

    def racing_link(src, dst):
        file.write_text("other", encoding="utf-8")
        real_link(src, dst)

    monkeypatch.setattr(ac.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        ac.write_automation_config(make(tmp_path))
    assert file.read_text(encoding="utf-8") == "other"
    assert sorted(f.name for f in file.parent.iterdir()) == ["config.json"]


@pytest.mark.parametrize("content",["{not json", "[]", '{"version": 2}',
                                     '{"version": 1, "profile": "p"}'])
def test_load_typed_errors(tmp_path, content):
    file = tmp_path / "config.json"
    file.write_text(content, encoding="utf-8")
    with pytest.raises(ac.AutomationConfigError):
        ac.load_automation_config(file)
    with pytest.raises(ac.AutomationConfigError):
        ac.load_automation_config(tmp_path / "missing.json")


def test_destination_stays_under_local_root(tmp_path):
    cfg = make(tmp_path)
    assert cfg.resolve_destination("a/b.txt") == (tmp_path / "local" / "a" / "b.txt").resolve()
    for bad in ["../x", "a/../../x", "a\\..\\..\\x", str(tmp_path / "other"), "", "C:/x"]:
        with pytest.raises(ac.DestinationError):
            cfg.resolve_destination(bad)


def test_explicit_path_never_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "bootstrap_runtime", lambda: pytest.fail("bootstrapped"))
    ac.load_automation_config(ac.write_automation_config(make(tmp_path)))


@pytest.mark.parametrize("error", [ImportError("inner"),
                                   ModuleNotFoundError("dep", name="dotenv")])
def test_bootstrap_inner_import_error_not_replayed(monkeypatch, error):
    calls = []

    def fake_import(name):
        calls.append(name)
        raise error

    import backend.core.agents.policy as policy
    monkeypatch.setattr(ac.importlib, "import_module", fake_import)
    monkeypatch.setattr(policy, "load_config", lambda: pytest.fail("fell back"))
    with pytest.raises(ImportError) as info:
        ac.bootstrap_runtime()
    assert info.value is error and calls == ["xlproject.loadenv"]


@pytest.mark.parametrize("missing", ["xlproject", "xlproject.loadenv"])
def test_bootstrap_falls_back_to_sibling_xlproject(tmp_path, monkeypatch, missing):
    calls = []

    def fake_import(name):
        calls.append(name)
        if len(calls) == 1:
            raise ModuleNotFoundError(missing, name=missing)

    import backend.core.agents.policy as policy
    import backend.core.settings as settings
    monkeypatch.setattr(ac.importlib, "import_module", fake_import)
    monkeypatch.setattr(policy, "load_config", lambda: {"codeyun_root": str(tmp_path / "codeyun")})
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path / "d"))
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    assert ac.default_paths().root == tmp_path / "d" / "baidu-netdisk" / "automation"
    assert calls == ["xlproject.loadenv", "xlproject.loadenv"]
    assert sys.path[:len(before)] == before
    assert sys.path[len(before):] == [str(tmp_path / "xlproject" / "src")]
