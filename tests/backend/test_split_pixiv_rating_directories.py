from pathlib import Path

from scripts import split_pixiv_rating_directories as split_script


def test_pixiv_path_hints_include_sensitive_artwork_parent_directory(tmp_path: Path) -> None:
    media_path = tmp_path / "1、pixiv" / "ぱーきんそん" / "栗花落カナヲ① 捕縛_136260668" / "03.png"

    hints = split_script.pixiv_path_hints(tmp_path, media_path)

    assert hints == ("1、pixiv", "ぱーきんそん", "栗花落カナヲ1 捕縛_136260668", "03")
    assert split_script.classification_family({"x_restrict": 0, "tags": [], "path_hints": hints}) == "pixiv"


def test_rebuild_current_device_indexes_scans_both_rating_families(
    tmp_path: Path,
    monkeypatch,
) -> None:
    directory_names = [
        name
        for mapping in split_script.TIER_MAPPINGS
        for name in mapping
    ]
    for name in directory_names:
        (tmp_path / name).mkdir()

    calls: list[tuple[str, str]] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(split_script, "Session", lambda _engine: FakeSession())
    monkeypatch.setattr(split_script, "get_device_id", lambda: "current-device")

    def fake_scan(req, _session, *, device_id):
        assert req.recursive is True
        assert req.hash_mode == "never"
        assert req.mark_missing_as_dangling is True
        calls.append((Path(req.absolute_path).name, device_id))
        return {"ok": True, "processed_count": 1, "items": [{"ignored": True}]}

    monkeypatch.setattr(split_script, "scan_device_file_records", fake_scan)

    result = split_script.rebuild_current_device_indexes(tmp_path)

    assert calls == [(name, "current-device") for name in directory_names]
    assert list(result) == directory_names
    assert all(value == {"ok": True, "processed_count": 1} for value in result.values())
