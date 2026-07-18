from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.volcano_princess import router
from backend.core.volcano_princess.catalog import _read_theater_catalog


def _write_catalog(root: Path) -> None:
    catalog_dir = root / "parsed_configs" / "theater_catalog"
    catalog_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "generated_at": "2026-07-18T00:00:00+00:00",
        "source": {
            "build_id": "17778916",
            "engine": "Unity 6000.0.26f1",
            "game_root": r"D:\SteamLibrary\steamapps\common\VolcanoPrincess",
            "data_sha256": "a" * 64,
        },
        "summary": {
            "drama_count": 1,
            "question_count": 2,
            "line_type_count": 2,
            "drama_category_count": 1,
            "image_count": 1,
        },
        "images": [
            {
                "id": "theater-list-background",
                "title": "剧院大厅",
                "description": "剧目选择界面的观众席背景",
                "width": 1222,
                "height": 657,
                "sprite_name": "workingUI_21",
                "sprite_path_id": 9125,
                "scene_path": "18 剧团演出/theatre/bg",
                "media_path": "media/images/theater/9125_theater-list-background.png",
                "media_sha256": "b" * 64,
            }
        ],
        "mechanics": {
            "rounds": 3,
            "options_per_round": 3,
            "correct_rule": "选择与题目要求情绪相同的台词",
            "performance_bgm_index": 22,
        },
        "line_types": [
            {"index": 0, "name": "愤怒", "game_color": "red"},
            {"index": 1, "name": "希望", "game_color": "yellow"},
        ],
        "drama_categories": ["英雄"],
        "nature_names": ["体魄"],
        "questions": [
            {"index": 0, "line_type_index": 0, "line_type": "愤怒", "line_index": 0, "content": "滚开！"},
            {"index": 1, "line_type_index": 1, "line_type": "希望", "line_index": 0, "content": "太阳正在升起。"},
        ],
        "dramas": [
            {
                "index": 0,
                "name": "光荣王妃",
                "description": "剧目描述",
                "role": "龙套",
                "theater_level": 0,
                "category_index": 0,
                "category": "英雄",
                "requirements": [{"nature_index": 0, "nature": "体魄", "value": 200}],
                "charm": 15,
                "base_salary": 120,
                "fame": 50,
            }
        ],
    }
    (catalog_dir / "catalog.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    image_dir = root / "media" / "images" / "theater"
    image_dir.mkdir(parents=True)
    (image_dir / "9125_theater-list-background.png").write_bytes(b"png-test")


def test_theater_catalog(tmp_path: Path, monkeypatch) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("VOLCANO_PRINCESS_REVERSE_ROOT", str(tmp_path))
    _read_theater_catalog.cache_clear()
    app = FastAPI()
    app.include_router(router, prefix="/api/volcano-princess")
    client = TestClient(app)

    response = client.get("/api/volcano-princess/theater")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["question_count"] == 2
    assert payload["questions"][0]["line_type"] == "愤怒"
    assert payload["dramas"][0]["name"] == "光荣王妃"
    assert payload["mechanics"]["performance_bgm_index"] == 22
    assert payload["images"][0]["media_url"].endswith("/theater-list-background")
    assert "game_root" not in payload["source"]

    image_response = client.get(payload["images"][0]["media_url"])
    assert image_response.status_code == 200
    assert image_response.content == b"png-test"
