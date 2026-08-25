from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.plugins.modules.media_sync import api
from backend.plugins.modules.media_sync.schemas import MediaSyncPlatformActionRequest


class _FakeJobManager:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.status_calls: list[dict] = []

    def start(self, profile, **kwargs) -> None:
        self.start_calls.append(kwargs)

    def build_status_response(self, profile, **kwargs):
        self.status_calls.append(kwargs)
        return {"running": True}


def _patch_profile_dependencies(monkeypatch, manager: _FakeJobManager) -> None:
    monkeypatch.setattr(api, "sync_job_manager", manager)
    monkeypatch.setattr(
        api,
        "_get_or_create_profile",
        lambda session, user_id: SimpleNamespace(user_id=user_id, root_dir=r"E:\data\m2510mn"),
    )
    monkeypatch.setattr(
        api,
        "_apply_action_payload",
        lambda profile, payload: {"payload_applied": True},
    )
    monkeypatch.setattr(
        api,
        "candidate_review_weight_summary",
        lambda **_kwargs: {"review_count": 200, "positive_weight_count": 1},
    )


def test_candidate_clean_uses_the_candidate_scope_for_start_and_status(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    payload = MediaSyncPlatformActionRequest(
        path=r"E:\data\m2510mn\2、pixiv",
    )

    result = api.start_candidate_clean(
        payload,
        session=object(),
        current_user=SimpleNamespace(id=7),
    )

    assert result == {"running": True}
    assert manager.start_calls == [
        {
            "sources": ["pixiv_curate"],
            "overrides": {"payload_applied": True, "pixiv_rating_family": "pixiv"},
            "scope_key": "candidate:pixiv",
        }
    ]


def test_candidate_clean_requires_confirmation_when_the_whole_batch_is_unweighted(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    monkeypatch.setattr(
        api,
        "candidate_review_weight_summary",
        lambda **_kwargs: {"review_count": 200, "positive_weight_count": 0},
    )
    payload = MediaSyncPlatformActionRequest(path=r"E:\data\m2510mn\2、pixiv")

    with pytest.raises(HTTPException) as exc_info:
        api.start_candidate_clean(payload, session=object(), current_user=SimpleNamespace(id=7))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "unweighted_batch_confirmation_required",
        "platform": "pixiv",
        "review_count": 200,
        "positive_weight_count": 0,
        "message": "当前整批没有任何加权图片，继续会删除整批候选。",
    }
    assert manager.start_calls == []


def test_candidate_clean_accepts_explicit_unweighted_batch_confirmation(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    monkeypatch.setattr(
        api,
        "candidate_review_weight_summary",
        lambda **_kwargs: {"review_count": 200, "positive_weight_count": 0},
    )
    payload = MediaSyncPlatformActionRequest(
        path=r"E:\data\m2510mn\2、pixiv",
        confirm_unweighted_batch=True,
    )

    result = api.start_candidate_clean(payload, session=object(), current_user=SimpleNamespace(id=7))

    assert result == {"running": True}
    assert manager.start_calls[0]["sources"] == ["pixiv_curate"]
    assert manager.status_calls == [
        {
            "scope_key": "candidate:pixiv",
            "include_sources": False,
        }
    ]


def test_pixiv_candidate_cache_status_is_retired():
    with pytest.raises(HTTPException) as exc_info:
        api.get_candidate_cache_status(
            path=r"E:\data\m2510mn\2、pixiv",
            session=object(),
            current_user=SimpleNamespace(id=7),
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "pixiv_url_candidate_cache_retired"

def test_platform_download_uses_the_default_platform_scope(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    payload = MediaSyncPlatformActionRequest()

    result = api.start_platform_download(
        "pixiv",
        payload,
        session=object(),
        current_user=SimpleNamespace(id=7),
    )

    assert result == {"running": True}
    assert manager.start_calls == [
        {
            "sources": ["pixiv_download"],
            "overrides": {"payload_applied": True, "pixiv_rating_family": "pixiv"},
        }
    ]
    assert manager.status_calls == [{}]


def test_video_candidate_download_uses_explicit_video_scope(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    payload = MediaSyncPlatformActionRequest(
        path=r"E:\data\m2510mn\2、video",
        urls=["https://www.bilibili.com/video/BV1K4411m7jx/"],
    )

    result = api.start_candidate_download(
        payload,
        session=object(),
        current_user=SimpleNamespace(id=7),
    )

    assert result == {"running": True}
    assert manager.start_calls == [
        {
            "sources": ["video_download"],
            "overrides": {"payload_applied": True, "platform_download_target_count": 20},
            "scope_key": "candidate:video",
        }
    ]


def test_legacy_pixi_candidate_path_uses_unified_pixiv_scope(monkeypatch):
    manager = _FakeJobManager()
    _patch_profile_dependencies(monkeypatch, manager)
    payload = MediaSyncPlatformActionRequest(path=r"E:\data\m2510mn\2、pixi")

    result = api.start_candidate_download(
        payload,
        session=object(),
        current_user=SimpleNamespace(id=7),
    )

    assert result == {"running": True}
    assert manager.start_calls == [
        {
            "sources": ["pixiv_download"],
            "overrides": {"payload_applied": True, "pixiv_rating_family": "pixiv"},
            "scope_key": "candidate:pixiv",
        }
    ]
