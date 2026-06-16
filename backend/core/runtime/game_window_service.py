from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from pyxllib.prog import process_runtime

from backend.core.runtime.process_launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings


GAME_WINDOW_SERVICE_KEY = "fanxiu-game-window"
GAME_WINDOW_SERVICE_TITLE = "凡修画面流"
DEFAULT_GAME_WINDOW_SERVICE_HOST = "127.0.0.1"
DEFAULT_GAME_WINDOW_SERVICE_PORT = 8766
GAME_WINDOW_SERVICE_MODULE = "backend.services.game_window_daemon"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


class GameWindowServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameWindowServiceProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def get_game_window_service_host() -> str:
    return (
        os.getenv("CODEYUN_GAME_WINDOW_SERVICE_HOST") or DEFAULT_GAME_WINDOW_SERVICE_HOST
    ).strip() or DEFAULT_GAME_WINDOW_SERVICE_HOST


def get_game_window_service_port() -> int:
    try:
        return int(os.getenv("CODEYUN_GAME_WINDOW_SERVICE_PORT") or DEFAULT_GAME_WINDOW_SERVICE_PORT)
    except ValueError:
        return DEFAULT_GAME_WINDOW_SERVICE_PORT


def get_game_window_service_base_url() -> str:
    configured = (os.getenv("CODEYUN_GAME_WINDOW_SERVICE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://{get_game_window_service_host()}:{get_game_window_service_port()}"


def get_game_window_service_log_path() -> Path:
    configured = (os.getenv("CODEYUN_GAME_WINDOW_SERVICE_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "logs" / "game-window-service.log").resolve(strict=False)


def _endpoint(path: str) -> str:
    return f"{get_game_window_service_base_url()}{path}"


def _request_timeout(default: float = 3.0) -> float:
    try:
        return float(os.getenv("CODEYUN_GAME_WINDOW_SERVICE_TIMEOUT") or default)
    except ValueError:
        return default


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except Exception:
        return []


def _safe_name(proc: Any) -> str:
    try:
        return str(proc.name() or "")
    except Exception:
        return ""


def _safe_ppid(proc: Any) -> int | None:
    try:
        return int(proc.ppid())
    except Exception:
        return None


def _safe_started_at(proc: Any) -> float | None:
    try:
        return float(proc.create_time())
    except Exception:
        return None


def _matches_game_window_service_process(proc: Any) -> bool:
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    for index, part in enumerate(cmdline[:-1]):
        if part == "-m" and cmdline[index + 1] == GAME_WINDOW_SERVICE_MODULE:
            return True
    return GAME_WINDOW_SERVICE_MODULE in " ".join(cmdline)


def list_game_window_service_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[GameWindowServiceProcess] = []
    for proc in process_runtime.process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_game_window_service_process(proc):
            continue
        items.append(
            GameWindowServiceProcess(
                pid=int(proc.pid),
                parent_pid=_safe_ppid(proc),
                name=_safe_name(proc),
                cmdline=" ".join(_safe_cmdline(proc)),
                started_at=_safe_started_at(proc),
            )
        )
    items.sort(key=lambda item: (item.started_at or 0, item.pid))
    return [asdict(item) for item in items]


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _default_status(
    *,
    processes: list[dict[str, Any]] | None = None,
    state: str | None = None,
    last_error: str = "",
) -> dict[str, Any]:
    processes = processes if processes is not None else list_game_window_service_processes()
    process_running = bool(processes)
    resolved_state = state or ("starting" if process_running else "stopped")
    state_label = {
        "running": "运行中",
        "starting": "进程启动中",
        "stopped": "已停止",
        "unreachable": "不可达",
    }.get(resolved_state, resolved_state)
    return {
        "key": GAME_WINDOW_SERVICE_KEY,
        "title": GAME_WINDOW_SERVICE_TITLE,
        "running": False,
        "state": resolved_state,
        "state_label": state_label,
        "target_title": os.getenv("CODEYUN_GAME_WINDOW_TARGET_TITLE") or "云手机",
        "url": get_game_window_service_base_url(),
        "host": get_game_window_service_host(),
        "port": get_game_window_service_port(),
        "log_path": os.fspath(get_game_window_service_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "last_error": last_error,
        "external": True,
    }


def get_game_window_service_status() -> dict[str, Any]:
    processes = list_game_window_service_processes()
    try:
        response = requests.get(_endpoint("/api/services/game-window/status"), timeout=_request_timeout())
        response.raise_for_status()
        payload = response.json()
        service = payload.get("service") if isinstance(payload, dict) else None
        if not isinstance(service, dict):
            raise GameWindowServiceError("游戏画面流服务状态响应格式不支持")
    except (requests.RequestException, ValueError, GameWindowServiceError) as exc:
        if processes:
            return _default_status(processes=processes, state="unreachable", last_error=str(exc))
        return _default_status(processes=processes, state="stopped", last_error="")

    state = str(service.get("state") or "running")
    merged = {
        **_default_status(processes=processes, state=state),
        **service,
        "running": True,
        "url": get_game_window_service_base_url(),
        "host": get_game_window_service_host(),
        "port": get_game_window_service_port(),
        "log_path": os.fspath(get_game_window_service_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "external": True,
    }
    merged["state_label"] = merged.get("state_label") or _default_status(state=state)["state_label"]
    return merged


def start_game_window_service(
    *,
    replace_existing: bool = False,
    wait_seconds: float = 10.0,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    if replace_existing:
        stop_game_window_service()
    else:
        status = get_game_window_service_status()
        if status.get("running"):
            return {"status": "started", "service": status}

    host = get_game_window_service_host()
    port = get_game_window_service_port()
    if _is_tcp_port_open(host, port, timeout=0.5) and not list_game_window_service_processes():
        raise GameWindowServiceError(f"游戏画面流服务端口已被其他进程占用：{host}:{port}")

    log_path = get_game_window_service_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "CODEYUN_GAME_WINDOW_SERVICE_HOST": host,
            "CODEYUN_GAME_WINDOW_SERVICE_PORT": str(port),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    if env_overrides:
        env.update({key: str(value) for key, value in env_overrides.items() if value is not None})
    command_args = ["--host", host, "--port", str(port)]
    try:
        with log_path.open("ab") as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CodeYun start game window service\n".encode("utf-8")
            )
            proc = popen_python_module_service(
                GAME_WINDOW_SERVICE_MODULE,
                *command_args,
                preferred_root=ROOT_DIR,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise GameWindowServiceError(f"启动游戏画面流服务失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_game_window_service_status()
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    status = get_game_window_service_status()
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise GameWindowServiceError(f"已启动游戏画面流服务 PID {proc.pid}，但 {host}:{port} 未在 {int(wait_seconds)} 秒内就绪。")


def ensure_game_window_service_running() -> dict[str, Any]:
    status = get_game_window_service_status()
    if status.get("running"):
        return status
    return start_game_window_service(replace_existing=False)["service"]


def stop_game_window_service(timeout: float = 5.0) -> dict[str, Any]:
    processes = list_game_window_service_processes()
    for item in processes:
        pid = item.get("pid")
        if pid is None:
            continue
        process_runtime.terminate_process_tree(int(pid), timeout=timeout)
    time.sleep(0.2)
    return {
        "status": "stopped",
        "stopped_pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "service": get_game_window_service_status(),
    }


def open_game_window_service_stream(params: dict[str, Any]) -> requests.Response:
    ensure_game_window_service_running()
    try:
        response = requests.get(
            _endpoint("/api/services/game-window/stream"),
            params=params,
            timeout=(_request_timeout(default=5.0), 60.0),
            stream=True,
        )
    except requests.RequestException as exc:
        raise GameWindowServiceError(f"游戏画面流服务请求失败：{exc}") from exc
    return response


def _extract_service_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.text.strip() or f"游戏画面流服务返回 HTTP {response.status_code}"


def send_game_window_service_click(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_game_window_service_running()
    try:
        response = requests.post(
            _endpoint("/api/services/game-window/input/click"),
            json=payload,
            timeout=(_request_timeout(default=5.0), _request_timeout(default=8.0)),
        )
    except requests.RequestException as exc:
        raise GameWindowServiceError(f"游戏画面流服务点击请求失败：{exc}") from exc
    if response.status_code >= 400:
        if response.status_code == 404:
            raise GameWindowServiceError("游戏画面流服务缺少点击接口，请停止并重新启动“凡修游戏画面流”服务。")
        raise GameWindowServiceError(_extract_service_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise GameWindowServiceError("游戏画面流服务点击响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise GameWindowServiceError("游戏画面流服务点击响应格式不支持")
    return data


def send_game_window_service_activate(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_game_window_service_running()
    try:
        response = requests.post(
            _endpoint("/api/services/game-window/input/activate"),
            json=payload,
            timeout=(_request_timeout(default=5.0), _request_timeout(default=8.0)),
        )
    except requests.RequestException as exc:
        raise GameWindowServiceError(f"游戏画面流服务激活窗口请求失败：{exc}") from exc
    if response.status_code >= 400:
        if response.status_code == 404:
            raise GameWindowServiceError("游戏画面流服务缺少激活窗口接口，请停止并重新启动“凡修游戏画面流”服务。")
        raise GameWindowServiceError(_extract_service_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise GameWindowServiceError("游戏画面流服务激活窗口响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise GameWindowServiceError("游戏画面流服务激活窗口响应格式不支持")
    return data


def send_game_window_service_drag(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_game_window_service_running()
    try:
        response = requests.post(
            _endpoint("/api/services/game-window/input/drag"),
            json=payload,
            timeout=(_request_timeout(default=5.0), _request_timeout(default=8.0)),
        )
    except requests.RequestException as exc:
        raise GameWindowServiceError(f"游戏画面流服务拖拽请求失败：{exc}") from exc
    if response.status_code >= 400:
        if response.status_code == 404:
            raise GameWindowServiceError("游戏画面流服务缺少拖拽接口，请停止并重新启动“凡修游戏画面流”服务。")
        raise GameWindowServiceError(_extract_service_error(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise GameWindowServiceError("游戏画面流服务拖拽响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise GameWindowServiceError("游戏画面流服务拖拽响应格式不支持")
    return data
