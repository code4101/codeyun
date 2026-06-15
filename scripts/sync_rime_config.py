from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path, PureWindowsPath
from typing import Any

import requests
from sqlmodel import Session, select


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai.rime_context_prediction import (  # noqa: E402
    RimeContextPredictionError,
    import_rime_context_prediction_article,
    refresh_rime_context_prediction_tree,
)
from backend.db import engine  # noqa: E402
from backend.models import UserDevice  # noqa: E402


DEFAULT_TARGET_NAME = "codepc_mi15"
DEFAULT_TARGET_RIME_DIR_BY_NAME = {
    "codepc_mi15": r"C:\Users\chen\AppData\Roaming\Rime",
}

SYNC_FILES = [
    "rime.lua",
    "codeyun_symbols.yaml",
    "default.custom.yaml",
    "luna_pinyin_simp.custom.yaml",
    "weasel.custom.yaml",
    "custom_phrase.txt",
    "radical_pinyin.dict.yaml",
    "radical_pinyin.schema.yaml",
    "codeyun_english.dict.yaml",
    "codeyun_english_base.dict.yaml",
    "codeyun_english_learned.dict.yaml",
    "codeyun_english.schema.yaml",
    "context_prediction.tsv",
    "context_prediction_hot.tsv",
    "context_prediction_runtime.tsv",
    "context_prediction_snapshot.tsv",
    "context_prediction_model_counts.tsv",
    "context_prediction_deleted_candidates.tsv",
    "打开预测索引.cmd",
    "scripts/build_context_prediction.py",
    "scripts/render_context_prediction_tree.py",
    "docs/context-prediction-model.md",
    "docs/context_prediction_tree.html",
]

DEPLOY_TRIGGER_FILES = {
    "rime.lua",
    "codeyun_symbols.yaml",
    "default.custom.yaml",
    "luna_pinyin_simp.custom.yaml",
    "weasel.custom.yaml",
    "custom_phrase.txt",
    "radical_pinyin.dict.yaml",
    "radical_pinyin.schema.yaml",
    "codeyun_english.dict.yaml",
    "codeyun_english_base.dict.yaml",
    "codeyun_english_learned.dict.yaml",
    "codeyun_english.schema.yaml",
    "context_prediction.tsv",
    "context_prediction_hot.tsv",
    "context_prediction_runtime.tsv",
    "context_prediction_snapshot.tsv",
    "context_prediction_deleted_candidates.tsv",
}


class SyncError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_http_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def api_url(entry: UserDevice, path: str) -> str:
    base = (entry.server_url or "").rstrip("/")
    if not base:
        raise SyncError(f"目标设备 {entry.name or entry.entry_id} 没有 server_url。")
    return f"{base}/api{path}"


def auth_headers(entry: UserDevice) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": entry.token,
    }


def request_json(
    session: requests.Session,
    entry: UserDevice,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: int = 30,
    allow_statuses: set[int] | None = None,
) -> tuple[Any, int]:
    try:
        response = session.request(
            method,
            api_url(entry, path),
            headers=auth_headers(entry),
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SyncError(f"请求目标设备失败：{exc}") from exc

    if allow_statuses and response.status_code in allow_statuses:
        return None, response.status_code

    if response.status_code >= 400:
        detail = response.text.strip()
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload.get("message") or detail)
        except ValueError:
            pass
        raise SyncError(f"目标设备返回 HTTP {response.status_code}: {detail}")

    if not response.content:
        return {}, response.status_code
    try:
        return response.json(), response.status_code
    except ValueError as exc:
        raise SyncError(f"目标设备返回了非 JSON 数据：{response.text[:200]}") from exc


def resolve_source_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    configured = os.environ.get("CODEYUN_RIME_USER_DIR")
    if configured:
        return Path(configured).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Rime"
    return Path.home() / "AppData" / "Roaming" / "Rime"


def find_target_entry(name: str | None, entry_id: str | None) -> UserDevice:
    with Session(engine) as db:
        if entry_id:
            entry = db.get(UserDevice, entry_id)
            if entry:
                return entry
            raise SyncError(f"没有找到目标设备 entry_id={entry_id}。")

        target_name = name or DEFAULT_TARGET_NAME
        entry = db.exec(
            select(UserDevice).where(
                UserDevice.name == target_name,
                UserDevice.is_active == True,  # noqa: E712
            )
        ).first()
        if entry:
            return entry
        raise SyncError(f"没有找到目标设备 name={target_name}。")


def resolve_target_rime_dir(
    session: requests.Session,
    entry: UserDevice,
    explicit_dir: str | None,
) -> str:
    if explicit_dir:
        return explicit_dir

    payload, status = request_json(
        session,
        entry,
        "GET",
        "/rime/context-prediction/tree",
        params={"limit": 1},
        timeout=20,
        allow_statuses={404},
    )
    if status != 404 and isinstance(payload, dict):
        rime_dir = str(payload.get("rime_dir") or "").strip()
        unavailable_status = str(payload.get("status") or "").strip()
        if rime_dir and unavailable_status not in {"rime_missing", "unsupported_platform"}:
            return rime_dir

    by_name = DEFAULT_TARGET_RIME_DIR_BY_NAME.get(entry.name or "")
    if by_name:
        return by_name

    raise SyncError(
        "无法自动判断目标 Rime 目录。请传入 --target-rime-dir，例如 "
        r'C:\Users\chen\AppData\Roaming\Rime。'
    )


def sync_rime_config_to_target(
    *,
    target_name: str | None = DEFAULT_TARGET_NAME,
    target_entry_id: str | None = None,
    target_rime_dir: str | None = None,
    source_dir: str | Path | None = None,
    dry_run: bool = False,
    no_deploy: bool = False,
    refresh_local: bool = True,
    pull_history: bool = True,
    history_limit: int = 200000,
    skip_unavailable: bool = False,
) -> dict[str, Any]:
    resolved_source_dir = resolve_source_dir(os.fspath(source_dir) if source_dir else None)
    if not resolved_source_dir.exists():
        raise SyncError(f"主设备 Rime 目录不存在：{resolved_source_dir}")

    try:
        entry = find_target_entry(target_name, target_entry_id)
    except SyncError as exc:
        if skip_unavailable:
            return {"ok": True, "skipped": True, "message": str(exc), "target": target_name or target_entry_id}
        raise

    if entry.mode != "remote":
        raise SyncError(f"目标设备必须是 remote，当前 {entry.name} mode={entry.mode}。")

    session = make_http_session()
    try:
        resolved_target_rime_dir = resolve_target_rime_dir(session, entry, target_rime_dir)
    except SyncError as exc:
        if skip_unavailable:
            return {"ok": True, "skipped": True, "message": str(exc), "target": entry.name}
        raise

    history_result = None
    if pull_history:
        history_result = pull_target_history_as_article(
            session,
            entry,
            limit=max(1, history_limit),
            enabled=True,
        )

    refresh_result = refresh_local_prediction(enabled=refresh_local)
    sync_result = sync_files(
        session,
        entry,
        source_dir=resolved_source_dir,
        target_rime_dir=resolved_target_rime_dir,
        dry_run=dry_run,
    )

    deploy_result = "skipped"
    if sync_result["deploy_required"] and not no_deploy:
        deploy_remote_weasel(session, entry, dry_run=dry_run)
        deploy_result = "deployed" if not dry_run else "dry_run"

    return {
        "ok": True,
        "skipped": False,
        "source_dir": str(resolved_source_dir),
        "target": entry.name,
        "target_rime_dir": resolved_target_rime_dir,
        "history": history_result,
        "refresh": {
            key: refresh_result.get(key)
            for key in ["available", "status", "message"]
            if isinstance(refresh_result, dict) and key in refresh_result
        } if refresh_result else None,
        "sync": sync_result,
        "deploy": deploy_result,
    }


def read_local_file(source_dir: Path, relative_path: str) -> tuple[str, str] | None:
    path = source_dir / Path(relative_path)
    if not path.exists():
        return None
    if not path.is_file():
        raise SyncError(f"本地路径不是文件：{path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SyncError(f"本地文件不是 UTF-8 文本：{path}") from exc
    return text, sha256_text(text)


def remote_path(target_rime_dir: str, relative_path: str) -> str:
    return str(PureWindowsPath(target_rime_dir) / PureWindowsPath(relative_path.replace("/", "\\")))


def remote_read_hash(
    session: requests.Session,
    entry: UserDevice,
    absolute_path: str,
) -> str | None:
    payload, status = request_json(
        session,
        entry,
        "GET",
        "/fs/text",
        params={"absolute_path": absolute_path, "encoding": "utf-8"},
        timeout=20,
        allow_statuses={404},
    )
    if status == 404:
        return None
    if not isinstance(payload, dict):
        raise SyncError(f"读取远程文件返回异常：{absolute_path}")
    text = str(payload.get("text") or "")
    return sha256_text(text)


def remote_write_text(
    session: requests.Session,
    entry: UserDevice,
    absolute_path: str,
    text: str,
) -> None:
    request_json(
        session,
        entry,
        "POST",
        "/fs/text",
        json_body={"absolute_path": absolute_path, "text": text, "encoding": "utf-8"},
        timeout=30,
    )


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def create_remote_task(
    session: requests.Session,
    entry: UserDevice,
    *,
    name: str,
    command: str,
    cwd: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    payload, _ = request_json(
        session,
        entry,
        "POST",
        "/task/create",
        json_body={
            "name": name,
            "command": command,
            "cwd": cwd,
            "description": "小狼毫配置同步临时任务",
            "timeout": timeout,
        },
        timeout=20,
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise SyncError(f"创建远程任务失败：{payload}")
    return payload


def start_remote_task(session: requests.Session, entry: UserDevice, task_id: str) -> None:
    request_json(session, entry, "POST", f"/task/{task_id}/start", timeout=20)


def get_remote_task(session: requests.Session, entry: UserDevice, task_id: str) -> dict[str, Any]:
    payload, _ = request_json(session, entry, "GET", f"/task/{task_id}", timeout=20)
    if not isinstance(payload, dict):
        raise SyncError(f"读取远程任务状态失败：{payload}")
    return payload


def get_remote_task_logs(session: requests.Session, entry: UserDevice, task_id: str) -> list[str]:
    payload, _ = request_json(session, entry, "GET", f"/task/{task_id}/logs", params={"n": 120}, timeout=20)
    if isinstance(payload, dict) and isinstance(payload.get("logs"), list):
        return [str(item).rstrip("\n") for item in payload["logs"]]
    if isinstance(payload, list):
        return [str(item).rstrip("\n") for item in payload]
    return []


def delete_remote_task(session: requests.Session, entry: UserDevice, task_id: str) -> None:
    try:
        request_json(session, entry, "DELETE", f"/task/{task_id}", timeout=20, allow_statuses={404})
    except SyncError:
        pass


def run_remote_task(
    session: requests.Session,
    entry: UserDevice,
    *,
    name: str,
    command: str,
    timeout: int = 120,
) -> list[str]:
    task = create_remote_task(session, entry, name=name, command=command, timeout=timeout)
    task_id = str(task["id"])
    try:
        start_remote_task(session, entry, task_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = get_remote_task(session, entry, task_id).get("status") or {}
            if not bool(status.get("running")):
                return get_remote_task_logs(session, entry, task_id)
            time.sleep(1)
        raise SyncError(f"远程任务超时：{name}")
    finally:
        delete_remote_task(session, entry, task_id)


def ensure_remote_directories(
    session: requests.Session,
    entry: UserDevice,
    target_rime_dir: str,
    relative_paths: list[str],
    *,
    dry_run: bool,
) -> None:
    dirs = sorted(
        {
            str(PureWindowsPath(remote_path(target_rime_dir, item)).parent)
            for item in relative_paths
            if "\\" in item.replace("/", "\\")
        }
    )
    if not dirs or dry_run:
        return
    quoted_dirs = ", ".join(json.dumps(item, ensure_ascii=False) for item in dirs)
    ps_script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$paths = @({quoted_dirs})\n"
        "foreach ($path in $paths) {\n"
        "  New-Item -ItemType Directory -Force -LiteralPath $path | Out-Null\n"
        "}\n"
    )
    command = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encode_powershell(ps_script)}"
    run_remote_task(session, entry, name="rime_sync_prepare_dirs", command=command, timeout=120)


def deploy_remote_weasel(
    session: requests.Session,
    entry: UserDevice,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$roots = @()
if ($env:ProgramFiles) { $roots += (Join-Path $env:ProgramFiles 'Rime') }
$programFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
if ($programFilesX86) { $roots += (Join-Path $programFilesX86 'Rime') }
$deployers = @()
foreach ($root in $roots) {
  if (Test-Path -LiteralPath $root) {
    $deployers += Get-ChildItem -LiteralPath $root -Filter WeaselDeployer.exe -Recurse -ErrorAction SilentlyContinue
  }
}
$deployer = $deployers | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $deployer) { throw '未找到 WeaselDeployer.exe' }
& $deployer.FullName /deploy
"""
    command = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encode_powershell(ps_script)}"
    logs = run_remote_task(session, entry, name="rime_sync_deploy", command=command, timeout=180)
    error_lines = [line for line in logs if "throw" in line.lower() or "error" in line.lower()]
    if error_lines:
        raise SyncError("远程部署可能失败：" + " | ".join(error_lines[-3:]))


def refresh_local_prediction(*, enabled: bool) -> dict[str, Any] | None:
    if not enabled:
        return None
    try:
        return refresh_rime_context_prediction_tree()
    except RimeContextPredictionError as exc:
        raise SyncError(f"刷新本机预测索引失败：{exc}") from exc


def pull_target_history_as_article(
    session: requests.Session,
    entry: UserDevice,
    *,
    limit: int,
    enabled: bool,
) -> dict[str, Any] | None:
    payload, status = request_json(
        session,
        entry,
        "GET",
        "/rime/context-prediction/history-article",
        params={"limit": limit},
        timeout=60,
        allow_statuses={404},
    )
    if status == 404:
        return {"status": "skipped", "message": "目标设备尚未部署输入历史接口。"}
    if not isinstance(payload, dict) or not payload.get("available"):
        return {
            "status": "skipped",
            "message": str((payload or {}).get("message") if isinstance(payload, dict) else "目标设备没有可用输入历史。"),
        }

    content = str(payload.get("content") or "").strip()
    if not content:
        return {"status": "skipped", "message": "目标设备输入历史为空。"}

    source_label = entry.name or entry.device_id or entry.entry_id
    import_rime_context_prediction_article(
        title=f"输入历史 · {source_label}",
        content=content,
        enabled=enabled,
        source_type="device_history",
        source_key=f"device_history:{entry.device_id or entry.entry_id}",
        source_label=f"输入历史 · {source_label}",
    )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "status": "imported",
        "entry_count": int(summary.get("entry_count") or 0),
        "char_count": int(summary.get("char_count") or len(content)),
    }


def sync_files(
    session: requests.Session,
    entry: UserDevice,
    *,
    source_dir: Path,
    target_rime_dir: str,
    dry_run: bool,
) -> dict[str, Any]:
    ensure_remote_directories(session, entry, target_rime_dir, SYNC_FILES, dry_run=dry_run)

    changed: list[str] = []
    skipped_missing: list[str] = []
    unchanged = 0

    for relative_path in SYNC_FILES:
        local = read_local_file(source_dir, relative_path)
        if local is None:
            skipped_missing.append(relative_path)
            continue
        text, local_hash = local
        target_path = remote_path(target_rime_dir, relative_path)
        remote_hash = remote_read_hash(session, entry, target_path)
        if remote_hash == local_hash:
            unchanged += 1
            continue
        changed.append(relative_path)
        if not dry_run:
            remote_write_text(session, entry, target_path, text)

    return {
        "changed": changed,
        "unchanged": unchanged,
        "skipped_missing": skipped_missing,
        "deploy_required": any(item in DEPLOY_TRIGGER_FILES for item in changed),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="增量同步主设备的小狼毫共享配置到辅助设备。")
    parser.add_argument("--target-name", default=DEFAULT_TARGET_NAME, help="目标设备名称，默认 codepc_mi15。")
    parser.add_argument("--target-entry-id", default="", help="目标设备 entry_id；传入后优先于 target-name。")
    parser.add_argument("--target-rime-dir", default="", help="目标设备 Rime 用户目录；留空时优先通过接口识别。")
    parser.add_argument("--source-dir", default="", help="主设备 Rime 用户目录；默认使用 APPDATA/Rime。")
    parser.add_argument("--dry-run", action="store_true", help="只比较，不写入目标设备。")
    parser.add_argument("--no-deploy", action="store_true", help="有变化时不触发目标设备重新部署。")
    parser.add_argument("--no-refresh-local", action="store_true", help="同步前不刷新主设备预测索引。")
    parser.add_argument("--no-pull-history", action="store_true", help="不同步目标设备输入历史到主设备文章。")
    parser.add_argument("--history-limit", type=int, default=200000, help="拉取目标输入历史事件上限。")
    parser.add_argument("--print-json", action="store_true", help="输出 JSON 结果。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = sync_rime_config_to_target(
        target_name=args.target_name,
        target_entry_id=args.target_entry_id or None,
        target_rime_dir=args.target_rime_dir or None,
        source_dir=args.source_dir or None,
        dry_run=args.dry_run,
        no_deploy=args.no_deploy,
        refresh_local=not args.no_refresh_local,
        pull_history=not args.no_pull_history,
        history_limit=args.history_limit,
    )
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("skipped"):
            print(f"跳过：{result.get('message')}")
            return 0
        sync_result = result["sync"]
        changed = ", ".join(sync_result["changed"]) or "无"
        print(f"目标设备：{result['target']}")
        print(f"目标目录：{result['target_rime_dir']}")
        if result.get("history"):
            print(f"历史拉取：{result['history']}")
        print(f"变更文件：{changed}")
        print(f"未变化文件数：{sync_result['unchanged']}")
        if sync_result["skipped_missing"]:
            print(f"本地缺失：{', '.join(sync_result['skipped_missing'])}")
        print(f"重新部署：{result['deploy']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
