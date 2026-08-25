from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session

from backend.core.file_publication_time import parse_publication_timestamp, set_file_publication_time
from backend.models import DeviceFile
from backend.plugins.modules.media_sync import models, sources


PIXIV_DATE = "2020-01-02T03:04:05+09:00"


def test_set_file_publication_time_preserves_bytes_and_sets_filesystem_dates(tmp_path) -> None:
    path = tmp_path / "sample.jpg"
    path.write_bytes(b"original-pixiv-bytes")
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()

    timestamp = set_file_publication_time(path, PIXIV_DATE)
    stat_result = path.stat()

    assert timestamp == parse_publication_timestamp(PIXIV_DATE)
    assert abs(stat_result.st_mtime - timestamp) <= 2
    if os.name == "nt":
        assert abs(stat_result.st_ctime - timestamp) <= 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest_before


def test_new_pixiv_download_applies_artwork_create_date(tmp_path) -> None:
    path = tmp_path / "download.png"
    path.write_bytes(b"download")

    applied = sources.align_pixiv_file_to_publication_date(
        path,
        {"artwork_id": "123", "create_date": PIXIV_DATE},
        lambda _message: None,
    )

    assert applied is True
    assert abs(path.stat().st_mtime - parse_publication_timestamp(PIXIV_DATE)) <= 2


def test_backfill_pixiv_publication_times_supports_preview_and_updates_index(tmp_path, engine, monkeypatch) -> None:
    path = tmp_path / "pixiv" / "author" / "123_sample.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"sample")
    original_mtime = path.stat().st_mtime
    monkeypatch.setattr(sources, "engine", engine)
    monkeypatch.setattr(sources, "load_cached_pixiv_artwork_create_dates", lambda _root: {"123": PIXIV_DATE})

    with Session(engine) as session:
        session.add(
            models.MediaSyncSourceItem(
                user_id=9,
                platform="pixiv",
                remote_id="123",
                media_index=0,
                absolute_path=str(path),
                downloaded_at=1,
            )
        )
        session.add(DeviceFile(device_id="device", absolute_path=str(path), modified_at_ms=1))
        session.commit()

    preview = sources.backfill_pixiv_publication_file_times(
        user_id=9,
        root_dir=str(tmp_path),
        dry_run=True,
    )
    assert preview["changed_count"] == 1
    assert path.stat().st_mtime == original_mtime

    result = sources.backfill_pixiv_publication_file_times(
        user_id=9,
        root_dir=str(tmp_path),
        dry_run=False,
    )
    assert result["changed_count"] == 1
    assert result["error_count"] == 0
    timestamp = parse_publication_timestamp(PIXIV_DATE)
    assert abs(path.stat().st_mtime - timestamp) <= 2
    with Session(engine) as session:
        item = session.get(models.MediaSyncSourceItem, 1)
        device_file = session.get(DeviceFile, 1)
        assert item.extra_json["create_date"] == PIXIV_DATE
        assert item.extra_json["file_time_source"] == "pixiv.createDate"
        assert device_file.modified_at_ms == int(timestamp * 1000)


def test_missing_publication_date_is_fetched_from_pixiv_detail(tmp_path, engine, monkeypatch) -> None:
    path = tmp_path / "pixiv" / "123.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"sample")
    monkeypatch.setattr(sources, "engine", engine)
    monkeypatch.setattr(sources, "load_cached_pixiv_artwork_create_dates", lambda _root: {})
    monkeypatch.setattr(sources, "open_browser", lambda **_kwargs: SimpleNamespace(new_tab=lambda _url: object()))
    monkeypatch.setattr(sources, "raise_if_browser_action_required", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sources, "create_pixiv_session", lambda _tab: object())
    monkeypatch.setattr(sources, "keep_one_domain_tab", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sources,
        "fetch_pixiv_illust_detail",
        lambda _session, **_kwargs: {"createDate": PIXIV_DATE},
    )
    with Session(engine) as session:
        session.add(
            models.MediaSyncSourceItem(
                user_id=10,
                platform="pixiv",
                remote_id="123",
                media_index=0,
                absolute_path=str(path),
                downloaded_at=1,
            )
        )
        session.commit()

    result = sources.fetch_missing_pixiv_publication_dates(
        user_id=10,
        root_dir=str(tmp_path),
        log=lambda _message: None,
    )

    assert result["requested_count"] == 1
    assert result["fetched_count"] == 1
    with Session(engine) as session:
        item = session.get(models.MediaSyncSourceItem, 1)
        assert item.extra_json["create_date"] == PIXIV_DATE
        assert item.extra_json["publication_date_source"] == "pixiv.detail_api"


def test_pinterest_detail_keeps_explicit_created_at() -> None:
    payload = {
        "initialReduxState": {
            "pins": {
                "456": {
                    "id": "456",
                    "title": "sample",
                    "description": "",
                    "created_at": PIXIV_DATE,
                    "images": {
                        "orig": {
                            "url": "https://i.pinimg.com/originals/sample.jpg",
                            "width": 100,
                            "height": 200,
                        }
                    },
                }
            }
        }
    }
    html = f'<script id="__PWS_INITIAL_PROPS__" type="application/json">{json.dumps(payload)}</script>'

    detail = sources.parse_pin_detail_html(html, expected_pin_id="456")

    assert detail["created_at"] == PIXIV_DATE


def test_pinterest_date_does_not_overwrite_reused_shared_media(tmp_path) -> None:
    path = tmp_path / "shared.jpg"
    path.write_bytes(b"shared")
    original_mtime = path.stat().st_mtime

    skipped = sources.align_pinterest_file_to_publication_date(
        path,
        created_at=PIXIV_DATE,
        reused_existing_media=True,
        pin_id="456",
        log=lambda _message: None,
    )
    assert skipped is False
    assert path.stat().st_mtime == original_mtime

    applied = sources.align_pinterest_file_to_publication_date(
        path,
        created_at=PIXIV_DATE,
        reused_existing_media=False,
        pin_id="456",
        log=lambda _message: None,
    )
    assert applied is True
    assert abs(path.stat().st_mtime - parse_publication_timestamp(PIXIV_DATE)) <= 2
