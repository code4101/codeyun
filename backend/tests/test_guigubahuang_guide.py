from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.guigubahuang import load_guigubahuang_guide
from backend.standard.guigubahuang import register


ROOT = Path(__file__).resolve().parents[2]


def test_guigubahuang_guide_has_verified_wudao_and_xianci_catalogs() -> None:
    guide = load_guigubahuang_guide()

    assert guide["source"]["build_id"] == "21758240"
    assert len(guide["wudao"]["attributes"]) == 12
    assert len(guide["xian_ci"]["shrines"]) == 15

    fire = next(item for item in guide["wudao"]["attributes"] if item["name"] == "火")
    assert fire["modifier"]["dp_cost"] == -5
    assert "每秒回复300体力" in fire["levels"][1]
    assert any("道魂副属性" in rule for rule in guide["wudao"]["rules"])

    nili = next(item for item in guide["xian_ci"]["shrines"] if item["id"] == "nili")
    assert nili["immortals"] == ["梁渠", "双双"]
    assert [reward["name"] for reward in nili["rewards"]] == ["凝神", "余辉"]


def test_guigubahuang_guide_api_is_read_only_and_registered() -> None:
    app = FastAPI()
    register(app)
    client = TestClient(app)

    response = client.get("/api/guigubahuang/guide")

    assert response.status_code == 200
    assert response.json()["source"]["steam_app_id"] == 1468810
    assert client.post("/api/guigubahuang/guide").status_code == 405


def test_guigubahuang_guide_is_visible_in_navigation_registry() -> None:
    registry = json.loads(
        (ROOT / "frontend/src/features/access/permissionRegistry.json").read_text(encoding="utf-8")
    )
    nodes = {node["key"]: node for node in registry["nodes"]}

    assert nodes["guigubahuang"]["parent_key"] == "game-tools"
    assert nodes["guigubahuang.guide"]["route_paths"] == ["/guigubahuang/guide"]
    assert nodes["guigubahuang.guide"]["menu_paths"] == ["/guigubahuang/guide"]
