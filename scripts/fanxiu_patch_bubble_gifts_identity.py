from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

XLPROJECT_SRC = ROOT.parent / "xlproject" / "src"
if str(XLPROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(XLPROJECT_SRC))
import xlproject.loadenv  # noqa: F401,E402  # shared deployment environment contract

from backend.core.access.auth import create_access_token  # noqa: E402


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
SCENE_ID = 591
SCENE_FILENAME = "0591.png"
MENU_SCENE_ID = 590
MENU_SCENE_FILENAME = "0590.png"
WEB_VARIANT_SCENE_ID = 743
WEB_VARIANT_SCENE_FILENAME = "0743.png"
TITLE_SHAPE_ID = "shape-bubble-gifts-title"
DYNAMIC_SHAPE_ID = "shape-bubble-gifts-silver-id"
MENU_CLOSE_SHAPE_ID = "shape-bubble-menu-close-20260914"
WEB_VARIANT_IDENTITY_SHAPE_ID = "shape-37shouyou-gift-title-20260914"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260914\1789315624597_876e8ae4_场景导航的有界恢复已耗尽_目标场景__34_当前_点击前场景_unknown_动作_shape_.png"
)


def _walk(nodes: list[dict[str, Any]]):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(children)


def _scene(tree: list[dict[str, Any]], scene_id: int, filename: str) -> dict[str, Any]:
    matches = [
        node
        for node in _walk(tree)
        if node.get("type") == "image" and node.get("filename") == filename
    ]
    if len(matches) != 1:
        raise RuntimeError(f"期望唯一 #{scene_id}，实际 {len(matches)} 个")
    return matches[0]


def _shape(scene: dict[str, Any], shape_id: str) -> dict[str, Any]:
    matches = [
        shape
        for shape in _walk(scene.get("shapes") or [])
        if str(shape.get("id") or "") == shape_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"#{SCENE_ID} 期望唯一 Shape {shape_id}，实际 {len(matches)} 个")
    return matches[0]


def _upsert_shape(scene: dict[str, Any], desired: dict[str, Any]) -> bool:
    shapes = scene.get("shapes")
    if not isinstance(shapes, list):
        raise RuntimeError(f"场景 {scene.get('filename')} shapes 结构无效")
    existing = next(
        (
            shape
            for shape in shapes
            if isinstance(shape, dict) and str(shape.get("id") or "") == desired["id"]
        ),
        None,
    )
    if existing == desired:
        return False
    if existing is None:
        shapes.append(desired)
    else:
        existing.clear()
        existing.update(desired)
    return True


def _menu_close_shape() -> dict[str, Any]:
    return {
        "id": MENU_CLOSE_SHAPE_ID,
        "kind": "shape",
        "title": "关闭",
        "description": (
            "37 SDK 气泡菜单的橙色气泡开关。现有 #421[气泡] 模板在 #590 参考帧唯一命中 88%，"
            "resolved_box=(17,650,57,67)；点击后只关闭 SDK 覆盖层，再重新识别底层游戏场景。"
        ),
        "locked": False,
        "floating": True,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "sceneJumpTarget": "-1",
        "loadDirection": "none",
        "imageMatchRole": "required",
        "pixelTolerance": 20,
        "ocrMatchRole": "off",
        "ocrEnabled": False,
        "ocrText": "",
        "ocrMatchMode": "contains",
        "ocrMinConfidence": 0,
        "ocrMaskMode": "inherit-envelope",
        "ocrMask": None,
        "maskEnabled": False,
        "alphaMask": None,
        "toleranceEnabled": False,
        "toleranceRange": None,
        "discriminatorEnabled": False,
        "discriminator": None,
        "discriminatorGroupId": None,
        "discriminatorValue": "",
        "x": 17 / 900,
        "y": 650 / 1600,
        "w": 57 / 900,
        "h": 67 / 1600,
        "scanScales": [1.0, 1.25],
        "children": [],
    }


def _access_token(username: str) -> str:
    return create_access_token({"sub": username}, expires_delta=timedelta(minutes=10))


def _request(
    method: str,
    url: str,
    *,
    token: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=json_body,
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {url} -> {response.status_code}: {response.text[:1000]}")
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{method} {url} 返回无效：{payload!r}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 #591 礼包列表的动态内容身份过拟合")
    parser.add_argument("--username", default="code4101")
    parser.add_argument("--api-base", default=API_BASE)
    args = parser.parse_args()

    if not FAILURE_FRAME.is_file():
        raise FileNotFoundError(f"第一现场不存在：{FAILURE_FRAME}")

    token = _access_token(args.username)
    tree_url = f"{args.api_base.rstrip('/')}/data-annotation/asset-tree"
    snapshot = _request("GET", f"{tree_url}?entry_id={ENTRY_ID}", token=token)
    tree = snapshot.get("tree")
    if not isinstance(tree, list):
        raise RuntimeError("资产 API 未返回有效 tree")

    scene = _scene(tree, SCENE_ID, SCENE_FILENAME)
    menu = _scene(tree, MENU_SCENE_ID, MENU_SCENE_FILENAME)
    web_variant = _scene(tree, WEB_VARIANT_SCENE_ID, WEB_VARIANT_SCENE_FILENAME)
    title = _shape(scene, TITLE_SHAPE_ID)
    dynamic = _shape(scene, DYNAMIC_SHAPE_ID)
    web_variant_identity = _shape(web_variant, WEB_VARIANT_IDENTITY_SHAPE_ID)
    if not (
        title.get("ocrEnabled") is True
        and title.get("ocrMatchRole") == "required"
        and title.get("ocrText") == "礼包"
        and title.get("isSceneIdentity") is True
        and title.get("sceneIdentityRole") == "required"
    ):
        raise RuntimeError("#591 稳定‘礼包’标题身份已变化，拒绝部分覆盖")
    if dynamic.get("ocrText") != "白银":
        raise RuntimeError("#591 动态内容 Shape 已变化，拒绝部分覆盖")
    if not (
        web_variant_identity.get("ocrText") == "礼"
        and web_variant_identity.get("ocrMatchRole") == "required"
        and web_variant_identity.get("imageMatchRole") == "off"
    ):
        raise RuntimeError("#743 单字‘礼’身份契约已变化，拒绝部分覆盖")

    desired_dynamic = {
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "description": (
            "礼包等级/活动名是动态业务内容，不承担页面身份。2026-09-14 第一现场同一位置变为"
            "‘中秋礼包’，标准总览确认与 #591 全图相似 97.2%；页面身份仅由固定标题‘礼包’承担。"
        ),
    }
    desired_title_description = (
        "SDK 礼包列表固定页标题；避开动态礼包名称、等级、剩余次数和领取状态，独立承担 #591 身份。"
    )
    changed = False
    for key, value in desired_dynamic.items():
        if dynamic.get(key) != value:
            dynamic[key] = value
            changed = True
    if title.get("description") != desired_title_description:
        title["description"] = desired_title_description
        changed = True
    # #743 is the same SDK gift page at a different scroll/header state. Its
    # single-character OCR identity and #591's stable "礼包" identity match
    # each other's references at 100%, so keeping it in the popup group makes
    # the guard close the page while the weekly Job is intentionally entering
    # #591. Retain the frame as a Layer-3 reference and let #591 own both
    # visual variants and the safe Return action.
    desired_web_variant = {
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "description": (
            "#591 礼包列表的页头/滚动位置变体，仅保留为参考帧。2026-09-14 正式 match "
            "验证 #591→#743 与 #743→#591 均为 100%；单字‘礼’不得在弹窗分组抢认并自动返回。"
        ),
    }
    for key, value in desired_web_variant.items():
        if web_variant_identity.get(key) != value:
            web_variant_identity[key] = value
            changed = True
    interruption_fields = {
        "behaviorTreeInterruption": True,
        "behaviorTreeInterruptionAction": "返回",
    }
    for key, value in interruption_fields.items():
        if scene.get(key) != value:
            scene[key] = value
            changed = True
    menu_interruption_fields = {
        "behaviorTreeInterruption": True,
        "behaviorTreeInterruptionAction": "关闭",
    }
    for key, value in menu_interruption_fields.items():
        if menu.get(key) != value:
            menu[key] = value
            changed = True
    changed = _upsert_shape(menu, _menu_close_shape()) or changed

    revision = snapshot.get("revision")
    if changed:
        saved = _request(
            "PUT",
            tree_url,
            token=token,
            json_body={
                "entry_id": ENTRY_ID,
                "tree": tree,
                "base_revision": revision,
            },
        )
        revision = saved.get("revision")

    print(
        json.dumps(
            {
                "ok": True,
                "changed": changed,
                "scene_id": SCENE_ID,
                "dynamic_shape_id": DYNAMIC_SHAPE_ID,
                "dynamic_scene_identity": False,
                "stable_identity_shape_id": TITLE_SHAPE_ID,
                "menu_scene_id": MENU_SCENE_ID,
                "menu_close_shape_id": MENU_CLOSE_SHAPE_ID,
                "merged_reference_scene_id": WEB_VARIANT_SCENE_ID,
                "merged_reference_scene_identity": False,
                "explicit_interruption_chain": [591, 590],
                "revision": revision,
                "failure_frame": str(FAILURE_FRAME),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
