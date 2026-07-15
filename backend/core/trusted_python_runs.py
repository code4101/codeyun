from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.core.services.launcher import popen_service, run_quiet
from backend.core.settings import get_settings


TRUSTED_PYTHON_RUN_LOG_TAIL_CHARS = 24_000
TRUSTED_PYTHON_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _trusted_python_runs_dir() -> Path:
    runs_dir = get_settings().data_dir / "trusted-python-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _run_dir(run_id: str) -> Path:
    return _trusted_python_runs_dir() / run_id


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return os.fspath(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _read_tail(path: Path, max_chars: int = TRUSTED_PYTHON_RUN_LOG_TAIL_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _build_runner_source(payload: dict[str, Any]) -> str:
    payload_source = repr(json.dumps(payload, ensure_ascii=False))
    return f"""from __future__ import annotations

import importlib
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path


_PAYLOAD = json.loads({payload_source})


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return str(value)


def _resolve_target(module_name, callable_name):
    module = importlib.import_module(module_name)
    target = module
    for part in callable_name.split("."):
        if not part:
            continue
        target = getattr(target, part)
    return target


def _run():
    mode = _PAYLOAD.get("mode")
    if mode == "script":
        namespace = {{
            "__name__": "__trusted_python_run__",
            "__file__": _PAYLOAD.get("virtual_file") or "<trusted-python-run>",
        }}
        source = _PAYLOAD.get("script") or ""
        exec(compile(source, "<trusted-python-run>", "exec"), namespace, namespace)
        return namespace.get("result")

    if mode == "module_call":
        target = _resolve_target(_PAYLOAD.get("module") or "", _PAYLOAD.get("callable") or "")
        return target(*(_PAYLOAD.get("args") or []), **(_PAYLOAD.get("kwargs") or {{}}))

    raise ValueError(f"Unsupported trusted python run mode: {{mode}}")


def _write_result(payload):
    result_path = Path(_PAYLOAD["result_path"])
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


try:
    result = _run()
except BaseException as exc:
    _write_result({{
        "ok": False,
        "error": {{
            "type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }},
    }})
    sys.exit(1)

_write_result({{"ok": True, "result": result}})
"""


def _build_response(run_dir: Path) -> dict[str, Any]:
    status_payload = _read_json(run_dir / "status.json")
    result_payload = _read_json(run_dir / "result.json")
    response = {
        **status_payload,
        "stdout": _read_tail(run_dir / "stdout.txt"),
        "stderr": _read_tail(run_dir / "stderr.txt"),
    }
    if result_payload:
        response["ok"] = bool(result_payload.get("ok"))
        if result_payload.get("ok"):
            response["result"] = result_payload.get("result")
        else:
            response["error"] = result_payload.get("error") or {
                "type": "PythonRunError",
                "message": "Trusted python run failed without a structured error.",
            }
    return response


def _update_finished_status(
    run_dir: Path,
    *,
    returncode: int | None,
    started_at: float,
    timeout: bool = False,
) -> None:
    current = _read_json(run_dir / "status.json")
    result_payload = _read_json(run_dir / "result.json")
    finished_at = time.time()
    if timeout:
        status = "timeout"
    elif returncode == 0 and result_payload.get("ok") is True:
        status = "completed"
    else:
        status = "failed"

    error = result_payload.get("error")
    if status == "failed" and not error:
        stderr = _read_tail(run_dir / "stderr.txt", max_chars=4000)
        error = {
            "type": "ProcessError",
            "message": stderr.strip() or f"Process exited with return code {returncode}",
        }

    _write_json(
        run_dir / "status.json",
        {
            **current,
            "status": status,
            "returncode": returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(finished_at - started_at, 3),
            "error": error,
        },
    )


def start_trusted_python_run(
    *,
    mode: str,
    script: str = "",
    module: str = "",
    callable_name: str = "",
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    async_run: bool = False,
    timeout: int = 3600,
) -> dict[str, Any]:
    if mode not in {"script", "module_call"}:
        raise ValueError("mode 必须是 script 或 module_call")
    if mode == "script" and not script.strip():
        raise ValueError("script 模式需要提供 script")
    if mode == "module_call" and (not module.strip() or not callable_name.strip()):
        raise ValueError("module_call 模式需要提供 module 和 callable")

    run_id = uuid.uuid4().hex
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=False)

    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    if not cwd_path.exists():
        raise ValueError(f"cwd 不存在: {cwd_path}")

    result_path = run_dir / "result.json"
    runner_path = run_dir / "runner.py"
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"
    sanitized_env = {str(key): str(value) for key, value in (env or {}).items()}
    payload = {
        "mode": mode,
        "script": script,
        "module": module,
        "callable": callable_name,
        "args": args or [],
        "kwargs": kwargs or {},
        "result_path": os.fspath(result_path),
        "virtual_file": os.fspath(runner_path),
    }

    runner_path.write_text(_build_runner_source(payload), encoding="utf-8")
    _write_json(
        run_dir / "request.json",
        {
            "run_id": run_id,
            "mode": mode,
            "module": module,
            "callable": callable_name,
            "cwd": os.fspath(cwd_path),
            "async": async_run,
            "timeout": timeout,
            "env_keys": sorted(sanitized_env),
        },
    )
    started_at = time.time()
    _write_json(
        run_dir / "status.json",
        {
            "run_id": run_id,
            "status": "running",
            "pid": None,
            "returncode": None,
            "cwd": os.fspath(cwd_path),
            "mode": mode,
            "started_at": started_at,
            "finished_at": None,
        },
    )

    run_env = os.environ.copy()
    run_env["PYTHONIOENCODING"] = "utf-8"
    run_env.update(sanitized_env)
    cmd = [sys.executable, os.fspath(runner_path)]

    if not async_run:
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                proc = run_quiet(
                    cmd,
                    cwd=os.fspath(cwd_path),
                    env=run_env,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                )
            _update_finished_status(run_dir, returncode=proc.returncode, started_at=started_at)
        except subprocess.TimeoutExpired:
            _update_finished_status(run_dir, returncode=None, started_at=started_at, timeout=True)
        return _build_response(run_dir)

    stdout_file = stdout_path.open("wb")
    stderr_file = stderr_path.open("wb")
    try:
        proc = popen_service(
            cmd,
            cwd=os.fspath(cwd_path),
            env=run_env,
            stdout=stdout_file,
            stderr=stderr_file,
        )
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise

    current = _read_json(run_dir / "status.json")
    _write_json(run_dir / "status.json", {**current, "pid": proc.pid})

    def _waiter() -> None:
        try:
            try:
                returncode = proc.wait(timeout=timeout)
                _update_finished_status(run_dir, returncode=returncode, started_at=started_at)
            except subprocess.TimeoutExpired:
                proc.kill()
                _update_finished_status(run_dir, returncode=None, started_at=started_at, timeout=True)
        except Exception as exc:  # pragma: no cover - defensive background guard
            current_status = _read_json(run_dir / "status.json")
            _write_json(
                run_dir / "status.json",
                {
                    **current_status,
                    "status": "failed",
                    "finished_at": time.time(),
                    "error": {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                },
            )
        finally:
            stdout_file.close()
            stderr_file.close()

    threading.Thread(target=_waiter, name=f"trusted-python-run-{run_id}", daemon=True).start()
    return _build_response(run_dir)


def get_trusted_python_run(run_id: str) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    if not TRUSTED_PYTHON_RUN_ID_RE.fullmatch(normalized_run_id):
        raise FileNotFoundError("trusted python run not found")
    run_dir = _run_dir(normalized_run_id)
    if not run_dir.exists():
        raise FileNotFoundError("trusted python run not found")
    return _build_response(run_dir)
