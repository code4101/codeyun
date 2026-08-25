from pathlib import Path

from backend.core.xiaoe_video_archive import build_archive_path
from scripts.download_xiaoe_video_incremental import _item_is_known, _item_key


def test_item_key_ignores_list_badges() -> None:
    assert _item_key("课程标题\n免费", "2026-08-03 08:00:00") == _item_key(
        "课程标题", "2026-08-03 08:00:00"
    )


def test_item_is_known_from_catalog_index(tmp_path: Path) -> None:
    item = {"title": "新课", "published_at": "2026-08-03 08:00:00"}
    index = {"items": {_item_key(**item): {"outcome": "downloaded"}}}
    assert _item_is_known(
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
