from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.fanxiu_resources import router as fanxiu_resources_router
from backend.core.fanxiu_resources import FANXIU_RESOURCE_EXPORT_ROOT_ENV
from backend.db import get_session


def _build_client(monkeypatch) -> TestClient:
    monkeypatch.setattr("backend.core.feature_access_guard.is_feature_access_allowed", lambda *args, **kwargs: True)
    app = FastAPI()

    def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    app.include_router(fanxiu_resources_router, prefix="/api/fanxiu")
    return TestClient(app)


def test_fanxiu_wwise_mp3_manifest_route_adds_media_urls(tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    output_dir = export_root / "parsed_configs" / "audio_catalog"
    mp3_path = output_dir / "mp3" / "Audio" / "GeneratedSoundBanks" / "Android" / "bgm_test" / "12345.mp3"
    output_dir.mkdir(parents=True)
    mp3_path.parent.mkdir(parents=True)
    mp3_path.write_bytes(b"ID3fake")
    (output_dir / "wwise_mp3_manifest.tsv").write_text(
        "source_bank\tkind\twem_id\tentry_index\twem_size\tsample_rate\tchannels\tduration_seconds\tencoding\tmp3_path\trelative_mp3_path\tstatus\terror\n"
        f"Audio/GeneratedSoundBanks/Android/bgm_test.bnk\tbgm\t12345\t0\t7\t32000\t1\t0.5\tCustom Vorbis\t{mp3_path}\tAudio/GeneratedSoundBanks/Android/bgm_test/12345.mp3\tconverted\t\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(export_root))
    client = _build_client(monkeypatch)

    response = client.get("/api/fanxiu/resources/wwise/mp3-manifest", params={"query": "12345", "kind": "bgm"})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    row = data["rows"][0]
    assert row["media_url"].startswith("/api/fanxiu/resources/audio/media?path=")
    assert row["player_url"].startswith("/api/fanxiu/resources/audio/player?path=")

    media_response = client.get(row["media_url"])
    assert media_response.status_code == 200
    assert media_response.headers["content-type"].startswith("audio/mpeg")
    assert media_response.content == b"ID3fake"


def test_fanxiu_wwise_mp3_manifest_route_reports_missing_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv(FANXIU_RESOURCE_EXPORT_ROOT_ENV, str(tmp_path / "exports"))
    client = _build_client(monkeypatch)

    response = client.get("/api/fanxiu/resources/wwise/mp3-manifest")

    assert response.status_code == 404
    assert "MP3 manifest 不存在" in response.json()["detail"]


def test_fanxiu_item_card_routes_do_not_rebuild_catalog_on_read(monkeypatch):
    client = _build_client(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def fake_search_fanxiu_item_cards(**kwargs):
        calls.append(("search", kwargs))
        return {"total": 0, "items": []}

    def fake_get_fanxiu_item_card(item_id, **kwargs):
        calls.append(("detail", {"item_id": item_id, **kwargs}))
        return {"card": {"id": item_id, "name": "测试道具"}}

    monkeypatch.setattr("backend.api.fanxiu_resources.search_fanxiu_item_cards", fake_search_fanxiu_item_cards)
    monkeypatch.setattr("backend.api.fanxiu_resources.get_fanxiu_item_card", fake_get_fanxiu_item_card)

    list_response = client.get("/api/fanxiu/resources/items/cards", params={"type_key": "46"})
    detail_response = client.get("/api/fanxiu/resources/items/card", params={"item_id": "30060000"})

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert calls[0][0] == "search"
    assert calls[0][1]["rebuild_missing"] is False
    assert calls[1][0] == "detail"
    assert calls[1][1]["rebuild_missing"] is False


def test_fanxiu_item_icon_quality_review_route_forwards_threshold(monkeypatch):
    client = _build_client(monkeypatch)
    calls: list[dict] = []

    def fake_load_item_icon_quality_review(**kwargs):
        calls.append(kwargs)
        return {
            "summary": {"threshold": kwargs["threshold"], "group_count": 1},
            "items": [{"field": "icon", "icon": "shared", "count": 50}],
            "total": 1,
        }

    monkeypatch.setattr("backend.api.fanxiu_resources.load_item_icon_quality_review", fake_load_item_icon_quality_review)

    response = client.get(
        "/api/fanxiu/resources/items/icon-quality-review",
        params={"threshold": 75, "rebuild_missing": "false"},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["threshold"] == 75
    assert calls == [{"threshold": 75, "rebuild_missing": False, "export_root": None}]
