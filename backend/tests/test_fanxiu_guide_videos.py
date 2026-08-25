from __future__ import annotations

import pytest

from backend.core.fanxiu.catalog.guide_videos import (
    _douyin_role,
    _merge_video_items,
    _parse_dynamic_video,
    collect_wang_laomo_video_catalog,
    load_guide_video_snapshot,
    query_guide_videos,
    save_guide_video_snapshot,
)


def _dynamic_video(*, bvid: str, title: str, published_at: int = 100) -> dict:
    return {
        "id_str": f"dynamic-{bvid}",
        "type": "DYNAMIC_TYPE_AV",
        "modules": {
            "module_author": {
                "mid": 678872453,
                "name": "凡人修仙传王老魔",
                "pub_ts": str(published_at),
            },
            "module_dynamic": {
                "major": {
                    "archive": {
                        "bvid": bvid,
                        "title": title,
                        "desc": "叶钦攻略切片",
                        "cover": "http://example.com/cover.jpg",
                        "duration_text": "02:44",
                        "stat": {"play": "78"},
                    }
                }
            },
        },
    }


def test_parse_dynamic_video_keeps_source_identity() -> None:
    item = _parse_dynamic_video(
        _dynamic_video(bvid="BV1ypTZzME4y", title="凡人修仙传仙窍技巧")
    )

    assert item is not None
    assert item["url"] == "https://www.bilibili.com/video/BV1ypTZzME4y/"
    assert item["uploader_name"] == "凡人修仙传王老魔"
    assert item["platform"] == "bilibili"
    assert item["source_role"] == "clip"
    assert item["cover_url"].startswith("https://")


def test_query_guide_videos_filters_before_pagination(tmp_path) -> None:
    path = tmp_path / "guide-videos.json"
    parsed = [
        _parse_dynamic_video(_dynamic_video(bvid="BV1ypTZzME4y", title="凡人修仙传仙窍技巧", published_at=200)),
        _parse_dynamic_video(_dynamic_video(bvid="BV1svKy6UEwu", title="凡人修仙传功法书", published_at=100)),
    ]
    items = [item for item in parsed if item is not None]
    save_guide_video_snapshot(
        {
            "schema_version": 2,
            "status": "done",
            "sources": [],
            "target_count": 2,
            "done_count": 2,
            "updated_at": 1,
            "error": "",
            "items": items,
        },
        path,
    )

    result = query_guide_videos(query="仙窍", page=1, page_size=20, snapshot_path=path)

    assert result["total"] == 1
    assert result["items"][0]["bvid"] == "BV1ypTZzME4y"


def test_query_guide_videos_can_filter_source(tmp_path) -> None:
    path = tmp_path / "guide-videos.json"
    bilibili = _parse_dynamic_video(
        _dynamic_video(bvid="BV1ypTZzME4y", title="仙窍技巧", published_at=200)
    )
    douyin = {
        "item_id": "douyin:123",
        "platform": "douyin",
        "source_id": "douyin:yeqin",
        "source_role": "original",
        "video_id": "123",
        "title": "叶钦攻略",
        "published_at": 300,
        "uploader_name": "凡人修仙传叶钦",
    }
    save_guide_video_snapshot({"status": "done", "items": [bilibili, douyin]}, path)

    result = query_guide_videos(source_id="douyin:yeqin", snapshot_path=path)

    assert result["total"] == 1
    assert result["items"][0]["title"] == "叶钦攻略"


def test_merge_video_items_updates_duplicate_and_keeps_newest_first() -> None:
    old = {"bvid": "BV1ypTZzME4y", "title": "旧标题", "published_at": 100}
    updated = {"bvid": "BV1ypTZzME4y", "title": "新标题", "published_at": 200}
    other = {"bvid": "BV1uy4Xz7EJ4", "title": "另一条", "published_at": 150}

    merged = _merge_video_items([updated, other], [old])

    assert [item["bvid"] for item in merged] == ["BV1ypTZzME4y", "BV1uy4Xz7EJ4"]
    assert merged[0]["title"] == "新标题"


def test_douyin_following_roles_keep_fanxiu_sources_only() -> None:
    assert _douyin_role({"nickname": "凡人修仙传人界篇"}) == "official"
    assert _douyin_role({"nickname": "凡人修仙传叶钦"}) == "original"
    assert _douyin_role({"nickname": "王老魔"}) == "clip"
    assert _douyin_role({"nickname": "思瓜"}) == "guide"
    assert _douyin_role({"nickname": "毕导", "signature": "分享科学的快乐"}) == ""


def test_failed_sync_preserves_previous_catalog(tmp_path) -> None:
    path = tmp_path / "guide-videos.json"
    item = _parse_dynamic_video(
        _dynamic_video(bvid="BV1ypTZzME4y", title="凡人修仙传仙窍技巧")
    )
    save_guide_video_snapshot(
        {
            "status": "done",
            "target_count": 1,
            "done_count": 1,
            "items": [item],
        },
        path,
    )

    class FailedTab:
        def get(self, *_args, **_kwargs):
            return False

        def close(self):
            return None

    class FailedBrowser:
        def new_tab(self):
            return FailedTab()

    with pytest.raises(RuntimeError, match="无法打开"):
        collect_wang_laomo_video_catalog(browser=FailedBrowser(), snapshot_path=path)

    snapshot = load_guide_video_snapshot(path)
    assert snapshot["status"] == "error"
    assert [video["bvid"] for video in snapshot["items"]] == ["BV1ypTZzME4y"]
