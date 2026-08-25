from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


ENTRY_ROOT = Path(
    r"D:\home\chenkunze\data\m2603codeyun\codepc_mf\fanxiu\data-annotation"
    r"\entries\30b82d72-8a76-4a74-be4b-4fc1591c6ce2"
)
TREE_PATH = ENTRY_ROOT / "asset-tree.json"
EVIDENCE_ROOT = Path(
    r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu-asset-backups"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    evidence_dir = EVIDENCE_ROOT / f"lilian-437-claim-action-{stamp}"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    before_sha = _sha256(TREE_PATH)
    shutil.copy2(TREE_PATH, evidence_dir / "asset-tree.before.json")

    tree = json.loads(TREE_PATH.read_text(encoding="utf-8"))
    frame = next(
        node
        for node in _walk(tree)
        if node.get("type") == "image" and node.get("filename") == "0437.png"
    )
    shapes = frame.setdefault("shapes", [])
    frame["title"] = "历练奖励"
    identity = next(
        shape
        for shape in shapes
        if shape.get("id") == "shape-1785472671511-abe1b25c67025"
    )
    before_node = json.loads(json.dumps(frame, ensure_ascii=False))

    identity["title"] = "领取身份"
    identity["isSceneIdentity"] = True
    identity["sceneIdentityRole"] = "required"
    identity["sceneJumpTarget"] = ""
    identity["ocrEnabled"] = True
    identity["ocrMatchRole"] = "required"
    identity["ocrText"] = "领取"
    action_id = "shape-lilian-0437-claim-action"
    action = next((shape for shape in shapes if shape.get("id") == action_id), None)
    if action is None:
        action = json.loads(json.dumps(identity, ensure_ascii=False))
        shapes.append(action)
    action.update(
        {
            "id": action_id,
            "title": "领取奖励",
            "description": "#437 明确的领取奖励动作；与身份 OCR 分离，供精确安全白名单和历练业务消费",
            "isSceneIdentity": False,
            "sceneIdentityRole": "off",
            "sceneJumpTarget": "438(43)",
            "ocrMatchRole": "off",
            "ocrEnabled": False,
            "ocrText": "",
            "verificationStatus": "visual-reviewed-action-pending-runtime",
            "source": "ocr+visual-model",
        }
    )

    reward_frame = next(
        node
        for node in _walk(tree)
        if node.get("type") == "image" and node.get("filename") == "0438.png"
    )
    close_action = next(
        shape
        for shape in reward_frame.get("shapes", [])
        if shape.get("title") == "关闭" and not shape.get("isSceneIdentity")
    )
    close_action["sceneJumpTarget"] = "425(44),437(1)"
    close_action["description"] = (
        "历练奖励弹层关闭；普通奖励结束到 #425，事件首层奖励可能先回 #437 领取结果奖励"
    )

    assert sum(shape.get("id") == action_id for shape in shapes) == 1
    assert sum(shape.get("title") == "领取奖励" for shape in shapes) == 1
    assert identity.get("isSceneIdentity") is True
    assert identity.get("ocrEnabled") is True
    assert identity.get("ocrText") == "领取"
    assert not identity.get("sceneJumpTarget")
    assert action.get("isSceneIdentity") is False
    assert action.get("ocrEnabled") is False
    assert action.get("sceneJumpTarget") == "438(43)"
    assert close_action.get("sceneJumpTarget") == "425(44),437(1)"

    TREE_PATH.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    after_sha = _sha256(TREE_PATH)
    manifest = {
        "tree_path": str(TREE_PATH),
        "reference_frame": str(ENTRY_ROOT / "images" / "0437.png"),
        "live_frame": str(
            Path(
                r"C:\Users\kzche\AppData\Local\Temp\codeyun\fanxiu-evidence"
            )
            / "scene437_20260825_1019.png"
        ),
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "before_node": before_node,
        "after_node": frame,
        "reward_close_node": close_action,
    }
    (evidence_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"evidence_dir": str(evidence_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
