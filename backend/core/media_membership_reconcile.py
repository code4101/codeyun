from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from backend.db import engine


SUPPORTED_MEMBERSHIP_PLATFORMS = frozenset({"pixiv", "pinterest"})


def normalize_membership_platform(platform: Any) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized not in SUPPORTED_MEMBERSHIP_PLATFORMS:
        raise ValueError(f"不支持的媒体站内喜好平台：{normalized or platform}")
    return normalized


def membership_reconcile_resource_key(platform: Any) -> str:
    """Keep remote membership work independent from local curation."""

    return f"resource:media-sync:membership:{normalize_membership_platform(platform)}"


def enqueue_media_membership_reconcile(
    *,
    user_id: int,
    platform: str,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Durably enqueue an idempotent remote-membership reconciliation."""

    from backend.core.jobs.local_runtime import submit_local_job_once

    normalized = normalize_membership_platform(platform)
    payload: dict[str, Any] = {"user_id": int(user_id), "platform": normalized}
    if root_dir:
        payload["root_dir"] = str(root_dir)
    run, queued = submit_local_job_once(
        job_type="media.membership-reconcile",
        payload=payload,
        user_id=int(user_id),
        resource_key=membership_reconcile_resource_key(normalized),
        dedup_key=f"media-membership-reconcile:{int(user_id)}:{normalized}",
    )
    return {
        "platform": normalized,
        "local_job_run_id": run.id,
        "queued": bool(queued),
    }


def enqueue_all_media_membership_reconciles() -> dict[str, Any]:
    """Daily safety net for handoffs that were interrupted or previously absent."""

    from backend.plugins.modules.media_sync.models import (
        MediaSyncProfile,
        ensure_private_media_sync_schema,
    )

    ensure_private_media_sync_schema()
    enqueued: list[dict[str, Any]] = []
    with Session(engine) as session:
        profiles = session.exec(select(MediaSyncProfile)).all()
        for profile in profiles:
            if profile.pixiv_enabled and str(profile.pixiv_bookmarks_url or "").strip():
                enqueued.append(
                    enqueue_media_membership_reconcile(
                        user_id=profile.user_id,
                        platform="pixiv",
                        root_dir=profile.root_dir,
                    )
                )
            if profile.pinterest_enabled and str(profile.pinterest_board_url or "").strip():
                enqueued.append(
                    enqueue_media_membership_reconcile(
                        user_id=profile.user_id,
                        platform="pinterest",
                        root_dir=profile.root_dir,
                    )
                )
    return {
        "profile_count": len(profiles),
        "jobs": enqueued,
        "queued_count": sum(1 for item in enqueued if item["queued"]),
    }


def run_media_membership_reconcile(
    context: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run one reconciliation inside the durable Local Job worker."""

    from backend.plugins.modules.media_sync.models import (
        MediaSyncProfile,
        ensure_private_media_sync_schema,
    )
    from backend.plugins.modules.media_sync.runtime import sync_job_manager

    user_id = int(payload.get("user_id") or 0)
    platform = normalize_membership_platform(payload.get("platform"))
    if user_id <= 0:
        raise ValueError("站内喜好补齐缺少用户 ID。")

    ensure_private_media_sync_schema()
    with Session(engine) as session:
        profile = session.exec(
            select(MediaSyncProfile).where(MediaSyncProfile.user_id == user_id)
        ).first()
        if profile is None:
            raise RuntimeError(f"未找到用户 {user_id} 的媒体同步配置。")
        if payload.get("root_dir"):
            profile.root_dir = str(payload["root_dir"])

    enabled = bool(getattr(profile, f"{platform}_enabled", False))
    collection_url = str(
        getattr(profile, "pixiv_bookmarks_url" if platform == "pixiv" else "pinterest_board_url", "")
        or ""
    ).strip()
    if not enabled or not collection_url:
        return {"platform": platform, "skipped": True, "reason": "platform_disabled"}

    context.raise_if_cancelled()
    context.heartbeat(
        stage=f"{platform}-membership",
        message=f"正在补齐 {platform.title()} 加权图片的站内喜好",
    )
    snapshot = sync_job_manager.run_now(
        profile,
        sources=[f"{platform}_membership"],
        scope_key=f"membership-reconcile:{context.run_id}",
    )
    if snapshot.get("error") or snapshot.get("stage") != "finished":
        raise RuntimeError(str(snapshot.get("error") or snapshot.get("message") or "站内喜好补齐失败"))
    return {
        "platform": platform,
        "stage": snapshot.get("stage"),
        "summary": (snapshot.get("summary") or {}).get(f"{platform}_membership", {}),
    }
