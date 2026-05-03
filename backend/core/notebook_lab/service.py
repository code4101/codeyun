from __future__ import annotations

import copy
import hashlib
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nbformat
from jupyter_client import KernelManager
from sqlmodel import Session

from backend.core.settings import get_settings
from backend.models import AppSetting

from .schemas import (
    NotebookBinding,
    NotebookCell,
    NotebookKernelStatus,
    NotebookRunResponse,
    NotebookRunStatus,
    NotebookState,
)


AI_NOTEBOOK_SETTING_KEY = "ai_notebook_lab.v1"


class NotebookLabError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotebookHashConflictError(NotebookLabError):
    def __init__(self, message: str = "Notebook 已被外部修改，请刷新后重试") -> None:
        super().__init__(message, status_code=409)


@dataclass
class _DraftDocument:
    device_id: str
    path: Path
    base_hash: str
    notebook: Any
    dirty: bool
    updated_at: float


class NotebookKernelRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._kernel_manager: KernelManager | None = None
        self._client: Any = None
        self.status: NotebookKernelStatus = "stopped"
        self.last_error: str | None = None

    def execute(self, code: str, *, cwd: Path, timeout_seconds: float = 120.0) -> tuple[NotebookRunStatus, list[Any]]:
        with self._lock:
            try:
                client = self._ensure_started(cwd)
                self.status = "busy"
                msg_id = client.execute(code)
                outputs = self._collect_outputs(client, msg_id, timeout_seconds=timeout_seconds)
                status: NotebookRunStatus = (
                    "error"
                    if any(getattr(output, "output_type", None) == "error" for output in outputs)
                    else "success"
                )
                self.status = "idle"
                self.last_error = None if status == "success" else "代码执行失败"
                return status, outputs
            except KeyboardInterrupt:
                self.status = "idle"
                self.last_error = "代码执行已中断"
                return "interrupted", []
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)
                raise NotebookLabError(f"执行 Notebook 代码失败：{exc}", status_code=500) from exc

    def interrupt(self) -> None:
        with self._lock:
            if self._kernel_manager is not None:
                self._kernel_manager.interrupt_kernel()
            self.status = "idle"

    def shutdown(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            if client is not None:
                try:
                    client.stop_channels()
                except Exception:
                    pass
            if self._kernel_manager is not None:
                try:
                    self._kernel_manager.shutdown_kernel(now=True)
                except Exception:
                    pass
            self._kernel_manager = None
            self.status = "stopped"

    def _ensure_started(self, cwd: Path) -> Any:
        if self._kernel_manager is not None and self._kernel_manager.is_alive():
            return self._client

        self.status = "starting"
        manager = KernelManager(kernel_name="python3")
        manager.start_kernel(cwd=str(cwd))
        client = manager.client()
        client.start_channels()
        client.wait_for_ready(timeout=30)
        self._kernel_manager = manager
        self._client = client
        self.status = "idle"
        return client

    def _collect_outputs(self, client: Any, msg_id: str, *, timeout_seconds: float) -> list[Any]:
        outputs: list[Any] = []
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NotebookLabError("Notebook 执行超时", status_code=504)
            try:
                message = client.get_iopub_msg(timeout=max(0.1, remaining))
            except queue.Empty as exc:
                raise NotebookLabError("Notebook 执行超时", status_code=504) from exc

            parent_msg_id = message.get("parent_header", {}).get("msg_id")
            if parent_msg_id != msg_id:
                continue

            msg_type = message.get("header", {}).get("msg_type")
            content = message.get("content", {})

            if msg_type == "status" and content.get("execution_state") == "idle":
                break
            if msg_type == "stream":
                outputs.append(
                    nbformat.v4.new_output(
                        "stream",
                        name=content.get("name", "stdout"),
                        text=content.get("text", ""),
                    )
                )
            elif msg_type in {"execute_result", "display_data"}:
                output_kwargs = {
                    "data": content.get("data") or {},
                    "metadata": content.get("metadata") or {},
                }
                if msg_type == "execute_result":
                    output_kwargs["execution_count"] = content.get("execution_count")
                outputs.append(nbformat.v4.new_output(msg_type, **output_kwargs))
            elif msg_type == "error":
                outputs.append(
                    nbformat.v4.new_output(
                        "error",
                        ename=content.get("ename", ""),
                        evalue=content.get("evalue", ""),
                        traceback=content.get("traceback") or [],
                    )
                )

        return outputs


_LOCK = threading.RLock()
_DRAFTS: dict[str, _DraftDocument] = {}
_RUNTIMES: dict[str, NotebookKernelRuntime] = {}


def reset_notebook_lab_runtime() -> None:
    with _LOCK:
        _DRAFTS.clear()
        runtimes = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    for runtime in runtimes:
        runtime.shutdown()


def get_notebook_workdir() -> Path:
    workdir = get_settings().ai_notebook_workdir
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir.resolve()


def _empty_store() -> dict[str, Any]:
    return {
        "bindings": {},
        "stale": {},
        "last_runs": {},
        "last_error": {},
    }


def _load_store(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, AI_NOTEBOOK_SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return _empty_store()
    payload = copy.deepcopy(row.value)
    for key, default_value in _empty_store().items():
        if not isinstance(payload.get(key), type(default_value)):
            payload[key] = copy.deepcopy(default_value)
    return payload


def _save_store(session: Session, payload: dict[str, Any]) -> None:
    row = session.get(AppSetting, AI_NOTEBOOK_SETTING_KEY)
    now = time.time()
    if row is None:
        row = AppSetting(key=AI_NOTEBOOK_SETTING_KEY)
    row.value = copy.deepcopy(payload)
    row.updated_at = now
    session.add(row)
    session.commit()


def _sanitize_filename_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return stem or "device"


def _default_notebook_path(device_id: str) -> Path:
    return get_notebook_workdir() / f"{_sanitize_filename_stem(device_id)}.ipynb"


def resolve_notebook_path(notebook_path: str | None, *, device_id: str) -> Path:
    workdir = get_notebook_workdir()
    raw = (notebook_path or "").strip()
    candidate = _default_notebook_path(device_id) if not raw else Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workdir / candidate
    candidate = candidate.resolve(strict=False)

    if candidate.suffix.lower() != ".ipynb":
        raise NotebookLabError("Notebook 路径必须是 .ipynb 文件")

    try:
        candidate.relative_to(workdir)
    except ValueError as exc:
        raise NotebookLabError("Notebook 文件必须位于 AI Notebook 工作目录内", status_code=403) from exc

    return candidate


def _compute_notebook_hash(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_notebook() -> Any:
    return nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell("")],
        metadata={
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
        },
    )


def _write_notebook(path: Path, notebook: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        nbformat.write(notebook, handle)


def _read_notebook(path: Path) -> Any:
    if not path.exists():
        raise NotebookLabError("Notebook 文件不存在", status_code=404)
    with path.open("r", encoding="utf-8") as handle:
        notebook = nbformat.read(handle, as_version=4)
    if _ensure_cell_ids(notebook):
        _write_notebook(path, notebook)
    return notebook


def _ensure_notebook_file(path: Path) -> None:
    if path.exists():
        notebook = _read_notebook(path)
        if not notebook.cells:
            notebook.cells.append(nbformat.v4.new_code_cell(""))
            _ensure_cell_ids(notebook)
            _write_notebook(path, notebook)
        return

    notebook = _new_notebook()
    _ensure_cell_ids(notebook)
    _write_notebook(path, notebook)


def _ensure_cell_ids(notebook: Any) -> bool:
    changed = False
    for cell in notebook.cells:
        if not cell.get("id"):
            cell["id"] = uuid.uuid4().hex[:12]
            changed = True
    return changed


def _normalize_source(source: Any) -> str:
    if isinstance(source, list):
        return "".join(str(item) for item in source)
    return str(source or "")


def _truncate_summary(text: str, *, limit: int = 1600) -> str:
    text = text.replace("\r\n", "\n")
    return text if len(text) <= limit else f"{text[:limit]}\n..."


def _text_from_mime_payload(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def summarize_outputs(outputs: list[Any]) -> list[str]:
    summary: list[str] = []
    for output in outputs:
        output_type = output.get("output_type") if isinstance(output, dict) else getattr(output, "output_type", "")
        if output_type == "stream":
            text = output.get("text", "") if isinstance(output, dict) else getattr(output, "text", "")
            if text:
                summary.append(_truncate_summary(_text_from_mime_payload(text)))
        elif output_type in {"execute_result", "display_data"}:
            data = output.get("data", {}) if isinstance(output, dict) else getattr(output, "data", {})
            if isinstance(data, dict):
                text = data.get("text/plain") or data.get("text/markdown") or data.get("text/html")
                if text:
                    summary.append(_truncate_summary(_text_from_mime_payload(text)))
                elif data:
                    summary.append(_truncate_summary(str(data)))
        elif output_type == "error":
            if isinstance(output, dict):
                ename = output.get("ename", "")
                evalue = output.get("evalue", "")
            else:
                ename = getattr(output, "ename", "")
                evalue = getattr(output, "evalue", "")
            summary.append(_truncate_summary(f"{ename}: {evalue}".strip(": ")))
    return summary


def _get_stale_cell_ids(session: Session, device_id: str) -> list[str]:
    payload = _load_store(session)
    raw_items = payload.get("stale", {}).get(device_id, [])
    if not isinstance(raw_items, list):
        return []
    return [str(item) for item in raw_items if isinstance(item, str)]


def _save_stale_cell_ids(session: Session, device_id: str, stale_cell_ids: list[str]) -> None:
    payload = _load_store(session)
    payload.setdefault("stale", {})[device_id] = list(dict.fromkeys(stale_cell_ids))
    _save_store(session, payload)


def _save_last_error(session: Session, device_id: str, error: str | None) -> None:
    payload = _load_store(session)
    if error:
        payload.setdefault("last_error", {})[device_id] = error
    else:
        payload.setdefault("last_error", {}).pop(device_id, None)
    _save_store(session, payload)


def _get_last_error(session: Session, device_id: str) -> str | None:
    payload = _load_store(session)
    value = payload.get("last_error", {}).get(device_id)
    return value if isinstance(value, str) and value else None


def _save_last_run(
    session: Session,
    device_id: str,
    cell_id: str,
    *,
    status: NotebookRunStatus,
    last_run_at: float,
) -> None:
    payload = _load_store(session)
    runs = payload.setdefault("last_runs", {}).setdefault(device_id, {})
    runs[cell_id] = {
        "status": status,
        "last_run_at": last_run_at,
    }
    _save_store(session, payload)


def _get_last_runs(session: Session, device_id: str) -> dict[str, dict[str, Any]]:
    payload = _load_store(session)
    raw_runs = payload.get("last_runs", {}).get(device_id, {})
    return raw_runs if isinstance(raw_runs, dict) else {}


def _build_binding(session: Session, *, entry_id: str, device_id: str) -> NotebookBinding:
    payload = _load_store(session)
    bindings = payload.setdefault("bindings", {})
    raw_binding = bindings.get(device_id)
    if isinstance(raw_binding, dict):
        raw_path = raw_binding.get("notebook_path")
        updated_at = raw_binding.get("updated_at")
    else:
        raw_path = None
        updated_at = None

    path = resolve_notebook_path(raw_path if isinstance(raw_path, str) else None, device_id=device_id)
    if not isinstance(raw_binding, dict):
        bindings[device_id] = {
            "notebook_path": str(path),
            "updated_at": time.time(),
        }
        updated_at = bindings[device_id]["updated_at"]
        _save_store(session, payload)

    _ensure_notebook_file(path)
    return NotebookBinding(
        entry_id=entry_id,
        device_id=device_id,
        notebook_path=str(path),
        workdir=str(get_notebook_workdir()),
        exists=path.exists(),
        updated_at=float(updated_at) if isinstance(updated_at, (int, float)) else None,
    )


def update_notebook_binding(
    session: Session,
    *,
    entry_id: str,
    device_id: str,
    notebook_path: str | None,
) -> NotebookState:
    path = resolve_notebook_path(notebook_path, device_id=device_id)
    _ensure_notebook_file(path)
    payload = _load_store(session)
    old_path = payload.setdefault("bindings", {}).get(device_id, {}).get("notebook_path")
    payload["bindings"][device_id] = {
        "notebook_path": str(path),
        "updated_at": time.time(),
    }
    if old_path != str(path):
        payload.setdefault("stale", {}).pop(device_id, None)
        payload.setdefault("last_runs", {}).pop(device_id, None)
        payload.setdefault("last_error", {}).pop(device_id, None)
        with _LOCK:
            _DRAFTS.pop(device_id, None)
            runtime = _RUNTIMES.pop(device_id, None)
        if runtime is not None:
            runtime.shutdown()
    _save_store(session, payload)
    return get_notebook_state(session, entry_id=entry_id, device_id=device_id)


def _serialize_cells(
    notebook: Any,
    *,
    stale_cell_ids: set[str],
    last_runs: dict[str, dict[str, Any]],
) -> list[NotebookCell]:
    cells: list[NotebookCell] = []
    for index, cell in enumerate(notebook.cells):
        cell_id = str(cell.get("id") or "")
        run_info = last_runs.get(cell_id, {}) if isinstance(last_runs, dict) else {}
        cells.append(
            NotebookCell(
                cell_id=cell_id,
                index=index,
                cell_type=str(cell.get("cell_type") or "code"),
                source=_normalize_source(cell.get("source")),
                execution_count=cell.get("execution_count"),
                outputs_summary=summarize_outputs(list(cell.get("outputs") or [])),
                stale=cell_id in stale_cell_ids,
                last_run_status=run_info.get("status") if isinstance(run_info, dict) else None,
                last_run_at=(
                    float(run_info["last_run_at"])
                    if isinstance(run_info, dict) and isinstance(run_info.get("last_run_at"), (int, float))
                    else None
                ),
            )
        )
    return cells


def _get_runtime_status(device_id: str) -> NotebookKernelStatus:
    with _LOCK:
        runtime = _RUNTIMES.get(device_id)
    return runtime.status if runtime is not None else "stopped"


def get_notebook_state(session: Session, *, entry_id: str, device_id: str) -> NotebookState:
    binding = _build_binding(session, entry_id=entry_id, device_id=device_id)
    path = Path(binding.notebook_path)
    with _LOCK:
        draft = _DRAFTS.get(device_id)

    if draft and draft.path == path:
        notebook = draft.notebook
        notebook_hash = draft.base_hash
    else:
        notebook = _read_notebook(path)
        notebook_hash = _compute_notebook_hash(path)
    stale_ids = _get_stale_cell_ids(session, device_id)
    stale_set = set(stale_ids)
    cells = _serialize_cells(
        notebook,
        stale_cell_ids=stale_set,
        last_runs=_get_last_runs(session, device_id),
    )
    existing_cell_ids = {cell.cell_id for cell in cells}
    visible_stale_ids = [cell_id for cell_id in stale_ids if cell_id in existing_cell_ids]
    if visible_stale_ids != stale_ids:
        _save_stale_cell_ids(session, device_id, visible_stale_ids)

    return NotebookState(
        session_id=f"device:{device_id}",
        entry_id=entry_id,
        device_id=device_id,
        binding=binding,
        notebook_path=binding.notebook_path,
        notebook_hash=notebook_hash,
        kernel_status=_get_runtime_status(device_id),
        cells=cells,
        stale_cell_ids=visible_stale_ids,
        last_error=_get_last_error(session, device_id),
        dirty=bool(draft and draft.path == path and draft.dirty),
    )


def _assert_disk_hash(path: Path, expected_hash: str) -> None:
    current_hash = _compute_notebook_hash(path)
    if current_hash != (expected_hash or ""):
        raise NotebookHashConflictError()


def _load_editable_notebook(path: Path, *, device_id: str, notebook_hash: str) -> Any:
    with _LOCK:
        draft = _DRAFTS.get(device_id)
        if draft is not None:
            if draft.path != path or draft.base_hash != notebook_hash:
                raise NotebookHashConflictError()
            return draft.notebook

    _assert_disk_hash(path, notebook_hash)
    return _read_notebook(path)


def _store_draft(device_id: str, path: Path, notebook_hash: str, notebook: Any) -> None:
    with _LOCK:
        _DRAFTS[device_id] = _DraftDocument(
            device_id=device_id,
            path=path,
            base_hash=notebook_hash,
            notebook=notebook,
            dirty=True,
            updated_at=time.time(),
        )


def update_notebook_cell(
    session: Session,
    *,
    entry_id: str,
    device_id: str,
    cell_id: str,
    notebook_hash: str,
    source: str,
) -> NotebookState:
    binding = _build_binding(session, entry_id=entry_id, device_id=device_id)
    path = Path(binding.notebook_path)
    notebook = _load_editable_notebook(path, device_id=device_id, notebook_hash=notebook_hash)
    _ensure_cell_ids(notebook)

    target_index = None
    for index, cell in enumerate(notebook.cells):
        if cell.get("id") == cell_id:
            target_index = index
            cell["source"] = source
            break
    if target_index is None:
        raise NotebookLabError("Cell 不存在", status_code=404)

    all_ids = [str(cell.get("id")) for cell in notebook.cells]
    stale_ids = list(dict.fromkeys([*_get_stale_cell_ids(session, device_id), *all_ids[target_index:]]))
    _save_stale_cell_ids(session, device_id, stale_ids)
    _store_draft(device_id, path, notebook_hash, notebook)
    return get_notebook_state(session, entry_id=entry_id, device_id=device_id)


def save_notebook(
    session: Session,
    *,
    entry_id: str,
    device_id: str,
    notebook_hash: str,
) -> NotebookState:
    binding = _build_binding(session, entry_id=entry_id, device_id=device_id)
    path = Path(binding.notebook_path)
    with _LOCK:
        draft = _DRAFTS.get(device_id)

    if draft is None:
        _assert_disk_hash(path, notebook_hash)
        return get_notebook_state(session, entry_id=entry_id, device_id=device_id)
    if draft.path != path or draft.base_hash != notebook_hash:
        raise NotebookHashConflictError()

    _assert_disk_hash(path, notebook_hash)
    _write_notebook(path, draft.notebook)
    with _LOCK:
        _DRAFTS.pop(device_id, None)
    _save_last_error(session, device_id, None)
    return get_notebook_state(session, entry_id=entry_id, device_id=device_id)


def _get_clean_notebook_for_run(path: Path, *, device_id: str, notebook_hash: str) -> Any:
    with _LOCK:
        draft = _DRAFTS.get(device_id)
    if draft is not None and draft.path == path and draft.dirty:
        raise NotebookLabError("请先保存 Notebook 后再运行", status_code=409)
    _assert_disk_hash(path, notebook_hash)
    return _read_notebook(path)


def _get_runtime(device_id: str) -> NotebookKernelRuntime:
    with _LOCK:
        runtime = _RUNTIMES.get(device_id)
        if runtime is None:
            runtime = NotebookKernelRuntime()
            _RUNTIMES[device_id] = runtime
        return runtime


def run_notebook_cell(
    session: Session,
    *,
    entry_id: str,
    device_id: str,
    notebook_hash: str,
    cell_id: str,
) -> NotebookRunResponse:
    binding = _build_binding(session, entry_id=entry_id, device_id=device_id)
    path = Path(binding.notebook_path)
    notebook = _get_clean_notebook_for_run(path, device_id=device_id, notebook_hash=notebook_hash)
    _ensure_cell_ids(notebook)

    target_cell = None
    for cell in notebook.cells:
        if cell.get("id") == cell_id:
            target_cell = cell
            break
    if target_cell is None:
        raise NotebookLabError("Cell 不存在", status_code=404)

    if target_cell.get("cell_type") != "code":
        status: NotebookRunStatus = "success"
        outputs: list[Any] = []
    else:
        runtime = _get_runtime(device_id)
        status, outputs = runtime.execute(
            _normalize_source(target_cell.get("source")),
            cwd=path.parent,
        )
        target_cell["outputs"] = outputs
        execution_counts = [
            output.get("execution_count")
            for output in outputs
            if isinstance(output, dict) and output.get("execution_count") is not None
        ]
        if execution_counts:
            target_cell["execution_count"] = execution_counts[-1]

    _write_notebook(path, notebook)
    run_at = time.time()
    _save_last_run(session, device_id, cell_id, status=status, last_run_at=run_at)
    if status == "success":
        stale_ids = [item for item in _get_stale_cell_ids(session, device_id) if item != cell_id]
        _save_stale_cell_ids(session, device_id, stale_ids)
        _save_last_error(session, device_id, None)
    else:
        _save_last_error(session, device_id, "Cell 执行失败")

    return NotebookRunResponse(
        status=status,
        outputs_summary=summarize_outputs(outputs),
        state=get_notebook_state(session, entry_id=entry_id, device_id=device_id),
    )


def run_temporary_code(
    session: Session,
    *,
    entry_id: str,
    device_id: str,
    code: str,
) -> NotebookRunResponse:
    binding = _build_binding(session, entry_id=entry_id, device_id=device_id)
    path = Path(binding.notebook_path)
    with _LOCK:
        draft = _DRAFTS.get(device_id)
    if draft is not None and draft.path == path and draft.dirty:
        raise NotebookLabError("请先保存 Notebook 后再运行临时代码", status_code=409)

    runtime = _get_runtime(device_id)
    status, outputs = runtime.execute(code, cwd=path.parent)
    if status == "success":
        _save_last_error(session, device_id, None)
    else:
        _save_last_error(session, device_id, "临时代码执行失败")
    return NotebookRunResponse(
        status=status,
        outputs_summary=summarize_outputs(outputs),
        state=get_notebook_state(session, entry_id=entry_id, device_id=device_id),
    )


def interrupt_notebook_kernel(session: Session, *, entry_id: str, device_id: str) -> NotebookState:
    runtime = _get_runtime(device_id)
    runtime.interrupt()
    return get_notebook_state(session, entry_id=entry_id, device_id=device_id)
