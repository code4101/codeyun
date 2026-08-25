from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_behavior_tree_runtime_uses_explicit_code_names() -> None:
    roots = [
        ROOT / "backend/core/fanxiu/behavior_tree",
        ROOT / "backend/core/fanxiu/data_annotation/behavior_tree_container.py",
        ROOT / "backend/core/fanxiu/data_annotation/behavior_tree_control.py",
        ROOT / "backend/core/fanxiu/data_annotation/behavior_tree_framework.py",
        ROOT / "backend/core/fanxiu/data_annotation/behavior_tree_runtime.py",
    ]
    forbidden = (
        "DataAnnotationRuntime",
        "FanxiuRuntime(",
        "data_annotation.runtime_runner",
        "data_annotation.runtime_control",
        "data_annotation.runtime_framework",
        "fanxiu.runtime.behavior_tree",
    )
    violations: list[str] = []
    for root in roots:
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
    assert not violations, "行为树自动化侧不得重新占用无前缀 Runtime 名称：\n" + "\n".join(violations)


def test_game_runtime_namespace_does_not_reexport_behavior_tree() -> None:
    package = (ROOT / "backend/core/fanxiu/runtime/__init__.py").read_text(encoding="utf-8")
    assert "Game-client Runtime" in package
    assert "from backend.core.fanxiu.behavior_tree" not in package


def test_behavior_tree_runtime_page_is_named_explicitly() -> None:
    page = (ROOT / "frontend/src/standard/fanxiu/data-annotation-runtime/page.vue").read_text(encoding="utf-8")
    permission_registry = (ROOT / "frontend/src/features/access/permissionRegistry.json").read_text(encoding="utf-8")
    assert "<h2>行为树 Runtime</h2>" in page
    assert '"title": "行为树 Runtime"' in permission_registry
