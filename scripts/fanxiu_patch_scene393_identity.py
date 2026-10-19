from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

XLPROJECT_SRC = ROOT.parent / "xlproject" / "src"
if str(XLPROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(XLPROJECT_SRC))
import xlproject.loadenv  # noqa: E402,F401  # shared deployment environment contract

from backend.core.access.auth import create_local_owner_session  # noqa: E402


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
SCENE_ID = 393
SCENE_FILENAME = "0393.png"
AVATAR_TITLE = "分身"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"

# 2026-10-19 邮件_选择性领取事故：#393 唯一身份是 OCR contains「分身」，邮件列表行
# 「分身协助奖励」恰好落在同一框内，Guard 误判 #393 后点中 #122 附件打开 #587，
# 导致「领取」点击落空。身份改为两个场景专属按钮文本，二者均 required。
AVATAR_IDENTITY = {"ocrText": "分身进入", "ocrMatchMode": "contains",
                   "ocrMatchRole": "required", "isSceneIdentity": True}
REAL_BODY_IDENTITY = {
    "id": "shape-scene393-20261019-real-body-enter",
    "kind": "shape",
    "title": "真身进入",
    "description": (
        "2026-10-19 以 #393 参考帧 OCR bbox(558,1057)-(707,1099) 标注的社团大比专属按钮；"
        "与「分身进入」共同 required，排除邮件列表「分身协助奖励」等含“分身”文本的假阳性。"
    ),
    "floating": False,
    "jitterEnabled": False,
    "jitterRadius": 4,
    "isSceneIdentity": True,
    "sceneIdentityRole": "required",
    "sceneJumpTarget": "",
    "imageMatchRole": "off",
    "pixelTolerance": 5,
    "ocrMatchRole": "required",
    "ocrEnabled": True,
    "ocrText": "真身进入",
    "ocrMatchMode": "contains",
    "ocrMinConfidence": 0,
    "maskEnabled": False,
    "alphaMask": None,
    "toleranceEnabled": False,
    "toleranceRange": None,
    "discriminatorEnabled": False,
    "discriminator": None,
    "discriminatorGroupId": None,
    "discriminatorValue": "",
    "x": 550 / 900,
    "y": 1050 / 1600,
    "w": 165 / 900,
    "h": 57 / 1600,
    "children": [],
    "locked": False,
    "ocrMaskMode": "inherit-envelope",
    "ocrMask": None,
    "loadDirection": "none",
}


def _walk(nodes: list[dict[str, Any]]):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(children)


def _scene(tree: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        node
        for node in _walk(tree)
        if node.get("type") == "image" and node.get("filename") == SCENE_FILENAME
    ]
    if len(matches) != 1:
        raise RuntimeError(f"期望唯一 #{SCENE_ID}，实际 {len(matches)} 个")
    return matches[0]


def _access_token(username: str) -> str:
    return create_local_owner_session(username=username, expires_minutes=10)["access_token"]


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


def _avatar(shapes: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [shape for shape in shapes if shape.get("title") == AVATAR_TITLE]
    if len(matches) != 1:
        raise RuntimeError(f"#{SCENE_ID} 期望唯一「{AVATAR_TITLE}」Shape，实际 {len(matches)} 个")
    return matches[0]


def _validate(scene: dict[str, Any]) -> None:
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    avatar = _avatar(shapes)
    for key, value in AVATAR_IDENTITY.items():
        if avatar.get(key) != value:
            raise RuntimeError(f"#{SCENE_ID}「分身」{key} 不一致：{avatar.get(key)!r}")
    real = next((shape for shape in shapes if shape.get("id") == REAL_BODY_IDENTITY["id"]), None)
    if real is None:
        raise RuntimeError(f"#{SCENE_ID} 缺少「真身进入」身份")
    mismatches = {key: real.get(key) for key, value in REAL_BODY_IDENTITY.items() if real.get(key) != value}
    if mismatches:
        raise RuntimeError(f"#{SCENE_ID}「真身进入」不一致：{mismatches}")


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 #393 单一“分身”OCR 身份误判邮件列表")
    parser.add_argument("--username", default="code4101")
    parser.add_argument("--api-base", default=API_BASE)
    args = parser.parse_args()

    token = _access_token(args.username)
    tree_url = f"{args.api_base.rstrip('/')}/data-annotation/asset-tree"
    snapshot = _request("GET", f"{tree_url}?entry_id={ENTRY_ID}", token=token)
    tree = snapshot.get("tree")
    if not isinstance(tree, list):
        raise RuntimeError("资产 API 未返回有效 tree")

    scene = _scene(tree)
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    avatar = _avatar(shapes)
    if avatar.get("ocrText") not in {"分身", AVATAR_IDENTITY["ocrText"]}:
        raise RuntimeError(f"#{SCENE_ID}「分身」身份契约已变化，拒绝覆盖：{avatar.get('ocrText')!r}")

    changed = False
    for key, value in AVATAR_IDENTITY.items():
        if avatar.get(key) != value:
            avatar[key] = value
            changed = True
    real = next((shape for shape in shapes if shape.get("id") == REAL_BODY_IDENTITY["id"]), None)
    if real is None:
        shapes.append(dict(REAL_BODY_IDENTITY))
        changed = True
    else:
        for key, value in REAL_BODY_IDENTITY.items():
            if real.get(key) != value:
                real[key] = value
                changed = True
    scene["shapes"] = shapes
    _validate(scene)

    revision = snapshot.get("revision")
    if changed:
        saved = _request(
            "PUT",
            tree_url,
            token=token,
            json_body={"entry_id": ENTRY_ID, "tree": tree, "base_revision": revision},
        )
        saved_tree = saved.get("tree")
        if not isinstance(saved_tree, list):
            raise RuntimeError("资产 API 保存后未返回有效 tree")
        _validate(_scene(saved_tree))
        revision = saved.get("revision")

    print(json.dumps({
        "ok": True,
        "changed": changed,
        "scene_id": SCENE_ID,
        "required_identities": ["分身进入", "真身进入"],
        "revision": revision,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
