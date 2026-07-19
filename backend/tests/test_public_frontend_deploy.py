from __future__ import annotations

from pathlib import Path

from backend.core.runtime import public_frontend_deploy
from backend.core.services import _subprocess as subprocess_utils


def test_node_npm_command_avoids_npm_cmd_on_windows(monkeypatch, tmp_path):
    npm_dir = tmp_path / "node"
    npm_cli = npm_dir / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    npm_cli.write_text("", encoding="utf-8")
    npm_cmd = npm_dir / "npm.cmd"
    npm_cmd.write_text("", encoding="utf-8")
    node = npm_dir / "node.exe"
    node.write_text("", encoding="utf-8")

    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(
        subprocess_utils.shutil,
        "which",
        lambda name: str(node) if name == "node.exe" else str(npm_cmd) if name == "npm.cmd" else None,
    )

    command = subprocess_utils.node_npm_command("run", "build")

    assert command == [str(node), str(npm_cli), "run", "build"]
    assert all(Path(part).name.lower() != "npm.cmd" for part in command)


def test_build_frontend_runs_vite_directly_without_npm_script(monkeypatch, tmp_path):
    frontend = tmp_path / "frontend"
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    log = tmp_path / "build.log"
    calls = []

    monkeypatch.setattr(public_frontend_deploy, "_frontend_dir", lambda: frontend)
    monkeypatch.setattr(public_frontend_deploy, "_log_path", lambda: log)
    monkeypatch.setattr(
        public_frontend_deploy,
        "node_script_command",
        lambda script, *args: ["node.exe", str(script), *args],
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return public_frontend_deploy.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(public_frontend_deploy, "run_quiet", fake_run)

    public_frontend_deploy._build_frontend(timeout_seconds=5)

    assert calls
    assert calls[0][0] == ["node.exe", str(vite), "build", "--manifest"]
    assert "npm" not in " ".join(calls[0][0]).lower()
