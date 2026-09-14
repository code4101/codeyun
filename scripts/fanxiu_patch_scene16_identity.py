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
import xlproject.loadenv  # noqa: E402,F401  # shared deployment environment contract

from backend.core.access.auth import create_access_token  # noqa: E402


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
SCENE_ID = 16
SCENE_FILENAME = "0016.png"
LEGACY_IDENTITY_ID = "shape-1779979913955-6e113f66e98b78"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"


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


def _ocr_identity(
    *,
    shape_id: str,
    title: str,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
) -> dict[str, Any]:
    return {
        "id": shape_id,
        "kind": "shape",
        "title": title,
        "description": (
            "2026-09-14 以 #16 参考帧全量 OCR bbox 标注的登录账号面板稳定语义身份；"
            "替代旧‘收起账号’近空白图像 ROI，排除仙府过场纯白帧假阳性。"
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
        "ocrText": text,
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
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "children": [],
        "locked": False,
        "ocrMaskMode": "inherit-envelope",
        "ocrMask": None,
        "loadDirection": "none",
    }


IDENTITIES = (
    _ocr_identity(
        shape_id="shape-scene16-20260914-recover-account",
        title="找回账号/密码",
        text="找回账号",
        x=0.245,
        y=0.615,
        w=0.220,
        h=0.040,
    ),
    _ocr_identity(
        shape_id="shape-scene16-20260914-other-account-login",
        title="其他账号登录",
        text="其他账号登录",
        x=0.545,
        y=0.615,
        w=0.195,
        h=0.040,
    ),
)


def _validate(scene: dict[str, Any]) -> None:
    shapes = [shape for shape in scene.get("shapes") or [] if isinstance(shape, dict)]
    by_id = {str(shape.get("id") or ""): shape for shape in shapes}
    legacy = by_id.get(LEGACY_IDENTITY_ID)
    if legacy is None:
        raise RuntimeError("#16 缺少旧‘收起账号’ Shape")
    for key, expected in {
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "imageMatchRole": "off",
    }.items():
        if legacy.get(key) != expected:
            raise RuntimeError(f"#16 旧身份 {key} 未关闭：{legacy.get(key)!r}")
    for expected in IDENTITIES:
        actual = by_id.get(str(expected["id"]))
        if actual is None:
            raise RuntimeError(f"#16 缺少语义身份 {expected['title']}")
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"#16 语义身份 {expected['title']} 不一致：{mismatches}")
    required = [shape for shape in shapes if shape.get("isSceneIdentity") is True]
    if {shape.get("id") for shape in required} != {shape["id"] for shape in IDENTITIES}:
        raise RuntimeError(f"#16 存在预期外身份 Shape：{[shape.get('title') for shape in required]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 #16 近空白图像身份导致的纯白帧假阳性")
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
    legacy = next(
        (shape for shape in shapes if str(shape.get("id") or "") == LEGACY_IDENTITY_ID),
        None,
    )
    if legacy is None:
        raise RuntimeError("#16 缺少旧‘收起账号’身份 Shape")
    if legacy.get("isSceneIdentity") is True:
        if legacy.get("sceneIdentityRole") != "required" or legacy.get("imageMatchRole") != "required":
            raise RuntimeError("#16 旧身份契约已变化，拒绝部分覆盖")
    elif not all(
        legacy.get(key) == value
        for key, value in {
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "imageMatchRole": "off",
        }.items()
    ):
        raise RuntimeError("#16 旧身份处于未知中间状态，拒绝覆盖")

    changed = False
    for key, value in {
        "isSceneIdentity": False,
        "sceneIdentityRole": "off",
        "imageMatchRole": "off",
    }.items():
        if legacy.get(key) != value:
            legacy[key] = value
            changed = True

    by_id = {str(shape.get("id") or ""): shape for shape in shapes}
    for expected in IDENTITIES:
        actual = by_id.get(str(expected["id"]))
        if actual is None:
            shapes.append(dict(expected))
            changed = True
            continue
        for key, value in expected.items():
            if actual.get(key) != value:
                actual[key] = value
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
        "required_identities": [shape["title"] for shape in IDENTITIES],
        "revision": revision,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
