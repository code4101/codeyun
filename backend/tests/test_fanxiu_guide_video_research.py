from __future__ import annotations

from backend.core.fanxiu.catalog.guide_video_research import (
    parse_guide_video_play_count,
    rank_guide_video_research_candidates,
    resolve_research_artifact,
    save_guide_video_research_snapshot,
    upsert_guide_video_research,
)
from backend.core.fanxiu.catalog.guide_videos import query_guide_videos, save_guide_video_snapshot


def test_play_count_supports_chinese_units() -> None:
    assert parse_guide_video_play_count("12.3万") == 123_000
    assert parse_guide_video_play_count("2831") == 2_831
    assert parse_guide_video_play_count("") == 0


def test_candidates_prefer_source_role_then_pinned_and_play_count() -> None:
    items = [
        {"item_id": "douyin:1", "source_role": "original", "play_text": "10万"},
        {"item_id": "douyin:2", "source_role": "guide", "play_text": "20万", "is_pinned": True},
        {"item_id": "douyin:3", "source_role": "clip", "play_text": "30万"},
        {"item_id": "douyin:4", "source_role": "official", "play_text": "99万"},
    ]
    ranked = rank_guide_video_research_candidates(items, limit=10)
    assert [item["item_id"] for item in ranked] == ["douyin:1", "douyin:3", "douyin:2"]


def test_query_enriches_catalog_with_research_and_urls(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    research_path = tmp_path / "research.json"
    download_path = tmp_path / "downloads.json"
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    save_guide_video_snapshot(
        {
            "status": "done",
            "items": [
                {
                    "item_id": "douyin:123",
                    "platform": "douyin",
                    "source_id": "douyin:yeqin",
                    "source_role": "original",
                    "video_id": "123",
                    "title": "仙窍攻略",
                }
            ],
        },
        catalog_path,
    )
    save_guide_video_research_snapshot(
        {
            "status": "done",
            "items": [
                {
                    "item_id": "douyin:123",
                    "status": "done",
                    "summary": "集中同元素。",
                    "local_video_path": str(video_path),
                }
            ],
        },
        research_path,
    )

    result = query_guide_videos(
        snapshot_path=catalog_path,
        research_snapshot_path=research_path,
        download_snapshot_path=download_path,
    )

    assert result["research_count"] == 1
    assert result["items"][0]["research"]["summary"] == "集中同元素。"
    assert "kind=media" in result["items"][0]["research"]["media_url"]


def test_upsert_and_resolve_artifacts(tmp_path, monkeypatch) -> None:
    snapshot_path = tmp_path / "research.json"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    upsert_guide_video_research(
        {
            "item_id": "douyin:123",
            "status": "done",
            "local_video_path": str(video),
        },
        snapshot_path,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.catalog.guide_video_research.guide_video_research_snapshot_path",
        lambda: snapshot_path,
    )

    assert resolve_research_artifact("douyin:123", "media") == video.resolve()
