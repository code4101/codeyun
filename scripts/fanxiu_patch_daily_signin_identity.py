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
SCENE_ID = 404
SCENE_FILENAME = "0404.png"
IDENTITY_SHAPE_ID = "shape-1784773918855-f78ed5d643dd08"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_scene_diagnostics\scene_repair"
    r"\20260914\1789321736331_71e529ae_场景导航的有界恢复已耗尽_目标场景__34_当前_点击前场景_unknown_动作_shape_.png"
)
TITLE_IDENTITY_BOX = {
    "x": 0.05,
    "y": 0.285,
    "w": 0.49,
    "h": 0.055,
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


def _identity_shape(scene: dict[str, Any]) -> dict[str, Any]:
    matches = [
        shape
        for shape in _walk(scene.get("shapes") or [])
        if str(shape.get("id") or "") == IDENTITY_SHAPE_ID
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"#{SCENE_ID} 期望唯一身份 Shape {IDENTITY_SHAPE_ID}，实际 {len(matches)} 个"
        )
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
        raise RuntimeError(
            f"{method} {url} -> {response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{method} {url} 返回无效：{payload!r}")
    return payload


def _validate(scene: dict[str, Any]) -> None:
    identity = _identity_shape(scene)
    expected = {
        "isSceneIdentity": True,
        "sceneIdentityRole": "required",
        "imageMatchRole": "required",
        "ocrMatchRole": "off",
        "ocrEnabled": False,
        **TITLE_IDENTITY_BOX,
    }
    mismatches = {
        key: {"expected": value, "actual": identity.get(key)}
        for key, value in expected.items()
        if identity.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"#{SCENE_ID} 身份修订未生效：{mismatches}")
    if scene.get("behaviorTreeInterruption") is not True:
        raise RuntimeError(f"#{SCENE_ID} 未声明行为树中断")
    if scene.get("behaviorTreeInterruptionAction") != "返回":
        raise RuntimeError(f"#{SCENE_ID} 行为树中断动作不是[返回]")
    return_shape = next(
        (
            shape
            for shape in scene.get("shapes") or []
            if isinstance(shape, dict) and str(shape.get("title") or "") == "返回"
        ),
        None,
    )
    if return_shape is None:
        raise RuntimeError(f"#{SCENE_ID} 缺少[返回] Shape")


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 #404 签到页身份遮挡和遗留页面恢复")
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

    scene = _scene(tree)
    identity = _identity_shape(scene)
    if not (
        identity.get("isSceneIdentity") is True
        and identity.get("sceneIdentityRole") == "required"
        and identity.get("imageMatchRole") == "required"
        and identity.get("ocrMatchRole") == "off"
    ):
        raise RuntimeError("#404 原身份契约已变化，拒绝部分覆盖")

    desired_description = (
        "签到页固定艺术标题图像身份。2026-09-14 标准总览确认第一现场与 #404 "
        "全图相似 96.9%；旧‘累签奖励’身份区被悬浮助手遮挡后仅 71%，低于 80% "
        "场景门槛。新标题区在第一现场为 100%，#34/#297/#403/#643 负样本仅 1–6%。"
    )
    changed = False
    desired_identity = {
        **TITLE_IDENTITY_BOX,
        "title": "签到有礼",
        "description": desired_description,
        "pixelTolerance": 20,
    }
    for key, value in desired_identity.items():
        if identity.get(key) != value:
            identity[key] = value
            changed = True

    for key, value in {
        "behaviorTreeInterruption": True,
        "behaviorTreeInterruptionAction": "返回",
    }.items():
        if scene.get(key) != value:
            scene[key] = value
            changed = True

    _validate(scene)
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
        saved_tree = saved.get("tree")
        if not isinstance(saved_tree, list):
            raise RuntimeError("资产 API 保存后未返回有效 tree")
        _validate(_scene(saved_tree))
        revision = saved.get("revision")

    print(
        json.dumps(
            {
                "ok": True,
                "changed": changed,
                "scene_id": SCENE_ID,
                "identity_shape_id": IDENTITY_SHAPE_ID,
                "identity_box": TITLE_IDENTITY_BOX,
                "behavior_tree_interruption_action": "返回",
                "revision": revision,
                "failure_frame": str(FAILURE_FRAME),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
