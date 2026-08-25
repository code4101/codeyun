from __future__ import annotations

import socket
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


def test_ssh_config_reads_direct_interface_index(monkeypatch):
    monkeypatch.setenv("YUN_SERVER_HOST", "203.0.113.10")
    monkeypatch.setenv("YUN_USER_PASS_CHENKUNZE", "test-password")
    monkeypatch.setenv("YUN_SERVER_DIRECT_INTERFACE_INDEX", "8")
    monkeypatch.setattr(public_frontend_deploy, "_load_env_file", lambda: None)

    config = public_frontend_deploy._ssh_config()

    assert config["direct_interface_index"] == 8


def test_ssh_config_falls_back_to_xlproject_service_config(monkeypatch):
    for name in (
        "YUN_SERVER_HOST",
        "YUN_SERVER_PORT",
        "YUN_USER_CHENKUNZE",
        "YUN_SERVER_USER",
        "YUN_USER_PASS_CHENKUNZE",
        "YUN_SERVER_PASS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(public_frontend_deploy, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        public_frontend_deploy,
        "_shared_ssh_password",
        lambda username: "shared-password" if username == "chenkunze" else None,
    )

    config = public_frontend_deploy._ssh_config()

    assert config["host"] == "code4101.com"
    assert config["port"] == 22
    assert config["username"] == "chenkunze"
    assert config["password"] == "shared-password"


def test_load_env_file_reads_shared_xlproject_env_before_codeyun_env(monkeypatch, tmp_path):
    root = tmp_path / "codeyun"
    shared_env = tmp_path / "xlproject" / ".env"
    shared_env.parent.mkdir(parents=True)
    shared_env.write_text("XL_SERVICES=[]\n", encoding="utf-8")
    root.mkdir()
    codeyun_env = root / ".env"
    codeyun_env.write_text("CODEYUN_TEST=1\n", encoding="utf-8")
    loaded = []

    monkeypatch.setattr(public_frontend_deploy, "ROOT_DIR", root)
    monkeypatch.setattr("dotenv.load_dotenv", lambda path: loaded.append(Path(path)))

    public_frontend_deploy._load_env_file()

    assert loaded == [shared_env, codeyun_env]


def test_open_direct_socket_pins_windows_interface(monkeypatch):
    calls = []

    class FakeSocket:
        def settimeout(self, value):
            calls.append(("timeout", value))

        def setsockopt(self, level, option, value):
            calls.append(("setsockopt", level, option, value))

        def connect(self, address):
            calls.append(("connect", address))

        def close(self):
            calls.append(("close",))

    fake_socket = FakeSocket()
    monkeypatch.setattr(public_frontend_deploy.os, "name", "nt")
    monkeypatch.setattr(
        public_frontend_deploy.socket,
        "getaddrinfo",
        lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.10", 22))],
    )
    monkeypatch.setattr(public_frontend_deploy.socket, "socket", lambda *args: fake_socket)

    result = public_frontend_deploy._open_direct_socket("yun.example", 22, 8)

    assert result is fake_socket
    assert ("setsockopt", socket.IPPROTO_IP, 31, socket.htonl(8)) in calls
    assert ("connect", ("203.0.113.10", 22)) in calls
