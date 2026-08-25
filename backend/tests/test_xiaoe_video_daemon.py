import json
from pathlib import Path
from types import SimpleNamespace

from scripts.download_xiaoe_video_daemon import (
    _complete_page_metadata,
    _catalog_total_from_text,
    _cursor_reached_catalog_end,
    _find_playlist_url,
    _load_state,
    _next_cursor,
    _normalize_page_title,
    _playlist_url_from_packet,
    _preview_unavailable_reason,
)


def test_next_cursor_stays_on_page_until_last_item() -> None:
    assert _next_cursor(4, 2) == (4, 3)
    assert _next_cursor(4, 9) == (5, 0)


def test_cursor_reached_catalog_end_handles_partial_last_page() -> None:
    assert _cursor_reached_catalog_end(164, 3, 1633)
    assert not _cursor_reached_catalog_end(164, 2, 1633)
    assert not _cursor_reached_catalog_end(164, 3, None)


def test_catalog_total_is_not_tied_to_old_full_download_count() -> None:
    assert _catalog_total_from_text("共1635条，每页10条") == 1635
    assert _catalog_total_from_text("列表加载中") is None


def test_load_state_uses_explicit_initial_cursor(tmp_path: Path) -> None:
    state = _load_state(tmp_path / "state.json", start_page=4, start_item=2)
    assert state["cursor"] == {"page": 4, "item_index": 2}


def test_load_state_resumes_persisted_cursor(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "cursor": {"page": 7, "item_index": 5},
                "completed_count": 12,
                "special_items": [{"title": "旧格式"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = _load_state(path, start_page=4, start_item=2)

    assert state["cursor"] == {"page": 7, "item_index": 5}
    assert state["completed_count"] == 12
    assert state["special_items"] == [{"title": "旧格式"}]


def test_preview_unavailable_reason_distinguishes_empty_upload() -> None:
    assert _preview_unavailable_reason(
        "*上传视频：\n选择文件\n视频格式：mp4",
        has_preview=False,
    ) == "详情页未绑定可预览视频（上传区域显示“选择文件”）"
    assert _preview_unavailable_reason(
        "*上传视频：\n暂不支持预览",
        has_preview=False,
    ) == "后台显示暂不支持预览"
    assert _preview_unavailable_reason(
        "*上传视频：\n旧视频\n1920x1080",
        has_preview=False,
        has_inactive_preview=True,
    ) == "详情页视频预览处于不可播放状态"
    assert _preview_unavailable_reason("正常视频", has_preview=True) is None


def test_playlist_url_from_direct_request() -> None:
    packet = SimpleNamespace(
        url="https://cdn.example/video.m3u8?token=abc",
        response=None,
    )
    assert _playlist_url_from_packet(packet) == packet.url


def test_playlist_url_from_get_play_url_response() -> None:
    playlist_url = "https://cdn.example/drm/video.m3u8?token=abc"
    body = {
        "code": 0,
        "data": {
            "sign": {
                "default_play": "720p_hls",
                "play_list": {
                    "720p_hls": {"play_url": playlist_url},
                    "720p_mp4": {"play_url": "https://cdn.example/video.mp4"},
                },
            },
        },
    }
    packet = SimpleNamespace(
        url="https://admin.example/xe.material-center.play/getPlayUrl",
        response=SimpleNamespace(body=body),
    )
    assert _playlist_url_from_packet(packet) == playlist_url
    assert _find_playlist_url({"play_url": "https://cdn.example/video.mp4"}) is None


def test_complete_page_metadata_fills_hidden_sale_time() -> None:
    class FakeTab:
        def run_js(self, *_args, **_kwargs):
            return [
                {"title": "正常条目", "published_at": "2024-07-15 11:03:34"},
                {"title": "已下架条目", "published_at": "2024-09-26 09:20:58"},
            ]

    rows = [
        {"title": "正常条目", "published_at": "2024-07-15 11:03:34"},
        {"title": "已下架条目", "published_at": ""},
    ]
    assert _complete_page_metadata(FakeTab(), rows, 50) == [
        {"title": "正常条目", "published_at": "2024-07-15 11:03:34"},
        {"title": "已下架条目", "published_at": "2024-09-26 09:20:58"},
    ]


def test_complete_page_metadata_ignores_status_badges_in_dom_title() -> None:
    class FakeTab:
        def run_js(self, *_args, **_kwargs):
            return [
                {"title": "梵行观的应用", "published_at": "2023-09-28 00:12:36"},
                {"title": "课程标题", "published_at": "2023-09-27 00:12:35"},
            ]

    rows = [
        {"title": "梵行观的应用\n免费", "published_at": ""},
        {"title": "课程标题\n指定用户\u00a0/\u00a07天", "published_at": ""},
    ]
    assert _complete_page_metadata(FakeTab(), rows, 91) == [
        {"title": "梵行观的应用", "published_at": "2023-09-28 00:12:36"},
        {"title": "课程标题", "published_at": "2023-09-27 00:12:35"},
    ]
    assert _normalize_page_title("课程标题\n指定用户 / 7天") == "课程标题"
