from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core import media_membership_reconcile as reconcile


def test_enqueue_uses_independent_resource_and_per_user_platform_dedup(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "backend.core.jobs.local_runtime.submit_local_job_once",
        lambda **kwargs: calls.append(kwargs) or (SimpleNamespace(id="job-1"), True),
    )

    result = reconcile.enqueue_media_membership_reconcile(
        user_id=2,
        platform="PIXIV",
        root_dir=r"E:\data\m2510mn",
    )

    assert result == {"platform": "pixiv", "local_job_run_id": "job-1", "queued": True}
    assert calls == [
        {
            "job_type": "media.membership-reconcile",
            "payload": {
                "user_id": 2,
                "platform": "pixiv",
                "root_dir": r"E:\data\m2510mn",
            },
            "user_id": 2,
            "resource_key": "resource:media-sync:membership:pixiv",
            "dedup_key": "media-membership-reconcile:2:pixiv",
        }
    ]


def test_membership_resource_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="不支持"):
        reconcile.membership_reconcile_resource_key("video")
