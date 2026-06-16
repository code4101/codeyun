from __future__ import annotations

from pathlib import Path

from backend.core.runtime import subprocess_utils


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
