import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PERMISSION_REGISTRY_PATH = (
    REPO_ROOT / "frontend" / "src" / "features" / "access" / "permissionRegistry.json"
)


def test_eastmoney_primary_menu_uses_privacy_safe_title() -> None:
    registry = json.loads(PERMISSION_REGISTRY_PATH.read_text(encoding="utf-8"))
    node = next(
        item
        for item in registry["nodes"]
        if item.get("key") == "notes.eastmoney"
    )

    assert node["title"] == "计算器"
    assert "东方财富" not in node["title"]
