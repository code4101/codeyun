from __future__ import annotations

import base64
import inspect
import os
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psutil
import requests

from pyxllib.prog import process_runtime

from backend.core.ocr.preview import OcrPreviewError, OcrShapeType
from backend.core.services.launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings


OCR_SERVICE_KEY = "ocr"
OCR_SERVICE_TITLE = "OCR"
DEFAULT_OCR_SERVICE_HOST = "127.0.0.1"
DEFAULT_OCR_SERVICE_PORT = 8765
OCR_SERVICE_MODULE = "backend.services.ocr_daemon"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


@dataclass(frozen=True)
class OcrServiceProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def get_ocr_service_host() -> str:
    return (os.getenv("CODEYUN_OCR_SERVICE_HOST") or DEFAULT_OCR_SERVICE_HOST).strip() or DEFAULT_OCR_SERVICE_HOST


def get_ocr_service_port() -> int:
    try:
        return int(os.getenv("CODEYUN_OCR_SERVICE_PORT") or DEFAULT_OCR_SERVICE_PORT)
    except ValueError:
        return DEFAULT_OCR_SERVICE_PORT


def get_ocr_service_base_url() -> str:
    configured = (os.getenv("CODEYUN_OCR_SERVICE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://{get_ocr_service_host()}:{get_ocr_service_port()}"


def get_ocr_service_log_path() -> Path:
    configured = (os.getenv("CODEYUN_OCR_SERVICE_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "logs" / "ocr-service.log").resolve(strict=False)


def _endpoint(path: str) -> str:
    return f"{get_ocr_service_base_url()}{path}"


def _request_timeout(default: float = 3.0) -> float:
    try:
        return float(os.getenv("CODEYUN_OCR_SERVICE_TIMEOUT") or default)
    except ValueError:
        return default


def _predict_timeout(default: float = 180.0) -> float:
    try:
        return float(os.getenv("CODEYUN_OCR_PREDICT_TIMEOUT") or default)
    except ValueError:
        return default


def _safe_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _safe_name(proc: psutil.Process) -> str:
    try:
        return str(proc.name() or "")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return int(proc.ppid())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _safe_started_at(proc: psutil.Process) -> float | None:
    try:
        return float(proc.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _matches_ocr_service_process(proc: psutil.Process) -> bool:
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    for index, part in enumerate(cmdline[:-1]):
        if part == "-m" and cmdline[index + 1] == OCR_SERVICE_MODULE:
            return True
    return OCR_SERVICE_MODULE in " ".join(cmdline)


def list_ocr_service_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[OcrServiceProcess] = []
    for proc in process_runtime.process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_ocr_service_process(proc):
            continue
        items.append(
            OcrServiceProcess(
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
    settings = get_settings()
    processes = processes if processes is not None else list_ocr_service_processes()
    process_running = bool(processes)
    resolved_state = state or ("starting" if process_running else "stopped")
    state_label = {
        "running": "运行中",
        "idle": "已加载",
        "cold": "待加载",
        "starting": "进程启动中",
        "stopped": "已停止",
        "unreachable": "不可达",
    }.get(resolved_state, resolved_state)
    return {
        "key": OCR_SERVICE_KEY,
        "title": OCR_SERVICE_TITLE,
        "engine": "paddleocr",
        "device": settings.ocr_device,
        "lang": settings.ocr_lang,
        "running": False,
        "loaded": False,
        "state": resolved_state,
        "state_label": state_label,
        "instance_count": 0,
        "idle_instance_count": 0,
        "active_instance_count": 0,
        "idle_timeout_seconds": settings.ocr_idle_timeout_seconds,
        "idle_remaining_seconds": None,
        "acquire_timeout_seconds": settings.ocr_acquire_timeout_seconds,
        "call_count": 0,
        "error_count": 0,
        "last_loaded_at": None,
        "last_used_at": None,
        "last_error": last_error,
        "url": get_ocr_service_base_url(),
        "host": get_ocr_service_host(),
        "port": get_ocr_service_port(),
        "log_path": os.fspath(get_ocr_service_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "external": True,
    }


def get_ocr_service_status() -> dict[str, Any]:
    processes = list_ocr_service_processes()
    host = get_ocr_service_host()
    port = get_ocr_service_port()
    if not processes and not _is_tcp_port_open(host, port, timeout=0.02):
        return _default_status(processes=processes, state="stopped", last_error="")
    try:
        response = requests.get(_endpoint("/api/services/ocr/status"), timeout=_request_timeout())
        response.raise_for_status()
        payload = response.json()
        service = payload.get("service") if isinstance(payload, dict) else None
        if not isinstance(service, dict):
            raise OcrPreviewError("OCR 服务状态响应格式不支持")
    except (requests.RequestException, ValueError, OcrPreviewError) as exc:
        if processes:
            return _default_status(processes=processes, state="unreachable", last_error=str(exc))
        return _default_status(processes=processes, state="stopped", last_error="")

    state = str(service.get("state") or "cold")
    service = {
        **_default_status(processes=processes, state=state),
        **service,
        "running": True,
        "url": get_ocr_service_base_url(),
        "host": host,
        "port": port,
        "log_path": os.fspath(get_ocr_service_log_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "external": True,
    }
    service["state_label"] = service.get("state_label") or _default_status(state=state)["state_label"]
    return service


def start_ocr_service(
    *,
    replace_existing: bool = False,
    wait_seconds: float = 20.0,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    if replace_existing:
        stop_ocr_service()
    else:
        status = get_ocr_service_status()
        if status.get("running"):
            return {"status": "started", "service": status}

    host = get_ocr_service_host()
    port = get_ocr_service_port()
    if _is_tcp_port_open(host, port, timeout=0.5) and not list_ocr_service_processes():
        raise OcrPreviewError(f"OCR 服务端口已被其他进程占用：{host}:{port}")

    log_path = get_ocr_service_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "CODEYUN_OCR_INLINE": "1",
            "CODEYUN_OCR_SERVICE_HOST": host,
            "CODEYUN_OCR_SERVICE_PORT": str(port),
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
            log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] CodeYun start OCR service\n".encode("utf-8"))
            proc = popen_python_module_service(
                OCR_SERVICE_MODULE,
                *command_args,
                preferred_root=ROOT_DIR,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise OcrPreviewError(f"启动 OCR 服务失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_ocr_service_status()
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    status = get_ocr_service_status()
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise OcrPreviewError(f"已启动 OCR 服务进程 PID {proc.pid}，但 {host}:{port} 未在 {int(wait_seconds)} 秒内就绪。")


def ensure_ocr_service_running() -> dict[str, Any]:
    status = get_ocr_service_status()
    if status.get("running"):
        return status
    return start_ocr_service(replace_existing=False)["service"]


def stop_ocr_service(timeout: float = 5.0) -> dict[str, Any]:
    processes = list_ocr_service_processes()
    for item in processes:
        pid = item.get("pid")
        if pid is None:
            continue
        process_runtime.terminate_process_tree(int(pid), timeout=timeout)
    time.sleep(0.2)
    return {
        "status": "stopped",
        "stopped_pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "service": get_ocr_service_status(),
    }


def reset_ocr_service() -> dict[str, Any]:
    ensure_ocr_service_running()
    try:
        response = requests.post(_endpoint("/api/services/ocr/reset"), timeout=_request_timeout(default=10.0))
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OcrPreviewError(f"重置 OCR 服务失败：{exc}") from exc
    service = payload.get("service") if isinstance(payload, dict) else None
    return service if isinstance(service, dict) else get_ocr_service_status()


def _infer_ocr_request_caller() -> str:
    root = ROOT_DIR.resolve(strict=False)
    ignored_suffixes = {
        os.fspath((root / "backend" / "core" / "runtime" / "ocr_service.py").resolve(strict=False)).lower(),
        os.fspath((root / "backend" / "core" / "ocr" / "preview.py").resolve(strict=False)).lower(),
        os.fspath((root / "backend" / "core" / "fanxiu" / "game" / "macro_annotation.py").resolve(strict=False)).lower(),
        os.fspath((root / "backend" / "core" / "fanxiu" / "data_annotation" / "runtime_runner.py").resolve(strict=False)).lower(),
    }
    for frame in inspect.stack(context=0)[1:]:
        filename = os.fspath(Path(frame.filename).resolve(strict=False))
        filename_key = filename.lower()
        if filename_key in ignored_suffixes:
            continue
        try:
            relative = os.fspath(Path(filename).relative_to(root))
        except ValueError:
            relative = filename
        return f"{relative}:{frame.function}:{frame.lineno}"[:500]
    return "unknown"


def _ocr_request_caller_header() -> str:
    return quote(_infer_ocr_request_caller(), safe="/:._-")[:500]


def predict_via_ocr_service(
    image_path: Path,
    *,
    shape_type: OcrShapeType = "polygon",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_ocr_service_running()
    try:
        image_bytes = Path(image_path).read_bytes()
    except FileNotFoundError as exc:
        raise OcrPreviewError("图片文件不存在") from exc
    except OSError as exc:
        raise OcrPreviewError(f"读取图片失败：{exc}") from exc

    payload = {
        "image": base64.b64encode(image_bytes).decode("ascii"),
        "shape_type": shape_type,
        "options": options or {},
    }

    def post_predict() -> requests.Response:
        return requests.post(
            _endpoint("/api/services/ocr/predict"),
            json=payload,
            headers={"X-CodeYun-OCR-Caller": _ocr_request_caller_header()},
            timeout=_predict_timeout(),
        )

    try:
        response = post_predict()
    except requests.RequestException as exc:
        status = get_ocr_service_status()
        if get_settings().ocr_device == "gpu" and not status.get("running"):
            try:
                start_ocr_service(
                    replace_existing=True,
                    env_overrides={"CODEYUN_OCR_DEVICE": "cpu"},
                )
                response = post_predict()
            except (requests.RequestException, OcrPreviewError) as retry_exc:
                raise OcrPreviewError(f"OCR 服务 GPU 失败且 CPU 降级失败：{retry_exc}") from retry_exc
        else:
            raise OcrPreviewError(f"OCR 服务请求失败：{exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise OcrPreviewError(str(detail or f"OCR 服务返回 {response.status_code}"))
    try:
        data = response.json()
    except ValueError as exc:
        raise OcrPreviewError("OCR 服务返回内容不是 JSON") from exc
    if not isinstance(data, dict) or "document" not in data:
        raise OcrPreviewError("OCR 服务预测响应格式不支持")
    return {
        "engine": data.get("engine") or "paddleocr",
        "shape_type": data.get("shape_type") or shape_type,
        "shape_count": data.get("shape_count") or 0,
        "document": data["document"],
    }


def should_use_inline_ocr() -> bool:
    if _env_flag("CODEYUN_OCR_INLINE", default=False):
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return bool(get_settings().is_test)
