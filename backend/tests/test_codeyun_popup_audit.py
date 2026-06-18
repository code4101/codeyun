from __future__ import annotations

from pathlib import Path

from scripts.codeyun_popup_audit import is_codeyun_event


def test_codex_app_shell_tool_chain_is_not_counted_as_codeyun_service_event():
    event = {
        "title": "",
        "class": "PseudoConsoleWindow",
        "chain": [
            {
                "name": "codex.exe",
                "cmdline": [
                    "C:\\Users\\kzche\\scoop\\persist\\nodejs-lts\\bin\\node_modules\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\x86_64-pc-windows-msvc\\codex\\codex.exe",
                    "exec",
                ],
                "cwd": "D:\\home\\chenkunze\\data\\m2603codeyun\\codepc_mf\\codex-cli-workspace",
            },
            {
                "name": "python.exe",
                "cmdline": ["D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\python.exe", "-"],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            },
            {
                "name": "pwsh.exe",
                "cmdline": [
                    "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
                    "-Command",
                    "from backend.core.maintenance.idle_maintenance import run_idle_maintenance_once",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            },
            {
                "name": "codex.exe",
                "cmdline": [
                    "C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.611.8604.0_x64__2p2nqsd0c76g0\\app\\resources\\codex.exe",
                    "app-server",
                    "--analytics-default-enabled",
                ],
                "cwd": "C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.611.8604.0_x64__2p2nqsd0c76g0\\app",
            },
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False


def test_codeyun_dev_runner_event_is_counted():
    event = {
        "title": "",
        "class": "PseudoConsoleWindow",
        "chain": [
            {
                "name": "pythonw.exe",
                "cmdline": ["D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe", "dev.py"],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True
