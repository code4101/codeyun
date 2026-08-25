from __future__ import annotations

import json
import re
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "fanxiu"
    / "storage_direct_item_detail_0610.json"
)


def _scene() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _identity_matches(scene: dict, ocr_lines: list[str]) -> bool:
    text = "\n".join(ocr_lines)
    identities = [shape for shape in scene["shapes"] if shape.get("isSceneIdentity")]
    return bool(identities) and all(
        re.search(str(shape["ocrText"]), text) is not None for shape in identities
    )


def test_direct_item_detail_identity_is_generic_and_excludes_dynamic_values() -> None:
    scene = _scene()
    identities = [shape for shape in scene["shapes"] if shape.get("isSceneIdentity")]
    identity_text = " ".join(str(shape.get("ocrText") or "") for shape in identities)

    assert scene["filename"] == "0610.png"
    assert {shape["title"] for shape in identities} == {
        "效果说明",
        "获取途径",
        "使用（高风险）",
    }
    assert "57810" not in identity_text
    assert "灵石" not in identity_text
    assert _identity_matches(
        scene,
        [
            "任意物品名称",
            "境界要求：无限制",
            "描述",
            "任意动态描述",
            "效果说明",
            "任意动态效果",
            "获取途径",
            "使用",
        ],
    )


def test_direct_item_detail_rejects_real_random_and_fixed_box_ocr_replays() -> None:
    scene = _scene()

    random_box_583 = [
        "装备玄铁宝匣",
        "境界要求：无限制",
        "打开后可以随机获得以下道具",
        "打开",
    ]
    fixed_box_585 = [
        "灵石仙币宝匣",
        "境界要求：无限制",
        "打开可获得以下道具：",
        "仙币",
        "灵石",
        "打开",
    ]

    assert not _identity_matches(scene, random_box_583)
    assert not _identity_matches(scene, fixed_box_585)


def test_use_stays_high_risk_while_only_curtain_return_has_a_safe_edge() -> None:
    scene = _scene()
    shapes = {shape["title"]: shape for shape in scene["shapes"]}

    assert shapes["使用（高风险）"]["sceneJumpTarget"] == ""
    assert "不授权生产点击" in shapes["使用（高风险）"]["description"]
    assert shapes["右侧暗幕返回"]["sceneJumpTarget"] == "525"
    assert shapes["右侧暗幕返回"]["x"] >= 0.94
