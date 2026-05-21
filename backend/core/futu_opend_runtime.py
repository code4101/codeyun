from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import psutil

from backend.core.device import build_background_popen_kwargs, process_candidates_by_name


FUTU_OPEND_SERVICE_KEY = "futu_opend"
FUTU_OPEND_TITLE = "Futu OpenD"
DEFAULT_FUTU_OPEND_HOST = "127.0.0.1"
DEFAULT_FUTU_OPEND_PORT = 11111
FUTU_OPEND_DOWNLOAD_URL = "https://support.futunn.com/en/topic464/"
FUTU_OPEND_DOC_URL = "https://openapi.futunn.com/futu-api-doc/opend/opend-intro.html"
FUTU_OPEND_EXE_ENV_KEYS = ("FUTU_OPEND_EXE", "FUTU_OPEND_PATH")
FUTU_OPEND_PROCESS_NAMES = {"futuopend.exe", "futu_opend.exe"}
FUTU_OPEND_CANDIDATE_PROCESS_NAMES = FUTU_OPEND_PROCESS_NAMES | {"opend.exe"}


class FutuOpenDError(RuntimeError):
    pass


@dataclass(frozen=True)
class FutuOpenDProcess:
    pid: int
    name: str
    exe: str
    cmdline: str
    started_at: float | None = None


def is_tcp_port_open(host: str = DEFAULT_FUTU_OPEND_HOST, port: int = DEFAULT_FUTU_OPEND_PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_futu_opend_available(
    *,
    host: str = DEFAULT_FUTU_OPEND_HOST,
    port: int = DEFAULT_FUTU_OPEND_PORT,
    auto_start: bool = True,
    wait_seconds: float = 15.0,
) -> dict[str, Any]:
    if is_tcp_port_open(host, port):
        return get_futu_opend_status(host=host, port=port)
    if not auto_start:
        raise FutuOpenDError(f"无法连接 Futu OpenD：{host}:{port}，请先在运行管理里启动 OpenD。")

    return start_futu_opend(host=host, port=port, wait_seconds=wait_seconds)["service"]


def get_futu_opend_status(
    *,
    host: str = DEFAULT_FUTU_OPEND_HOST,
    port: int = DEFAULT_FUTU_OPEND_PORT,
) -> dict[str, Any]:
    processes = list_futu_opend_processes()
    executable_path, executable_source = discover_futu_opend_executable(processes=processes)
    configured_path = _configured_executable_path()
    running = is_tcp_port_open(host, port)
    configured = bool(executable_path)

    state = "running" if running else ("stopped" if configured else "unconfigured")
    state_label = {
        "running": "运行中",
        "stopped": "已配置",
        "unconfigured": "未配置",
    }[state]
    last_error = ""
    if configured_path and not Path(configured_path).is_file():
        last_error = f"配置的 OpenD 路径不存在：{configured_path}"
    elif not running and not configured:
        last_error = "未发现 FutuOpenD.exe；请从富途官方入口安装 OpenD，或设置 FUTU_OPEND_EXE 指向已解压的 FutuOpenD.exe。"

    return {
        "key": FUTU_OPEND_SERVICE_KEY,
        "title": FUTU_OPEND_TITLE,
        "host": host,
        "port": int(port),
        "endpoint": f"{host}:{int(port)}",
        "running": running,
        "state": state,
        "state_label": state_label,
        "configured": configured,
        "configured_path": configured_path,
        "executable_path": executable_path,
        "executable_source": executable_source,
        "download_url": FUTU_OPEND_DOWNLOAD_URL,
        "doc_url": FUTU_OPEND_DOC_URL,
        "process_count": len(processes),
        "processes": [asdict(process) for process in processes],
        "pids": [process.pid for process in processes],
        "last_error": last_error,
    }


def start_futu_opend(
    *,
    host: str = DEFAULT_FUTU_OPEND_HOST,
    port: int = DEFAULT_FUTU_OPEND_PORT,
    wait_seconds: float = 15.0,
) -> dict[str, Any]:
    if is_tcp_port_open(host, port):
        return {"status": "started", "service": get_futu_opend_status(host=host, port=port)}

    executable_path, _source = discover_futu_opend_executable()
    if not executable_path:
        status = get_futu_opend_status(host=host, port=port)
        raise FutuOpenDError(status.get("last_error") or "未找到 Futu OpenD 可执行文件。")

    exe_path = Path(executable_path)
    try:
        proc = subprocess.Popen(  # noqa: S603 - executable path is explicit and shell is not used.
            [os.fspath(exe_path)],
            cwd=os.fspath(exe_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **build_background_popen_kwargs(independent=True),
        )
    except OSError as exc:
        raise FutuOpenDError(f"启动 Futu OpenD 失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        if is_tcp_port_open(host, port):
            status = get_futu_opend_status(host=host, port=port)
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    status = get_futu_opend_status(host=host, port=port)
    raise FutuOpenDError(
        f"已启动 Futu OpenD 进程 PID {proc.pid}，但 {host}:{port} 未在 {int(wait_seconds)} 秒内就绪。"
    )


def stop_futu_opend(
    *,
    host: str = DEFAULT_FUTU_OPEND_HOST,
    port: int = DEFAULT_FUTU_OPEND_PORT,
    timeout: float = 5.0,
) -> dict[str, Any]:
    targets = list(_iter_futu_opend_psutil_processes())
    if not targets:
        if is_tcp_port_open(host, port):
            raise FutuOpenDError("OpenD 端口仍在监听，但未找到可安全停止的 FutuOpenD 进程。")
        return {"status": "stopped", "service": get_futu_opend_status(host=host, port=port)}

    for proc in targets:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            raise FutuOpenDError(f"停止 Futu OpenD 失败：{exc}") from exc

    _gone, alive = psutil.wait_procs(targets, timeout=max(0.1, float(timeout)))
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            raise FutuOpenDError(f"强制停止 Futu OpenD 失败：{exc}") from exc
    if alive:
        psutil.wait_procs(alive, timeout=2)

    return {"status": "stopped", "service": get_futu_opend_status(host=host, port=port)}


def discover_futu_opend_executable(
    *,
    processes: Iterable[FutuOpenDProcess] | None = None,
) -> tuple[str, str]:
    for path, source in _configured_executable_candidates():
        if path.is_file():
            return os.fspath(path), source

    for process in processes if processes is not None else list_futu_opend_processes():
        if process.exe and Path(process.exe).is_file():
            return process.exe, "process"

    path_from_path = shutil.which("FutuOpenD") or shutil.which("FutuOpenD.exe")
    if path_from_path:
        return path_from_path, "path"

    for path, source in _registry_executable_candidates():
        if path.is_file():
            return os.fspath(path), source

    for path in _common_executable_candidates():
        if path.is_file():
            return os.fspath(path), "common-path"

    return "", ""


def list_futu_opend_processes() -> list[FutuOpenDProcess]:
    processes: list[FutuOpenDProcess] = []
    for proc in _iter_futu_opend_psutil_processes():
        info = _process_info(proc)
        if info is not None:
            processes.append(info)
    return sorted(processes, key=lambda item: item.pid)


def _iter_futu_opend_psutil_processes():
    for proc in process_candidates_by_name(FUTU_OPEND_CANDIDATE_PROCESS_NAMES):
        try:
            if _matches_futu_opend_process(proc):
                yield proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue


def _process_info(proc: psutil.Process) -> FutuOpenDProcess | None:
    try:
        cmdline = proc.cmdline()
        try:
            started_at = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            started_at = None
        return FutuOpenDProcess(
            pid=int(proc.pid),
            name=str(proc.name() or ""),
            exe=str(proc.exe() or ""),
            cmdline=" ".join(str(part) for part in cmdline),
            started_at=float(started_at) if started_at else None,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError, TypeError):
        return None


def _matches_futu_opend_process(proc: psutil.Process) -> bool:
    try:
        name = str(proc.name() or "").strip().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        name = ""
    try:
        exe = str(proc.exe() or "").strip().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        exe = ""
    try:
        cmdline = " ".join(str(part) for part in proc.cmdline()).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        cmdline = ""
    exe_name = Path(exe).name.lower()
    if name in FUTU_OPEND_PROCESS_NAMES or exe_name in FUTU_OPEND_PROCESS_NAMES:
        return True
    if name == "opend.exe" or exe_name == "opend.exe":
        return "futu" in exe or "futu" in cmdline
    return "futuopend.exe" in cmdline or "futu_opend.exe" in cmdline


def _configured_executable_path() -> str:
    for env_key in FUTU_OPEND_EXE_ENV_KEYS:
        value = os.getenv(env_key)
        if value and value.strip():
            return os.path.abspath(os.path.expandvars(os.path.expanduser(value.strip().strip('"'))))
    return ""


def _configured_executable_candidates() -> Iterable[tuple[Path, str]]:
    for env_key in FUTU_OPEND_EXE_ENV_KEYS:
        value = os.getenv(env_key)
        if value and value.strip():
            yield Path(os.path.abspath(os.path.expandvars(os.path.expanduser(value.strip().strip('"'))))), f"env:{env_key}"


def _registry_executable_candidates() -> Iterable[tuple[Path, str]]:
    if sys.platform != "win32":
        return []

    import winreg

    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    candidates: list[tuple[Path, str]] = []
    for hive, root_path in roots:
        try:
            root = winreg.OpenKey(hive, root_path)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    subkey = winreg.OpenKey(root, subkey_name)
                except OSError:
                    continue
                with subkey:
                    display_name = _read_registry_text(subkey, "DisplayName")
                    if "opend" not in display_name.lower():
                        continue
                    for value_name in ("DisplayIcon", "InstallLocation"):
                        value = _read_registry_text(subkey, value_name)
                        candidates.extend((path, "registry") for path in _registry_value_paths(value))
    return candidates


def _read_registry_text(key: Any, value_name: str) -> str:
    try:
        value, _kind = __import__("winreg").QueryValueEx(key, value_name)
    except OSError:
        return ""
    return str(value or "").strip()


def _registry_value_paths(value: str) -> Iterable[Path]:
    text = value.strip()
    if not text:
        return []
    if text.startswith('"'):
        end = text.find('"', 1)
        text = text[1:end] if end > 1 else text.strip('"')
    if "," in text and text.lower().endswith((".exe,0", ".exe,1")):
        text = text.rsplit(",", 1)[0]
    path = Path(os.path.expandvars(text))
    if path.suffix.lower() == ".exe":
        return [path]
    return [path / "FutuOpenD.exe", path / "OpenD.exe"]


def _common_executable_candidates() -> Iterable[Path]:
    raw_paths = [
        r"C:\FutuOpenD\FutuOpenD.exe",
        r"C:\Program Files\FutuOpenD\FutuOpenD.exe",
        r"C:\Program Files (x86)\FutuOpenD\FutuOpenD.exe",
        r"C:\Program Files\Futu OpenD\FutuOpenD.exe",
        r"C:\Program Files (x86)\Futu OpenD\FutuOpenD.exe",
    ]
    for env_key in ("LOCALAPPDATA", "APPDATA", "PROGRAMDATA"):
        base = os.getenv(env_key)
        if base:
            raw_paths.extend(
                [
                    os.path.join(base, "FutuOpenD", "FutuOpenD.exe"),
                    os.path.join(base, "Programs", "FutuOpenD", "FutuOpenD.exe"),
                    os.path.join(base, "Futu", "FutuOpenD", "FutuOpenD.exe"),
                ]
            )
    return [Path(os.path.expandvars(path)) for path in raw_paths]
