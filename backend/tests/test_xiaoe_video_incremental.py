from pathlib import Path
from types import SimpleNamespace
import json

from backend.core.xiaoe_video_archive import build_archive_path
from scripts.download_xiaoe_video_incremental import _item_is_known, _item_key


def test_item_key_ignores_list_badges() -> None:
    assert _item_key("课程标题\n免费", "2026-08-03 08:00:00") == _item_key(
        "课程标题", "2026-08-03 08:00:00"
    )


def test_downloaded_index_without_file_is_missing(tmp_path: Path) -> None:
    item = {"title": "新课", "published_at": "2026-08-03 08:00:00"}
    index = {"items": {_item_key(**item): {"outcome": "downloaded"}}}
    assert not _item_is_known(
        item=item,
        output_dir=tmp_path,
        index=index,
        legacy_special_keys=set(),
    )


def test_item_is_known_from_existing_archive(tmp_path: Path) -> None:
    item = {"title": "旧课", "published_at": "2026-08-02 08:00:00"}
    path = build_archive_path(tmp_path, item["title"], item["published_at"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"existing")
    assert _item_is_known(
        item=item,
        output_dir=tmp_path,
        index={},
        legacy_special_keys=set(),
    )


def test_item_is_known_from_legacy_special_state(tmp_path: Path) -> None:
    item = {"title": "不可预览课", "published_at": "2026-08-01 08:00:00"}
    assert _item_is_known(
        item=item,
        output_dir=tmp_path,
        index={},
        legacy_special_keys={_item_key(**item)},
    )


def test_zero_byte_archive_is_missing(tmp_path: Path) -> None:
    item = {"title": "旧课", "published_at": "2026-08-02 08:00:00"}
    path = build_archive_path(tmp_path, **item)
    path.parent.mkdir(parents=True)
    path.touch()
    assert not _item_is_known(item=item, output_dir=tmp_path, index={}, legacy_special_keys=set())


def test_existing_legacy_badge_filename_is_not_downloaded_again(tmp_path: Path) -> None:
    item = {"title": "旧课", "published_at": "2026-08-02 08:00:00"}
    path = build_archive_path(tmp_path, item["title"] + "\n指定用户", item["published_at"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"existing")
    assert _item_is_known(item=item, output_dir=tmp_path, index={}, legacy_special_keys=set())


def test_full_scan_repairs_after_existing_and_continues_after_failure(tmp_path: Path, monkeypatch) -> None:
    import scripts.download_xiaoe_video_incremental as module

    items = [{"resource_id": f"v_{i}", "title": f"课{i}", "published_at": "2026-08-02 08:00:00"}
             for i in range(4)]
    existing = build_archive_path(tmp_path, items[0]["title"], items[0]["published_at"])
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    browser = SimpleNamespace(tab_ids=["anchor"])
    monkeypatch.setattr(module, "_load_xlproject_env", lambda: None)
    monkeypatch.setattr(module, "_open_browser", lambda: browser)
    monkeypatch.setattr(module, "_get_or_open_list_tab", lambda _b: SimpleNamespace(tab_id="anchor"))
    monkeypatch.setattr(module, "_goto_page", lambda *_args: None)
    monkeypatch.setattr(module, "read_video_catalog", lambda _tab: items)
    attempted = []

    def open_detail(_browser, resource_id):
        attempted.append(resource_id)
        if resource_id == "v_1":
            raise RuntimeError("播放地址读取失败")
        return SimpleNamespace(url=resource_id, close=lambda: None)

    monkeypatch.setattr(module, "open_video_detail", open_detail)
    monkeypatch.setattr(module, "_capture_playlist", lambda _t: "https://cdn.example/video.m3u8")
    monkeypatch.setattr(module, "download_hls_video", lambda **kw: {"status": "downloaded"})
    state = module.run_incremental(SimpleNamespace(output_dir=tmp_path))
    assert attempted == ["v_1", "v_2", "v_3"]
    assert state["status"] == "failed"
    assert state["remaining_count"] == 1
    assert len(state["new_downloaded"]) == 2
    audit = json.loads(Path(state["audit_path"]).read_text(encoding="utf-8"))
    assert audit["counts"] == {"existing": 1, "missing": 3, "special": 0}


def test_catalog_scan_rejects_partial_page(monkeypatch) -> None:
    import scripts.download_xiaoe_video_daemon as module
    import pytest

    monkeypatch.setattr(module, "_ensure_list_ready", lambda _tab: None)
    monkeypatch.setattr(module, "_catalog_total", lambda _tab: 2)
    tab = SimpleNamespace(run_js=lambda *_a, **_k: {"code": 0, "data": {"total": 2, "list": []}})
    with pytest.raises(module.XiaoeDaemonError, match="数量不完整"):
        module.read_video_catalog(tab)


def test_catalog_scan_rejects_duplicate_ids(monkeypatch) -> None:
    import scripts.download_xiaoe_video_daemon as module
    import pytest

    monkeypatch.setattr(module, "_ensure_list_ready", lambda _tab: None)
    monkeypatch.setattr(module, "_catalog_total", lambda _tab: 2)
    row = {"resource_id": "v_1", "title": "课", "sale_at": "2026-08-02 08:00:00"}
    tab = SimpleNamespace(run_js=lambda *_a, **_k: {"code": 0, "data": {"total": 2, "list": [row, row]}})
    with pytest.raises(module.XiaoeDaemonError, match="重复 ID"):
        module.read_video_catalog(tab)
