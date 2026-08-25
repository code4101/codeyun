from __future__ import annotations

from backend.core.fanxiu.catalog.guide_video_downloads import (
    load_guide_video_download_snapshot,
    rank_guide_video_download_candidates,
    save_guide_video_download_snapshot,
)
from backend.core.fanxiu.catalog.guide_videos import query_guide_videos, save_guide_video_snapshot


def test_download_candidates_prefer_pinned_then_play_count() -> None:
    items = [
        {"item_id": "douyin:1", "platform": "douyin", "source_role": "original", "play_text": "10万"},
        {"item_id": "douyin:2", "platform": "douyin", "source_role": "guide", "play_text": "20", "is_pinned": True},
        {"item_id": "bilibili:3", "platform": "bilibili", "source_role": "clip", "play_text": "30万"},
    ]
    ranked = rank_guide_video_download_candidates(items)
    assert [item["item_id"] for item in ranked] == ["douyin:2", "bilibili:3", "douyin:1"]


def test_download_candidates_skip_done_and_retry_backoff() -> None:
    items = [
        {"item_id": "douyin:1", "platform": "douyin"},
        {"item_id": "douyin:2", "platform": "douyin"},
        {"item_id": "douyin:3", "platform": "douyin"},
    ]
    records = [
        {"item_id": "douyin:1", "status": "done"},
        {"item_id": "douyin:2", "status": "error", "retry_after": 200},
    ]
    ranked = rank_guide_video_download_candidates(items, records, now=100)
    assert [item["item_id"] for item in ranked] == ["douyin:3"]


def test_download_snapshot_counts_records(tmp_path) -> None:
    path = tmp_path / "downloads.json"
    save_guide_video_download_snapshot(
        {
            "status": "running",
            "target_count": 3,
            "items": [
                {"item_id": "douyin:1", "status": "done"},
                {"item_id": "douyin:2", "status": "error"},
            ],
        },
        path,
    )
    snapshot = load_guide_video_download_snapshot(path)
    assert snapshot["done_count"] == 1
    assert snapshot["failed_count"] == 1
    assert snapshot["target_count"] == 3


def test_catalog_query_exposes_download_progress(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    download_path = tmp_path / "downloads.json"
    save_guide_video_snapshot(
        {
            "status": "done",
            "items": [{"item_id": "douyin:1", "platform": "douyin", "title": "攻略"}],
        },
        catalog_path,
    )
    save_guide_video_download_snapshot(
        {
            "status": "running",
            "target_count": 1,
            "current_item_id": "douyin:1",
            "items": [{"item_id": "douyin:1", "status": "done"}],
        },
        download_path,
    )
    result = query_guide_videos(
        snapshot_path=catalog_path,
        research_snapshot_path=tmp_path / "research.json",
        download_snapshot_path=download_path,
    )
    assert result["download_status"] == "running"
    assert result["download_done_count"] == 1
    assert result["items"][0]["download"]["status"] == "done"
