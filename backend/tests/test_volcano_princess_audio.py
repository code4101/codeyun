from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.volcano_princess import router
from backend.core.volcano_princess.catalog import _read_catalog


def _write_catalog(root: Path, *, unsafe_media_path: bool = False) -> None:
    catalog_dir = root / "parsed_configs" / "audio_catalog"
    media_dir = root / "media" / "audio"
    catalog_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)
    entries = [
        {
            "id": "resources.assets:4027",
            "path_id": 4027,
            "name": "0480",
            "category": "voice_or_effect",
            "duration_seconds": 7.889,
            "channels": 1,
            "frequency_hz": 44100,
            "source_asset": "VolcanoPrincess_Data/resources.assets",
            "media_path": "../outside.mp3" if unsafe_media_path else "media/audio/4027_0480.mp3",
            "media_bytes": 7,
            "media_sha256": "a" * 64,
        },
        {
            "id": "resources.assets:4073",
            "path_id": 4073,
            "name": "019 砂漠の熱風",
            "category": "music_or_ambience",
            "duration_seconds": 217.57,
            "channels": 2,
            "frequency_hz": 44100,
            "source_asset": "VolcanoPrincess_Data/resources.assets",
            "media_path": "media/audio/4073_music.mp3",
            "media_bytes": 8,
            "media_sha256": "b" * 64,
        },
        {
            "id": "resources.assets:4230",
            "path_id": 4230,
            "name": "071鼠标挪上按钮",
            "category": "short_clip",
            "duration_seconds": 0.072,
            "channels": 2,
            "frequency_hz": 48000,
            "source_asset": "VolcanoPrincess_Data/resources.assets",
            "media_path": "media/audio/4230_hover.mp3",
            "media_bytes": 9,
            "media_sha256": "c" * 64,
        },
    ]
    payload = {
        "schema_version": 1,
        "app_id": "volcano_princess",
        "app_name": "火山的女儿",
        "generated_at": "2026-07-17T00:00:00+00:00",
        "source": {
            "build_id": "17778916",
            "engine": "Unity 6000.0.26f1",
            "game_root": r"D:\SteamLibrary\steamapps\common\VolcanoPrincess",
            "asset_path": r"D:\SteamLibrary\steamapps\common\VolcanoPrincess\resources.assets",
        },
        "summary": {"entry_count": 3, "exported_count": 3, "failed_count": 0},
        "entries": entries,
    }
    (catalog_dir / "catalog.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if not unsafe_media_path:
        (media_dir / "4027_0480.mp3").write_bytes(b"ID3test")
        (media_dir / "4073_music.mp3").write_bytes(b"ID3music")
        (media_dir / "4230_hover.mp3").write_bytes(b"ID3hover!")


def _client(root: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("VOLCANO_PRINCESS_REVERSE_ROOT", str(root))
    _read_catalog.cache_clear()
    app = FastAPI()
    app.include_router(router, prefix="/api/volcano-princess")
    return TestClient(app)


def test_audio_catalog_meta_list_detail_and_media(tmp_path: Path, monkeypatch) -> None:
    _write_catalog(tmp_path)
    client = _client(tmp_path, monkeypatch)

    meta = client.get("/api/volcano-princess/audio/meta")
    assert meta.status_code == 200
    assert meta.json()["source"] == {"build_id": "17778916", "engine": "Unity 6000.0.26f1"}

    page = client.get(
        "/api/volcano-princess/audio",
        params={"category": "music_or_ambience", "sort_by": "duration", "sort_order": "desc"},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["path_id"] == 4073
    assert page.json()["items"][0]["media_url"] == "/api/volcano-princess/media/audio/4073"

    search = client.get("/api/volcano-princess/audio", params={"q": "鼠标", "page_size": 1})
    assert search.status_code == 200
    assert search.json()["items"][0]["path_id"] == 4230

    detail = client.get("/api/volcano-princess/audio/4027")
    assert detail.status_code == 200
    assert detail.json()["name"] == "0480"

    media = client.get("/api/volcano-princess/media/audio/4027")
    assert media.status_code == 200
    assert media.headers["content-type"] == "audio/mpeg"
    assert media.headers["etag"] == f'"{"a" * 64}"'
    assert media.content == b"ID3test"


def test_audio_media_cannot_escape_media_root(tmp_path: Path, monkeypatch) -> None:
    _write_catalog(tmp_path, unsafe_media_path=True)
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/volcano-princess/media/audio/4027")
    assert response.status_code == 404
    assert response.json()["detail"] == "音频文件不存在"

