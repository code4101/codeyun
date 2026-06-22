from __future__ import annotations

from pathlib import Path

from scripts.codeyun_popup_audit import is_codeyun_event
from scripts.codeyun_popup_audit import is_codeyun_workspace_event


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


def test_codeyun_nearby_process_evidence_is_counted():
    event = {
        "title": "Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "pythonw.exe",
                "cmdline": [
                    "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe",
                    "D:\\home\\chenkunze\\slns\\codeyun\\dev.py",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True


def test_external_terminal_with_no_nearby_codeyun_evidence_is_not_counted():
    event = {
        "title": "Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "git.exe",
                "cmdline": ["git", "status"],
                "cwd": "D:\\home\\chenkunze\\slns\\other-project",
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False


def test_external_git_in_codeyun_cwd_is_not_counted_by_cwd_only():
    event = {
        "title": "C:\\Program Files\\Git\\cmd\\git.exe",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "git.exe",
                "cmdline": ["git.exe", "-c", "core.hooksPath=NUL", "status", "--porcelain"],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            },
            {
                "name": "pythonw.exe",
                "cmdline": [
                    "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe",
                    "-m",
                    "backend.core.runtime.uvicorn_hidden",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            },
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False
    assert is_codeyun_workspace_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True


def test_git_window_with_codeyun_parent_marker_is_counted():
    event = {
        "title": "git.exe",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "git.exe",
                "cmdline": ["git.exe", "status", "--porcelain"],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
                "parent": {
                    "name": "pythonw.exe",
                    "cmdline": [
                        "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe",
                        "dev.py",
                    ],
                    "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
                },
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True


def test_generic_terminal_prefers_external_codex_evidence_over_nearby_codeyun_service():
    event = {
        "title": "Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "powershell.exe",
                "cmdline": [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | ConvertTo-Json",
                ],
                "cwd": "C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.0\\app",
                "parent": {
                    "name": "Codex.exe",
                    "cmdline": ["C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.0\\app\\Codex.exe"],
                    "cwd": "C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.0\\app",
                },
            },
            {
                "name": "pythonw.exe",
                "cmdline": [
                    "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe",
                    "-m",
                    "backend.core.runtime.uvicorn_hidden",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            },
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False
    assert is_codeyun_workspace_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True


def test_generic_terminal_with_only_uvicorn_worker_nearby_is_not_counted():
    event = {
        "title": "Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "pythonw.exe",
                "cmdline": [
                    "C:\\Users\\kzche\\AppData\\Roaming\\uv\\python\\cpython-3.13.11-windows-x86_64-none\\pythonw.exe",
                    "-c",
                    "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=52512, pipe_handle=1924)",
                    "--multiprocessing-fork",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
                "parent": {
                    "name": "pythonw.exe",
                    "cmdline": [
                        "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\pythonw.exe",
                        "-m",
                        "backend.core.runtime.uvicorn_hidden",
                    ],
                    "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
                },
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False
    assert is_codeyun_workspace_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False


def test_terminal_with_codeyun_cmd_cwd_is_workspace_event():
    event = {
        "title": "Windows Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "cmd.exe",
                "cmdline": ["cmd.exe", "/c"],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False
    assert is_codeyun_workspace_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True


def test_fanxiu_watch_doctor_terminal_is_codeyun_service_event():
    event = {
        "title": "Terminal",
        "class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "chain": [
            {
                "name": "WindowsTerminal.exe",
                "cmdline": ["WindowsTerminal.exe", "-Embedding"],
                "cwd": "C:\\WINDOWS\\system32",
            }
        ],
        "nearby_processes": [
            {
                "name": "python.exe",
                "cmdline": [
                    "D:\\home\\chenkunze\\slns\\codeyun\\.venv\\Scripts\\python.exe",
                    "scripts/fanxiu_bt.py",
                    "watch-doctor",
                    "--max-iterations",
                    "1",
                    "--auto-run-due",
                ],
                "cwd": "D:\\home\\chenkunze\\slns\\codeyun",
            }
        ],
    }

    assert is_codeyun_event(event, Path("D:/home/chenkunze/slns/codeyun")) is True
    assert is_codeyun_workspace_event(event, Path("D:/home/chenkunze/slns/codeyun")) is False
