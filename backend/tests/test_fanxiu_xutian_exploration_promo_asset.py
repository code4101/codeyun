from __future__ import annotations

import json
import re
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "fanxiu"
    / "xutian_exploration_promo_0611.json"
)


def _scene() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _identity_matches(scene: dict, ocr_lines: list[str]) -> bool:
    text = "\n".join(ocr_lines)
    identities = [shape for shape in scene["shapes"] if shape.get("isSceneIdentity")]
    return bool(identities) and all(
        re.search(str(shape["ocrText"]), text) is not None for shape in identities
    )


def test_xutian_exploration_promo_uses_three_bounded_ocr_identities() -> None:
    scene = _scene()
    identities = [shape for shape in scene["shapes"] if shape.get("isSceneIdentity")]

    assert scene["filename"] == "0611.png"
    assert {shape["title"] for shape in identities} == {
        "虚天殿标题",
        "探索说明",
        "前往（高风险）",
    }
    assert _identity_matches(
        scene,
        ["虚天殿", "探索遗迹每日闯关危机挑战", "前 往"],
    )
    assert _identity_matches(
        scene,
        ["虚 天 殿", "探索遗迹·每日闯关·危机挑战", "前往"],
    )


def test_xutian_exploration_promo_rejects_adjacent_xutian_and_activity_replays() -> None:
    scene = _scene()
    negative_replays = [
        ["虚天殿已经关闭请等待下次开启", "地图特色", "虚天榜", "兑换宝阁"],
        ["虚天殿", "个人", "位面", "查看奖励", "虚天榜"],
        ["虚天殿", "位面", "查看奖励", "兑换宝阁"],
        ["丹道问鼎", "查看详情", "活动时间"],
        ["万宝臻宝", "探宝十次", "启宝", "任务", "商店"],
        ["缘定三生", "共度仙途", "立即参与"],
    ]

    assert all(not _identity_matches(scene, lines) for lines in negative_replays)


def test_xutian_exploration_forward_remains_unwired_high_risk_action() -> None:
    scene = _scene()
    forward = next(shape for shape in scene["shapes"] if shape["title"] == "前往（高风险）")

    assert forward["sceneJumpTarget"] == ""
    assert "不授权点击" in forward["description"]
    assert forward["w"] >= 0.35
