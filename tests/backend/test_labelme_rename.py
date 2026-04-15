import json

import pytest
from fastapi import HTTPException

from backend.api.filesystem import LabelmeRenameRequest, rename_labelme_annotation_pair


def test_rename_labelme_annotation_pair_moves_image_and_json(tmp_path):
    base_dir = tmp_path / "dataset"
    source_dir = base_dir / "a" / "b"
    source_dir.mkdir(parents=True)
    source_image = source_dir / "c.jpg"
    source_json = source_dir / "c.json"
    source_image.write_bytes(b"jpg")
    source_json.write_text(
        json.dumps(
            {
                "version": "5.1.7",
                "imagePath": "c.jpg",
                "shapes": [{"label": "target", "points": [[1, 2], [3, 4]]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = rename_labelme_annotation_pair(
        LabelmeRenameRequest(
            absolute_path=str(source_image),
            base_absolute_path=str(base_dir),
            target_relative_path="d/e.jpg",
        )
    )

    target_image = base_dir / "d" / "e.jpg"
    target_json = base_dir / "d" / "e.json"
    assert result["ok"] is True
    assert result["target_relative_path"] == "d/e.jpg"
    assert target_image.read_bytes() == b"jpg"
    assert not source_image.exists()
    assert not source_json.exists()
    payload = json.loads(target_json.read_text(encoding="utf-8"))
    assert payload["imagePath"] == "e.jpg"
    assert payload["shapes"][0]["label"] == "target"


def test_rename_labelme_annotation_pair_requires_overwrite_for_existing_target(tmp_path):
    base_dir = tmp_path / "dataset"
    source_dir = base_dir / "a"
    target_dir = base_dir / "d"
    source_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    source_image = source_dir / "c.jpg"
    source_json = source_dir / "c.json"
    target_image = target_dir / "e.jpg"
    target_json = target_dir / "e.json"
    source_image.write_bytes(b"source")
    source_json.write_text('{"imagePath":"c.jpg","shapes":[]}', encoding="utf-8")
    target_image.write_bytes(b"target")
    target_json.write_text('{"imagePath":"e.jpg","shapes":[]}', encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        rename_labelme_annotation_pair(
            LabelmeRenameRequest(
                absolute_path=str(source_image),
                base_absolute_path=str(base_dir),
                target_relative_path="d/e.jpg",
            )
        )

    assert exc_info.value.status_code == 409
    assert target_image.read_bytes() == b"target"
    assert source_image.read_bytes() == b"source"

    result = rename_labelme_annotation_pair(
        LabelmeRenameRequest(
            absolute_path=str(source_image),
            base_absolute_path=str(base_dir),
            target_relative_path="d/e.jpg",
            overwrite=True,
        )
    )

    assert result["overwritten"] is True
    assert target_image.read_bytes() == b"source"
    payload = json.loads(target_json.read_text(encoding="utf-8"))
    assert payload["imagePath"] == "e.jpg"


def test_rename_labelme_annotation_pair_overwrite_removes_stale_target_json_when_source_has_none(tmp_path):
    base_dir = tmp_path / "dataset"
    source_dir = base_dir / "a"
    target_dir = base_dir / "d"
    source_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    source_image = source_dir / "c.jpg"
    target_image = target_dir / "e.jpg"
    target_json = target_dir / "e.json"
    source_image.write_bytes(b"source")
    target_image.write_bytes(b"target")
    target_json.write_text('{"imagePath":"old.jpg","shapes":[]}', encoding="utf-8")

    result = rename_labelme_annotation_pair(
        LabelmeRenameRequest(
            absolute_path=str(source_image),
            base_absolute_path=str(base_dir),
            target_relative_path="d/e.jpg",
            overwrite=True,
        )
    )

    assert result["overwritten"] is True
    assert target_image.read_bytes() == b"source"
    assert not target_json.exists()
