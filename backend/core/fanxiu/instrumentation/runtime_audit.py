"""Offline Runtime inventory, without importing or contacting the game.

Run ``uv run python -m backend.core.fanxiu.instrumentation.runtime_audit`` to
print JSON for this source directory; optional file/directory arguments narrow
the inventory. Findings are review candidates, never automatic bug verdicts:
an explicit diagnostic may legitimately discover roots, and a small table may
legitimately require all fields. Cache names are clues, not proof of reuse.

Use file/function/line to inspect the current call chain, then prioritize using
live Runtime diagnostics. Static analysis cannot determine actual UI lifecycle,
cache invalidation correctness, or latency. Before declaring a reader accepted,
measure cold/hot/recovery runs and verify current values after UI reopen and
target changes. This CLI only parses source with the standard library; it does
not discover processes, read game memory, or change any game state.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Finding:
    file: str
    function: str
    line: int
    category: str
    expression: str


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class _Inventory(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.scopes: list[str] = []
        self.aliases: dict[str, str] = {}
        self.findings: list[Finding] = []

    def _add(self, node: ast.AST, category: str, expression: str) -> None:
        self.findings.append(Finding(
            self.file, ".".join(self.scopes) or "<module>",
            node.lineno, category, expression,
        ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = alias.name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def _visit_scope(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store) and (
            node.id.lower().endswith("_cache") or node.id.lower().startswith("_cached_")
        ):
            self._add(node, "cache_storage_hint", node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Store) and node.attr.lower().endswith("_cache"):
            self._add(node, "cache_storage_hint", _dotted_name(node))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        expression = _dotted_name(node.func)
        parts = expression.split(".")
        if parts:
            parts[0] = self.aliases.get(parts[0], parts[0])
        name = ".".join(parts)
        leaf = parts[-1]
        category = None
        if name.endswith("MumuProcessMemory.discover"):
            category = "process_discovery"
        elif name.endswith("MumuProcessMemory.discover_cached"):
            category = "process_cache_hint"
        elif leaf == "resolve_manager_root":
            policy = next((kw.value for kw in node.keywords if kw.arg == "allow_discovery"), None)
            if policy is None:
                category = "manager_discovery_policy_implicit"
            elif isinstance(policy, ast.Constant) and policy.value is False:
                category = "manager_discovery_disabled"
            else:
                category = "manager_discovery_policy_review"
        elif leaf in {"fields", "table"}:
            category = "full_table_read_candidate"
        elif leaf == "active_ui_component_objects":
            category = "active_component_traversal_candidate"
        elif leaf == "clear_ui_runtime_context_cache":
            category = "global_context_invalidation"
        elif leaf in {"lru_cache", "cache"}:
            category = "cache_decorator_hint"
        if category:
            self._add(node, category, expression)
        self.generic_visit(node)


def analyze_source(source: str, *, file: str = "<source>") -> list[Finding]:
    """Parse a source string; AST name matching is deliberately conservative.

    Import aliases are recognized syntactically. Dynamic dispatch, shadowing,
    indirect calls and **kwargs policies require human call-chain review.
    """
    visitor = _Inventory(file)
    visitor.visit(ast.parse(source, filename=file))
    return sorted(visitor.findings, key=lambda item: (item.line, item.category))


def audit_paths(paths: Iterable[Path]) -> dict:
    """Inventory unique Python files; report unreadable/invalid files explicitly."""
    files: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        files.update(path.rglob("*.py") if path.is_dir() else (path,))
    findings: list[Finding] = []
    errors = []
    for path in sorted(files):
        try:
            findings.extend(analyze_source(path.read_text(encoding="utf-8-sig"), file=str(path)))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"file": str(path), "error": str(exc)})
    return {
        "interpretation": "Static review candidates and cache clues; not confirmed defects or live acceptance.",
        "files_analyzed": len(files) - len(errors),
        "counts": dict(sorted(Counter(item.category for item in findings).items())),
        "findings": [asdict(item) for item in findings],
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Python files or source directories")
    args = parser.parse_args(argv)
    report = audit_paths(args.paths or (Path(__file__).parent,))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
