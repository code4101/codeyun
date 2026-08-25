from __future__ import annotations

import io

from PIL import Image

from backend.api import filesystem


def test_heic_thumbnail_is_decoded_to_browser_native_jpeg(monkeypatch, tmp_path) -> None:
    source_path = tmp_path / "sample.heic"
    Image.new("RGB", (640, 480), color=(32, 96, 160)).save(source_path, format="HEIF")
    monkeypatch.setattr(
        filesystem,
        "resolve_request_path",
        lambda *_args, **_kwargs: (source_path, {"root": "test", "path": source_path.name}),
    )

    response = filesystem.build_thumbnail_response(absolute_path=str(source_path), max_edge=320)

    assert response.media_type == "image/jpeg"
    with Image.open(io.BytesIO(response.body)) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (320, 240)


def test_heif_extension_is_supported_by_media_listing() -> None:
    assert ".heic" in filesystem.IMAGE_EXTENSIONS
    assert ".heif" in filesystem.IMAGE_EXTENSIONS
    assert ".heic" in Image.registered_extensions()
    assert ".heif" in Image.registered_extensions()
