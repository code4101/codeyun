from pathlib import Path

from backend.core.xiaoe_text_archive import (
    archive_text_article,
    build_text_archive_dir,
    localize_content_images,
)


def test_build_text_archive_dir_uses_text_year_and_timestamp(tmp_path: Path) -> None:
    assert build_text_archive_dir(tmp_path, "文章:一", "2024-05-06 07:08:09") == (
        tmp_path / "图文" / "2024" / "20240506_070809_文章_一"
    )


def test_localize_content_images_rewrites_and_deduplicates(tmp_path: Path) -> None:
    def fake_loader(url: str, stem: Path) -> Path:
        path = stem.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(url.encode())
        return path

    localized, count = localize_content_images(
        '<p><img src="https://cdn.test/a.png"><img data-src="https://cdn.test/a.png"></p>',
        tmp_path,
        image_loader=fake_loader,
    )
    assert count == 1
    assert "https://cdn.test" not in localized
    assert localized.count("images/001_") == 2


def test_archive_text_article_saves_cover_and_offline_body(tmp_path: Path) -> None:
    def fake_loader(url: str, stem: Path) -> Path:
        path = stem.with_suffix(".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(url.encode())
        return path

    result = archive_text_article(
        title="离线文章",
        published_at="2025-01-02 03:04:05",
        content_html='<p><img src="https://cdn.test/body.jpg"></p>',
        cover_url="https://cdn.test/cover.jpg",
        source_url="https://admin.test/detail/1",
        output_dir=tmp_path,
        image_loader=fake_loader,
    )
    saved = Path(str(result["path"])).read_text(encoding="utf-8")
    assert result["body_image_count"] == 1
    assert "https://cdn.test" not in saved
    assert 'src="cover.jpg"' in saved


def test_localize_content_images_rewrites_css_and_video_poster(tmp_path: Path) -> None:
    def fake_loader(url: str, stem: Path) -> Path:
        path = stem.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(url.encode())
        return path

    localized, count = localize_content_images(
        '<style>.hero{background:url(https://cdn.test/a.png)}</style>'
        '<video poster="//cdn.test/poster.jpg"></video>',
        tmp_path,
        image_loader=fake_loader,
    )
    assert count == 2
    assert "cdn.test" not in localized


def test_localize_content_images_prefers_real_lazy_url_over_blob(tmp_path: Path) -> None:
    def fake_loader(url: str, stem: Path) -> Path:
        path = stem.with_suffix(".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(url.encode())
        return path

    localized, count = localize_content_images(
        '<img src="blob:https://admin.xiaoe-tech.com/session" '
        'data-src="https://cdn.test/real.jpg"><img src="blob:temporary">',
        tmp_path,
        image_loader=fake_loader,
    )

    assert count == 1
    assert "blob:" not in localized
    assert "cdn.test" not in localized
