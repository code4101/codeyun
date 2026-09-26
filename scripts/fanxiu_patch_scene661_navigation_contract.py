from __future__ import annotations

"""Remove copied world-HUD navigation actions from Fanxiu scene #661.

#661 is the in-world landmark/role overlay whose only trustworthy visible
control is ``进入``.  Older assets copied fixed-coordinate controls from the
world scene #34 onto it.  Keeping their jump history is useful evidence, but
``navigationRole=non_navigation`` prevents the generic planner from treating
those background coordinates as traversable graph edges.
"""

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

XLPROJECT_SRC = ROOT.parent / "xlproject" / "src"
if str(XLPROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(XLPROJECT_SRC))
import xlproject.loadenv  # noqa: F401,E402

from backend.core.access.auth import create_access_token  # noqa: E402


ENTRY_ID = "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
API_BASE = "http://127.0.0.1:8000/api/fanxiu"
INVALID_WORLD_HUD_TITLES = {"打开下方菜单", "日常", "聊天"}


def _walk(nodes: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
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
    parser = argparse.ArgumentParser(description="修复凡修 #661 错误继承世界 HUD 导航动作")
    parser.add_argument("--username", default="code4101")
    parser.add_argument("--api-base", default=API_BASE)
    args = parser.parse_args()

    token = create_access_token(
        {"sub": args.username},
        expires_delta=timedelta(minutes=10),
    )
    tree_url = f"{args.api_base.rstrip('/')}/data-annotation/asset-tree"
    snapshot = _request("GET", f"{tree_url}?entry_id={ENTRY_ID}", token=token)
    tree = snapshot.get("tree")
    if not isinstance(tree, list):
        raise RuntimeError("资产 API 未返回有效 tree")

    scene = _scene_by_number(tree, 661)
    scene_nodes = scene.get("shapes")
    if not isinstance(scene_nodes, list):
        raise RuntimeError("#661 缺少 shapes")
    by_title: dict[str, dict[str, Any]] = {}
    for title in sorted(INVALID_WORLD_HUD_TITLES):
        matches = [node for node in _walk(scene_nodes) if str(node.get("title") or "") == title]
        if len(matches) != 1:
            raise RuntimeError(f"#661 Shape「{title}」数量应为 1，实际 {len(matches)}")
        by_title[title] = matches[0]

    changed_titles: list[str] = []
    for title in sorted(INVALID_WORLD_HUD_TITLES):
        shape = by_title[title]
        # These copied controls have no local visual identity contract.  If a
        # future asset gains one, stop instead of silently disabling it.
        has_required_identity = bool(shape.get("isSceneIdentity")) or shape.get("sceneIdentityRole") == "required"
        if has_required_identity:
            raise RuntimeError(f"#661 Shape「{title}」已具备 required 身份契约，需要人工重新判定")
        if shape.get("navigationRole") != "non_navigation":
            shape["navigationRole"] = "non_navigation"
            changed_titles.append(title)

    revision = snapshot.get("revision")
    if changed_titles:
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
                "entry_id": ENTRY_ID,
                "scene_id": 661,
                "changed_titles": changed_titles,
                "revision": revision,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
