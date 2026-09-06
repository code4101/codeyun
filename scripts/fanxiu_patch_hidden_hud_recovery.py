from __future__ import annotations

import argparse
import base64
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
FAILURE_FRAME = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu_unknown\20260907"
    r"\1788733020679_go_scene_34.png"
)
HIDDEN_SCENE_TITLE = "界面已隐藏"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"


def _walk(nodes: list[dict[str, Any]]):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk(children)


def _scene_by_number(tree: list[dict[str, Any]], scene_id: int) -> dict[str, Any]:
    filename = f"{scene_id:04d}.png"
    for node in _walk(tree):
        if node.get("type") == "image" and node.get("filename") == filename:
            return node
    raise RuntimeError(f"资产树缺少场景 #{scene_id}")


def _scene_by_title(tree: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    matches = [
        node
        for node in _walk(tree)
        if node.get("type") == "image" and str(node.get("title") or "").strip() == title
    ]
    if len(matches) > 1:
        raise RuntimeError(f"资产树存在多个同名场景：{title}")
    return matches[0] if matches else None


def _shape(
    *,
    shape_id: str,
    title: str,
    description: str,
    x: float,
    y: float,
    w: float,
    h: float,
    scene_jump_target: str = "",
    identity: bool = False,
    image_match: bool = False,
    ocr_text: str = "",
    ocr_mode: str = "contains",
    pixel_tolerance: int = 5,
) -> dict[str, Any]:
    return {
        "id": shape_id,
        "kind": "shape",
        "title": title,
        "description": description,
        "locked": False,
        "floating": False,
        "jitterEnabled": False,
        "jitterRadius": 4,
        "isSceneIdentity": identity,
        "sceneIdentityRole": "required" if identity else "off",
        "sceneJumpTarget": scene_jump_target,
        "loadDirection": "none",
        "imageMatchRole": "required" if image_match else "off",
        "pixelTolerance": pixel_tolerance,
        "ocrMatchRole": "required" if ocr_text else "off",
        "ocrEnabled": bool(ocr_text),
        "ocrText": ocr_text,
        "ocrMatchMode": ocr_mode,
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
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "children": [],
    }


def _upsert_shape(scene: dict[str, Any], desired: dict[str, Any]) -> bool:
    shapes = scene.setdefault("shapes", [])
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


def _access_token(username: str) -> str:
    # The API still performs the normal active-user, feature-access and entry
    # ownership checks.  This only creates a short-lived local bearer token;
    # no password or long-lived credential is read or printed.
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
    parser = argparse.ArgumentParser(description="修复凡修离开确认穿透点击与隐藏 HUD 恢复资产")
    parser.add_argument("--username", default="code4101")
    parser.add_argument("--api-base", default=API_BASE)
    args = parser.parse_args()

    if not FAILURE_FRAME.is_file():
        raise FileNotFoundError(f"第一现场不存在：{FAILURE_FRAME}")

    token = _access_token(args.username)
    tree_url = f"{args.api_base.rstrip('/')}/data-annotation/asset-tree"
    save_frame_url = f"{args.api_base.rstrip('/')}/data-annotation/save-frame"
    snapshot = _request(
        "GET",
        f"{tree_url}?entry_id={ENTRY_ID}",
        token=token,
    )
    tree = snapshot.get("tree")
    if not isinstance(tree, list):
        raise RuntimeError("资产 API 未返回有效 tree")
    hidden = _scene_by_title(tree, HIDDEN_SCENE_TITLE)
    created = False
    if hidden is None:
        frame_data_url = "data:image/png;base64," + base64.b64encode(FAILURE_FRAME.read_bytes()).decode("ascii")
        saved = _request(
            "POST",
            save_frame_url,
            token=token,
            json_body={
                "entry_id": ENTRY_ID,
                "current_frame_data_url": frame_data_url,
                "title": HIDDEN_SCENE_TITLE,
                "same_level_as_scene_id": 86,
                "base_revision": snapshot.get("revision"),
            },
        )
        tree = saved.get("tree")
        if not isinstance(tree, list):
            raise RuntimeError("保存原始失败帧后资产 API 未返回有效 tree")
        revision = saved.get("revision")
        hidden = _scene_by_title(tree, HIDDEN_SCENE_TITLE)
        if hidden is None:
            raise RuntimeError("保存原始失败帧后未找到新场景")
        created = True
    else:
        revision = snapshot.get("revision")

    changed = False
    hidden_fields = {
        "behaviorTreeInterruption": False,
        "behaviorTreeInterruptionAction": "",
        "description": (
            "2026-09-07：离开确认弹窗使用父级空白时点击穿透到底层 HUD 隐藏控件。"
            "真实验收否定了右上发光球的恢复控件假设；仅留诊断帧，禁止自动点击。"
        ),
    }
    for key, value in hidden_fields.items():
        if hidden.get(key) != value:
            hidden[key] = value
            changed = True
    changed = _upsert_shape(
        hidden,
        _shape(
            shape_id="shape-hidden-hud-show-interface-20260907",
            title="显示界面",
            description="未证实为交互控件的特效，仅保留诊断区域。",
            x=0.865,
            y=0.170,
            w=0.120,
            h=0.060,
            scene_jump_target="",
            identity=False,
            image_match=True,
            pixel_tolerance=85,
        ),
    ) or changed

    cancel_description = (
        "未绑定当前离开动作时，只能点击确认框自身取消；禁止使用父级空白，"
        "其坐标会穿透触发底层 HUD 隐藏控件。"
    )
    for scene_id in (86, 289):
        changed = _upsert_shape(
            _scene_by_number(tree, scene_id),
            _shape(
                shape_id=f"shape-leave-confirm-{scene_id}-cancel-20260907",
                title="取消",
                description=cancel_description,
                x=0.198,
                y=0.638,
                w=0.282,
                h=0.058,
                scene_jump_target="-1",
                ocr_text="取消",
            ),
        ) or changed

    changed = _upsert_shape(
        _scene_by_number(tree, 186),
        _shape(
            shape_id="shape-scene-186-team-controls-20260907",
            title="队伍入口",
            description=(
                "#85/#186 都曾仅用‘离开’作身份，造成同一 #85 原帧在动画帧间翻转为 #186，"
                "并过早消费离开确认授权。#186 必须额外出现创建队伍/加入队伍专属面板。"
            ),
            x=0.555,
            y=0.075,
            w=0.405,
            h=0.225,
            identity=True,
            ocr_text="创建队伍.*加入队伍",
            ocr_mode="regex",
        ),
    ) or changed

    if changed or created:
        saved_tree = _request(
            "PUT",
            tree_url,
            token=token,
            json_body={
                "entry_id": ENTRY_ID,
                "tree": tree,
                "base_revision": revision,
            },
        )
        revision = saved_tree.get("revision")

    filename = str(hidden.get("filename") or "")
    print(
        json.dumps(
            {
                "ok": True,
                "created": created,
                "changed": changed,
                "hidden_scene_id": int(filename.removesuffix(".png")),
                "hidden_scene_filename": filename,
                "revision": revision,
                "failure_frame": str(FAILURE_FRAME),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
