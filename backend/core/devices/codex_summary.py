from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import requests
from fastapi import HTTPException
from sqlmodel import Session, select

from backend.core.devices import device as device_core
from backend.core.codex.sessions import (
    annotate_codex_daily_summary_source,
    cache_remote_codex_thread_detail,
    cache_remote_codex_workload,
    collect_cached_codex_daily_summary_source,
    collect_codex_daily_summary_source,
    merge_codex_daily_summary_sources,
    resolve_codex_daily_summary_epoch_range,
)
from backend.models import CodexTextCacheTurn, UserDevice

CODEX_REMOTE_READ_TIMEOUT_SECONDS = 120
CODEX_REMOTE_WORKLOAD_TIMEOUT_SECONDS = 180
REMOTE_DEVICE_DIRECT_PROXIES = {"http": "", "https": "", "all": "", "no_proxy": "*"}


def codex_summary_entry_label(entry: UserDevice | dict[str, Any]) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("device_id") or entry.get("entry_id") or "").strip()
    return str(entry.name or entry.device_id or entry.entry_id).strip()


def snapshot_codex_summary_entries(entries: list[UserDevice]) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": entry.entry_id,
            "user_id": entry.user_id,
            "device_id": entry.device_id,
            "name": codex_summary_entry_label(entry),
            "mode": entry.mode,
            "server_url": entry.server_url,
            "token": entry.token,
        }
        for entry in entries
    ]


def build_multi_codex_summary_identity(entry_specs: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    entry_ids = [str(entry["entry_id"]) for entry in entry_specs]
    digest = hashlib.sha1(",".join(entry_ids).encode("utf-8")).hexdigest()[:12]
    root_dir = f"{len(entry_ids)} 台设备各自默认 .codex"
    return (
        f"entries:{','.join(entry_ids)}",
        {
            "root_key": f"device-entries:{digest}:default-codex",
            "root_dir": root_dir,
            "default_root_dir": "",
        },
    )


def ensure_local_codex_entry(entry: UserDevice) -> None:
    if entry.mode != "local":
        raise HTTPException(status_code=400, detail="This entry is not a local entry")

    local_device_id = device_core.get_device_id()
    if entry.device_id != local_device_id:
        raise HTTPException(status_code=409, detail="Local entry device_id does not match current node")


def remote_codex_base_url(entry: UserDevice | SimpleNamespace) -> str:
    if entry.mode != "remote":
        raise HTTPException(status_code=400, detail="This entry is not a remote entry")
    if not entry.server_url:
        raise HTTPException(status_code=400, detail="Remote entry has no server_url configured")
    return str(entry.server_url).rstrip("/")


def remote_codex_headers(entry: UserDevice | SimpleNamespace) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {entry.token}",
        "X-Device-Token": str(entry.token),
    }


def fetch_remote_codex_json(
    entry: UserDevice | SimpleNamespace,
    method: str,
    path: str,
    *,
    timeout: int = 20,
) -> dict[str, Any] | list[Any]:
    target_url = f"{remote_codex_base_url(entry)}/api{path}"
    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=remote_codex_headers(entry),
            proxies=REMOTE_DEVICE_DIRECT_PROXIES.copy(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"连接远端设备失败：{exc}") from exc

    if resp.status_code >= 400:
        detail = None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
            if isinstance(raw_detail, str) and raw_detail.strip():
                detail = raw_detail.strip()
        raise HTTPException(
            status_code=resp.status_code,
            detail=detail or resp.text.strip() or f"远端请求失败：HTTP {resp.status_code}",
        )

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        raise HTTPException(status_code=502, detail="远端设备返回的不是 JSON 数据")
    return resp.json()


def collect_remote_codex_entry_daily_summary_source(
    entry_spec: dict[str, Any],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    remote_entry = SimpleNamespace(**entry_spec)
    workload_payload = fetch_remote_codex_json(
        remote_entry,
        "GET",
        "/codex/workload",
        timeout=CODEX_REMOTE_WORKLOAD_TIMEOUT_SECONDS,
    )
    if not isinstance(workload_payload, dict):
        raise HTTPException(status_code=502, detail="远端 Codex workload 返回格式不正确")

    try:
        cache_info = cache_remote_codex_workload(entry_spec["entry_id"], workload_payload, session=session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"写入远端 Codex 缓存失败：{exc}") from exc

    root_key = str(cache_info["root_key"])
    _, day_start_at, day_end_at = resolve_codex_daily_summary_epoch_range(target_date_text)
    thread_ids = sorted(
        {
            str(thread_id)
            for thread_id in session.exec(
                select(CodexTextCacheTurn.thread_id)
                .where(
                    CodexTextCacheTurn.root_key == root_key,
                    CodexTextCacheTurn.end_at > day_start_at,
                    CodexTextCacheTurn.start_at < day_end_at,
                )
            ).all()
            if str(thread_id or "").strip()
        }
    )

    for thread_id in thread_ids:
        detail_payload = fetch_remote_codex_json(
            remote_entry,
            "GET",
            f"/codex/threads/{thread_id}",
            timeout=CODEX_REMOTE_READ_TIMEOUT_SECONDS,
        )
        if not isinstance(detail_payload, dict):
            raise HTTPException(status_code=502, detail=f"远端 Codex 会话 {thread_id} 返回格式不正确")
        try:
            cache_remote_codex_thread_detail(entry_spec["entry_id"], detail_payload, session=session)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"写入远端 Codex 会话缓存失败：{exc}") from exc

    return collect_cached_codex_daily_summary_source(
        root_key,
        target_date_text,
        user_id=user_id,
        session=session,
    )


def collect_multi_codex_daily_summary_source(
    entry_specs: list[dict[str, Any]],
    root_identity: dict[str, str],
    target_date_text: str,
    *,
    user_id: int | None,
    session: Session,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    source_failures: list[dict[str, Any]] = []
    for entry_spec in entry_specs:
        device_name = codex_summary_entry_label(entry_spec)
        try:
            if entry_spec["mode"] == "local":
                local_entry = UserDevice(**entry_spec)
                ensure_local_codex_entry(local_entry)
                source = collect_codex_daily_summary_source(
                    None,
                    target_date_text,
                    user_id=user_id,
                    session=session,
                )
            else:
                source = collect_remote_codex_entry_daily_summary_source(
                    entry_spec,
                    target_date_text,
                    user_id=user_id,
                    session=session,
                )
        except HTTPException as exc:
            source_failures.append(
                {
                    "entry_id": str(entry_spec.get("entry_id") or ""),
                    "device_id": str(entry_spec.get("device_id") or ""),
                    "device_name": device_name,
                    "mode": str(entry_spec.get("mode") or ""),
                    "status_code": int(exc.status_code),
                    "error": str(exc.detail),
                }
            )
            continue

        sources.append(
            annotate_codex_daily_summary_source(
                source,
                source_entry_id=str(entry_spec["entry_id"]),
                source_device_name=device_name,
            )
        )

    if not sources:
        failure_text = "；".join(f"{item['device_name']}：{item['error']}" for item in source_failures)
        raise HTTPException(
            status_code=502,
            detail=f"所有设备 Codex 数据读取失败：{failure_text or '没有可读取的设备来源'}",
        )

    merged = merge_codex_daily_summary_sources(
        sources,
        root_key=root_identity["root_key"],
        root_dir=root_identity["root_dir"],
        target_date_text=target_date_text,
        user_id=user_id,
        session=session,
    )
    if source_failures:
        merged["source_failures"] = source_failures
    return merged
